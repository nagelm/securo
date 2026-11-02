"""Principal repayment never reaches P&L; only interest does.

One payment of 2,000 = 1,200 interest + 800 principal moves every income and
expense figure by exactly 1,200 of expense, all of it in the interest
category, and moves the loan by exactly the 800 of principal. Each test here
fails if the transfer pair, the P&L filters or the guards around them are
removed. Every amount is made up.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Awaitable, Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import mcp_server.tools  # noqa: F401  (registers the MCP tools)
from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.account import AccountCreate
from app.services import transaction_service
from app.services._query_filters import counts_as_user_pnl
from app.services.account_service import create_account, get_account_summary
from app.services.budget_service import get_budget_vs_actual
from app.services.category_service import create_default_categories, find_default_category
from app.services.dashboard_service import (
    _counts_as_user_pnl_row,
    get_spending_by_category,
    get_summary,
)
from app.services.loan_repayment_category import LOAN_REPAYMENTS_KEY
from app.services.report_service import get_cash_flow_report, get_income_expenses_report
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY
from tests.test_fork_loan_accounts import _manual_loan, _tx
from tests.test_loan_pnl_guard import sqlite_to_char  # noqa: F401  (fixture)

TOTAL = Decimal("2000.00")
INTEREST = Decimal("1200.00")
PRINCIPAL = TOTAL - INTEREST


# ---------------------------------------------------------------------------
# fixture: checking +10,000, manual loan -300,000, "Loan interest" category
# ---------------------------------------------------------------------------


class Book:
    def __init__(self, user: User, workspace: Workspace, checking: Account, loan: Account, interest: Category):
        self.user = user
        self.workspace = workspace
        self.checking = checking
        self.loan = loan
        self.interest = interest


def _before_this_month():
    return app_today().replace(day=1) - timedelta(days=40)


async def _category(session: AsyncSession, workspace: Workspace, user: User, name: str, **flags) -> Category:
    cat = Category(
        id=uuid.uuid4(), user_id=user.id, workspace_id=workspace.id, name=name,
        icon="tag", color="#000000", **flags,
    )
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


async def _checking(session: AsyncSession, workspace: Workspace, user: User, *, currency: str = "BRL", name="Everyday",
                    acc_type: str = "checking") -> Account:
    return await create_account(
        session, workspace.id, user.id,
        AccountCreate(
            name=name, type=acc_type, balance=Decimal("10000"), currency=currency,
            # Opened before this month so its opening row is outside every
            # window under test.
            balance_date=_before_this_month(),
        ),
    )


@pytest_asyncio.fixture
async def book(session: AsyncSession, test_user: User, test_workspace: Workspace) -> Book:
    checking = await _checking(session, test_workspace, test_user)
    loan = await _manual_loan(session, test_workspace, test_user, "300000")
    interest = await _category(session, test_workspace, test_user, "Loan interest")
    return Book(test_user, test_workspace, checking, loan, interest)


async def _fresh(session: AsyncSession, model, row_id):
    """Read a row from the database, not the session's identity map."""
    return (
        await session.execute(
            select(model).where(model.id == row_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# every P&L entry point: (income, expenses, expenses in the interest category)
# ---------------------------------------------------------------------------

Probe = Callable[[AsyncSession, Book], Awaitable[tuple[float, float, float]]]


def _month():
    start = app_today().replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return start, end


async def _dashboard_monthly(session, book):
    summary = await get_summary(session, book.workspace.id, book.user.id)
    spending = await get_spending_by_category(session, book.workspace.id, book.user.id)
    cat = sum(s.total for s in spending if s.category_id == str(book.interest.id))
    return summary.monthly_income, summary.monthly_expenses, cat


async def _dashboard_projected(session, book):
    summary = await get_summary(session, book.workspace.id, book.user.id)
    spending = await get_spending_by_category(session, book.workspace.id, book.user.id)
    cat = sum(s.projected_total for s in spending if s.category_id == str(book.interest.id))
    return summary.projected_income, summary.projected_expenses, cat


def _from_report(report, interest_key: str) -> tuple[float, float, float]:
    totals = {b.key: b.value for b in report.summary.breakdowns}
    cat = sum(
        c.value for c in report.composition if c.key == interest_key and c.group == "expenses"
    )
    return totals["income"], totals["expenses"], cat


async def _report_income_expenses(session, book):
    report = await get_income_expenses_report(
        session, book.workspace.id, book.user.id, months=1, interval="monthly", currency="BRL"
    )
    return _from_report(report, str(book.interest.id))


async def _report_category_trend(session, book):
    report = await get_income_expenses_report(
        session, book.workspace.id, book.user.id, months=1, interval="monthly", currency="BRL"
    )
    income = sum(c.total for c in report.category_trend if c.group == "income")
    expenses = sum(c.total for c in report.category_trend if c.group == "expenses")
    cat = sum(
        c.total for c in report.category_trend
        if c.key == str(book.interest.id) and c.group == "expenses"
    )
    return income, expenses, cat


async def _report_cash_flow(session, book):
    report = await get_cash_flow_report(
        session, book.workspace.id, book.user.id, months=1, interval="daily", currency="BRL"
    )
    today = app_today().isoformat()
    point = next(p for p in report.trend if p.date == today)
    # The cash flow chart has no category split for actuals; the interest
    # share is checked by every other probe.
    return point.breakdowns["inflow"], point.breakdowns["outflow"], point.breakdowns["outflow"]


async def _budget_actual(session, book):
    rows = await get_budget_vs_actual(session, book.workspace.id, book.user.id)
    cat = sum(float(r.actual_amount) for r in rows if r.category_id == book.interest.id)
    return 0.0, sum(float(r.actual_amount) for r in rows), cat


async def _budget_projected(session, book):
    rows = await get_budget_vs_actual(session, book.workspace.id, book.user.id)
    cat = sum(float(r.projected_amount) for r in rows if r.category_id == book.interest.id)
    return 0.0, sum(float(r.projected_amount) for r in rows), cat


async def _transactions_summary(session, book):
    start, end = _month()
    _, _, summary = await transaction_service.get_transactions(
        session, book.workspace.id, book.user.id, from_date=start, to_date=end,
        include_summary=True, skip_pagination=True,
    )
    assert summary is not None
    _, _, cat_summary = await transaction_service.get_transactions(
        session, book.workspace.id, book.user.id, from_date=start, to_date=end,
        category_id=book.interest.id, include_summary=True, skip_pagination=True,
    )
    assert cat_summary is not None
    return float(summary["income"]), float(summary["expense"]), float(cat_summary["expense"])


async def _account_stats(session, book):
    income = expenses = 0.0
    for account in (book.checking, book.loan):
        stats = await get_account_summary(session, account.id, book.workspace.id)
        assert stats is not None
        income += stats["monthly_income"]
        expenses += stats["monthly_expenses"]
    loan_stats = await get_account_summary(session, book.loan.id, book.workspace.id)
    assert loan_stats is not None
    return income, expenses, loan_stats["monthly_expenses"]


def _mcp_aggregate(exclude_transfers: bool) -> Probe:
    async def probe(session, book):
        start, end = _month()
        handler = REGISTRY["aggregate"].handler
        ctx = CallContext(user_id=book.user.id, workspace_id=book.workspace.id)
        common = dict(
            metric="sum", group_by="category", from_date=start.isoformat(),
            to_date=end.isoformat(), exclude_transfers=exclude_transfers,
        )
        expense = await handler(session=session, ctx=ctx, tx_type="expense", **common)
        income = await handler(session=session, ctx=ctx, tx_type="income", **common)
        cat = sum(i["value"] for i in expense["items"] if i["bucket"] == str(book.interest.id))
        return (
            sum(i["value"] for i in income["items"]),
            sum(i["value"] for i in expense["items"]),
            cat,
        )

    return probe


async def _mcp_income_expenses(session, book):
    handler = REGISTRY["get_income_expenses"].handler
    ctx = CallContext(user_id=book.user.id, workspace_id=book.workspace.id)
    report = await handler(session=session, ctx=ctx, months=1, interval="monthly")
    totals = {b["key"]: b["value"] for b in report["summary"]["breakdowns"]}
    cat = sum(
        c["value"] for c in report["composition"]
        if c["key"] == str(book.interest.id) and c["group"] == "expenses"
    )
    return totals["income"], totals["expenses"], cat


PROBES: dict[str, Probe] = {
    "dashboard_monthly": _dashboard_monthly,
    "dashboard_projected": _dashboard_projected,
    "report_income_expenses": _report_income_expenses,
    "report_category_trend": _report_category_trend,
    # Fork (owner 2026-10-14): cash flow is cash, not P&L. With loans left out
    # of it, the whole repayment leaves cash; see the test below.
    "budget_actual": _budget_actual,
    "budget_projected": _budget_projected,
    "transactions_summary": _transactions_summary,
    "account_stats": _account_stats,
    "mcp_aggregate_excluding_transfers": _mcp_aggregate(True),
    "mcp_aggregate_including_transfers": _mcp_aggregate(False),
    "mcp_get_income_expenses": _mcp_income_expenses,
}


# ---------------------------------------------------------------------------
# the invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unpaired_loan_credit_never_income(session, book, sqlite_to_char):  # noqa: F811
    """A repayment credit whose cash leg was never paired is not income:
    not in the SQL filter, not in the forecast twin, posted or pending."""
    await _tx(session, book.user, book.loan, "2000", "credit", source="sync")
    pending = await _tx(session, book.user, book.loan, "700", "credit", status="pending")

    summary = await get_summary(session, book.workspace.id, book.user.id)
    assert summary.monthly_income == pytest.approx(0.0)
    assert summary.projected_income == pytest.approx(0.0)
    income, _, _ = await _report_income_expenses(session, book)
    assert income == pytest.approx(0.0)

    loaded = (
        await session.execute(
            select(Transaction).where(Transaction.id == pending.id).options(selectinload(Transaction.account))
        )
    ).scalar_one()
    assert _counts_as_user_pnl_row(loaded) is False


# ---------------------------------------------------------------------------
# guards: unlink, categories, synced loans, paying accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_still_works_for_plain_transfers(client: AsyncClient, auth_headers, session, book):
    pair = uuid.uuid4()
    other = await _checking(session, book.workspace, book.user, name="Savings", acc_type="savings")
    await _tx(session, book.user, book.checking, "50", "debit", transfer_pair_id=pair)
    await _tx(session, book.user, other, "50", "credit", transfer_pair_id=pair)

    response = await client.delete(f"/api/connections/transfers/{pair}", headers=auth_headers)

    assert response.status_code == 204, response.text


# ---------------------------------------------------------------------------
# pairing an existing debit, deleting, atomicity
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# the Loan repayments category: principal in it never reaches P&L, paired or not
# ---------------------------------------------------------------------------


class SeededBook(Book):
    def __init__(self, book: Book, repayments: Category, transfers: Category):
        super().__init__(book.user, book.workspace, book.checking, book.loan, book.interest)
        self.repayments = repayments
        self.transfers = transfers

    def measuring(self, category: Category) -> Book:
        """The same book with the probes' category bucket pointed at `category`."""
        return Book(self.user, self.workspace, self.checking, self.loan, category)


@pytest_asyncio.fixture
async def seeded(session: AsyncSession, test_user: User, test_workspace: Workspace) -> SeededBook:
    """A workspace seeded with the default categories the way sign-up seeds
    them, so Loan repayments carries the template's flags."""
    await create_default_categories(session, test_user.id, "en", workspace_id=test_workspace.id)
    checking = await _checking(session, test_workspace, test_user)
    loan = await _manual_loan(session, test_workspace, test_user, "300000")
    interest = await _category(session, test_workspace, test_user, "Loan interest")
    repayments_id = await find_default_category(session, test_workspace.id, LOAN_REPAYMENTS_KEY)
    transfers_id = await find_default_category(session, test_workspace.id, "transfers")
    assert repayments_id is not None and transfers_id is not None
    repayments = await session.get(Category, repayments_id)
    transfers = await session.get(Category, transfers_id)
    assert repayments is not None and transfers is not None
    return SeededBook(Book(test_user, test_workspace, checking, loan, interest), repayments, transfers)


async def _mcp_by_payee(session, book):
    start, end = _month()
    handler = REGISTRY["aggregate"].handler
    ctx = CallContext(user_id=book.user.id, workspace_id=book.workspace.id)
    common = dict(metric="sum", group_by="payee", from_date=start.isoformat(), to_date=end.isoformat())
    expense = await handler(session=session, ctx=ctx, tx_type="expense", **common)
    income = await handler(session=session, ctx=ctx, tx_type="income", **common)
    with_payee = sum(i["value"] for i in expense["items"] + income["items"] if i["bucket"] is not None)
    return (
        sum(i["value"] for i in income["items"]),
        sum(i["value"] for i in expense["items"]),
        with_payee,
    )


REPAYMENT_PROBES: dict[str, Probe] = {**PROBES, "mcp_aggregate_by_payee": _mcp_by_payee}


@pytest.mark.asyncio
async def test_seeded_loan_repayments_is_a_transfer_category_in_housing(session: AsyncSession, seeded):
    assert seeded.repayments.name == "Loan repayments"
    assert seeded.repayments.treat_as_transfer is True
    assert seeded.repayments.is_ignored is False
    assert seeded.repayments.group_id is not None
    group = await session.get(CategoryGroup, seeded.repayments.group_id)
    assert group is not None
    assert group.name == "Housing"


UNPAIRED_LEGS = {
    "cash_debit": ("checking", "debit"),
    "cash_credit": ("checking", "credit"),
    "loan_credit": ("loan", "credit"),
    "loan_debit": ("loan", "debit"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("leg", list(UNPAIRED_LEGS), ids=list(UNPAIRED_LEGS))
@pytest.mark.parametrize("probe", list(REPAYMENT_PROBES), ids=list(REPAYMENT_PROBES))
async def test_unpaired_row_in_loan_repayments_never_reaches_pnl(session, seeded, sqlite_to_char, probe, leg):  # noqa: F811
    """The flag alone keeps principal out: a leg that lost (or never had)
    its pair, on either account and in either direction, moves nothing."""
    measure = REPAYMENT_PROBES[probe]
    view = seeded.measuring(seeded.repayments)
    before = await measure(session, view)
    account_attr, tx_type = UNPAIRED_LEGS[leg]
    lender = Payee(id=uuid.uuid4(), user_id=seeded.user.id, workspace_id=seeded.workspace.id, name="Example Lender")
    session.add(lender)
    await session.commit()
    row = await _tx(
        session, seeded.user, getattr(seeded, account_attr), "777", tx_type, source="sync",
        category_id=seeded.repayments.id,
    )
    row.payee_id = lender.id
    await session.commit()
    assert row.transfer_pair_id is None

    after = await measure(session, view)
    assert [a - b for a, b in zip(after, before)] == pytest.approx([0.0, 0.0, 0.0])


@pytest.mark.asyncio
async def test_unpaired_loan_repayments_rows_agree_with_the_python_twin(session, seeded):
    for attr, tx_type in UNPAIRED_LEGS.values():
        await _tx(session, seeded.user, getattr(seeded, attr), "777", tx_type, category_id=seeded.repayments.id)

    scoped = Transaction.workspace_id == seeded.workspace.id
    sql_ids = set((await session.execute(select(Transaction.id).where(scoped, counts_as_user_pnl()))).scalars())
    rows = (
        await session.execute(
            select(Transaction).where(scoped, Transaction.category_id == seeded.repayments.id).options(
                selectinload(Transaction.account), selectinload(Transaction.category)
            )
        )
    ).scalars().all()
    assert len(rows) == 4
    assert not {tx.id for tx in rows} & sql_ids
    assert not [tx for tx in rows if _counts_as_user_pnl_row(tx)]


# ---------------------------------------------------------------------------
# reports never read loan_payments
# ---------------------------------------------------------------------------

_BACKEND = Path(__file__).resolve().parent.parent
_AGGREGATION_MODULES = [
    _BACKEND / "app" / "services" / "_query_filters.py",
    _BACKEND / "app" / "services" / "report_service.py",
    _BACKEND / "app" / "services" / "dashboard_service.py",
    _BACKEND / "app" / "services" / "budget_service.py",
    *sorted((_BACKEND / "mcp_server" / "tools").glob("*.py")),
]
_FORBIDDEN = ("LoanPayment", "loan_payment", "loan_payments", "loan_details")


