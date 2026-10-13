"""Principal repayment never reaches P&L; only interest does.

One payment of 2,000 = 1,200 interest + 800 principal moves every income and
expense figure by exactly 1,200 of expense, all of it in the interest
category, and moves the loan by exactly the 800 of principal. Each test here
fails if the transfer pair, the P&L filters or the guards around them are
removed. Every amount is made up.
"""

import ast
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Awaitable, Callable

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

import mcp_server.tools  # noqa: F401  (registers the MCP tools)
from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.loan_payment import LoanPayment
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.account import AccountCreate
from app.schemas.loan_payment import LoanPaymentCreate
from app.services import account_service, loan_payment_service, transaction_service
from app.services._query_filters import counts_as_user_pnl
from app.services.account_service import create_account, get_account_summary
from app.services.budget_service import get_budget_vs_actual
from app.services.category_service import create_default_categories, find_default_category
from app.services.dashboard_service import (
    _counts_as_user_pnl_row,
    get_spending_by_category,
    get_summary,
)
from app.services.loan_payment_service import LoanPaymentConflict, record_payment
from app.services.loan_repayment_category import LOAN_REPAYMENTS_KEY
from app.services.report_service import get_cash_flow_report, get_income_expenses_report
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY
from tests.test_loan_accounts import _balance, _connection, _manual_loan, _tx
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


async def _pay(session: AsyncSession, book: Book, total=TOTAL, interest=INTEREST, **overrides) -> LoanPayment:
    payload = dict(
        loan_account_id=book.loan.id,
        cash_account_id=book.checking.id,
        date=app_today(),
        total_amount=total,
        interest_amount=interest,
        interest_category_id=book.interest.id if interest else None,
    )
    payload.update(overrides)
    return await record_payment(session, book.workspace.id, book.user.id, LoanPaymentCreate.model_validate(payload))


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
    "report_cash_flow": _report_cash_flow,
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
async def test_payment_moves_loan_by_principal_and_cash_by_total(session, book):
    await _pay(session, book)

    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-299200.0)
    assert await _balance(session, book.workspace, book.checking.id) == pytest.approx(8000.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", list(PROBES), ids=list(PROBES))
async def test_only_interest_reaches_every_pnl_entry_point(session, book, sqlite_to_char, probe):  # noqa: F811
    measure = PROBES[probe]
    income_before, expenses_before, cat_before = await measure(session, book)

    await _pay(session, book)

    income_after, expenses_after, cat_after = await measure(session, book)
    assert income_after - income_before == pytest.approx(0.0)
    assert expenses_after - expenses_before == pytest.approx(float(INTEREST))
    assert cat_after - cat_before == pytest.approx(float(INTEREST))


@pytest.mark.asyncio
async def test_payment_rows_have_the_designed_shape(session, book):
    payment = await _pay(session, book)

    cash = await _fresh(session, Transaction, payment.cash_transaction_id)
    leg = await _fresh(session, Transaction, payment.loan_transaction_id)
    interest = await _fresh(session, Transaction, payment.interest_transaction_id)
    assert cash is not None and leg is not None and interest is not None
    assert (cash.account_id, cash.type, cash.amount) == (book.checking.id, "debit", TOTAL)
    assert (leg.account_id, leg.type, leg.amount) == (book.loan.id, "credit", TOTAL)
    assert (interest.account_id, interest.type, interest.amount) == (book.loan.id, "debit", INTEREST)
    assert cash.transfer_pair_id is not None and cash.transfer_pair_id == leg.transfer_pair_id
    assert interest.transfer_pair_id is None and interest.category_id == book.interest.id
    # The payee page sums raw debits per payee: the legs carry none.
    assert cash.payee_id is None and leg.payee_id is None
    assert cash.category_id is None and leg.category_id is None
    assert payment.cash_leg_created is True


@pytest.mark.asyncio
async def test_interest_only_and_principal_only_payments(session, book):
    # Interest only: the whole payment is interest, still a pair plus a row.
    interest_only = await _pay(session, book, total=Decimal("500"), interest=Decimal("500"))
    assert interest_only.interest_transaction_id is not None
    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-300000.0)

    # Principal only: no interest row at all.
    principal_only = await _pay(session, book, total=Decimal("800"), interest=Decimal("0"))
    assert principal_only.interest_transaction_id is None
    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-299200.0)
    assert await _balance(session, book.workspace, book.checking.id) == pytest.approx(8700.0)

    summary = await get_summary(session, book.workspace.id, book.user.id)
    assert summary.monthly_expenses == pytest.approx(500.0)
    assert summary.monthly_income == pytest.approx(0.0)

    rows = await loan_payment_service.list_payments(session, book.workspace.id, loan_account_id=book.loan.id)
    by_id = {r.id: r for r in rows}
    assert (by_id[interest_only.id].interest, by_id[interest_only.id].principal) == (Decimal("500.00"), Decimal("0.00"))
    assert (by_id[principal_only.id].interest, by_id[principal_only.id].principal) == (Decimal("0"), Decimal("800.00"))


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


@pytest.mark.asyncio
async def test_python_twin_agrees_with_sql_filter(session, book):
    await _pay(session, book)
    await _tx(session, book.user, book.loan, "300", "credit")  # unpaired repayment
    await _tx(session, book.user, book.loan, "40", "debit")  # uncategorised fee

    scoped = Transaction.workspace_id == book.workspace.id
    sql_ids = set((await session.execute(select(Transaction.id).where(scoped, counts_as_user_pnl()))).scalars())
    rows = (
        await session.execute(
            select(Transaction).where(scoped).options(
                selectinload(Transaction.account), selectinload(Transaction.category)
            )
        )
    ).scalars().all()
    twin_ids = {tx.id for tx in rows if _counts_as_user_pnl_row(tx)}

    assert sql_ids == twin_ids
    loan_rows = [tx for tx in rows if tx.account_id == book.loan.id and tx.source != "opening_balance"]
    assert {tx.id for tx in loan_rows if tx.type == "debit"} <= twin_ids
    assert not {tx.id for tx in loan_rows if tx.type == "credit"} & twin_ids
    assert not {tx.id for tx in rows if tx.transfer_pair_id is not None} & twin_ids


# ---------------------------------------------------------------------------
# guards: unlink, categories, synced loans, paying accounts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unlink_refused_for_loan_payment_pair(client: AsyncClient, auth_headers, session, book):
    payment = await _pay(session, book)
    cash = await _fresh(session, Transaction, payment.cash_transaction_id)
    assert cash is not None and cash.transfer_pair_id is not None

    response = await client.delete(f"/api/connections/transfers/{cash.transfer_pair_id}", headers=auth_headers)

    assert response.status_code == 409, response.text
    await session.refresh(cash)
    assert cash.transfer_pair_id is not None
    summary = await get_summary(session, book.workspace.id, book.user.id)
    assert summary.monthly_expenses == pytest.approx(float(INTEREST))


@pytest.mark.asyncio
async def test_unlink_still_works_for_plain_transfers(client: AsyncClient, auth_headers, session, book):
    pair = uuid.uuid4()
    other = await _checking(session, book.workspace, book.user, name="Savings", acc_type="savings")
    await _tx(session, book.user, book.checking, "50", "debit", transfer_pair_id=pair)
    await _tx(session, book.user, other, "50", "credit", transfer_pair_id=pair)

    response = await client.delete(f"/api/connections/transfers/{pair}", headers=auth_headers)

    assert response.status_code == 204, response.text


@pytest.mark.asyncio
async def test_interest_category_must_be_expense(client: AsyncClient, auth_headers, session, book):
    transfer_like = await _category(session, book.workspace, book.user, "Moves", treat_as_transfer=True)
    ignored = await _category(session, book.workspace, book.user, "Hidden", is_ignored=True)

    for category in (transfer_like, ignored):
        response = await client.post(
            "/api/loan-payments",
            json={
                "loan_account_id": str(book.loan.id), "cash_account_id": str(book.checking.id),
                "date": app_today().isoformat(), "total_amount": "2000", "interest_amount": "1200",
                "interest_category_id": str(category.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 400, response.text

    missing = await client.post(
        "/api/loan-payments",
        json={
            "loan_account_id": str(book.loan.id), "cash_account_id": str(book.checking.id),
            "date": app_today().isoformat(), "total_amount": "2000", "interest_amount": "1200",
        },
        headers=auth_headers,
    )
    assert missing.status_code == 400, missing.text
    assert (await session.execute(select(func.count()).select_from(LoanPayment))).scalar_one() == 0


@pytest.mark.asyncio
async def test_amounts_are_validated(client: AsyncClient, auth_headers, book):
    base = {
        "loan_account_id": str(book.loan.id), "cash_account_id": str(book.checking.id),
        "date": app_today().isoformat(), "interest_category_id": str(book.interest.id),
    }
    for total, interest in (("0", "0"), ("100", "150"), ("100", "-1"), ("100.005", "0"), ("-5", "0")):
        response = await client.post(
            "/api/loan-payments", json={**base, "total_amount": total, "interest_amount": interest},
            headers=auth_headers,
        )
        assert response.status_code == 400, (total, interest, response.text)


@pytest.mark.asyncio
async def test_payment_refused_on_synced_loan(client: AsyncClient, auth_headers, session, book):
    conn = await _connection(session, book.user)
    synced = Account(
        id=uuid.uuid4(), user_id=book.user.id, workspace_id=book.workspace.id, name="Bank mortgage",
        type="loan", balance=Decimal("-300000"), currency="BRL", connection_id=conn.id,
    )
    session.add(synced)
    await session.commit()
    feedless = Account(
        id=uuid.uuid4(), user_id=book.user.id, workspace_id=book.workspace.id, name="Bank car loan",
        type="loan", balance=Decimal("-20000"), currency="BRL", connection_id=conn.id,
    )
    session.add(feedless)
    await session.commit()
    await _tx(session, book.user, synced, "1200", "debit", source="sync", category_id=book.interest.id)

    for loan in (synced, feedless):
        response = await client.post(
            "/api/loan-payments",
            json={
                "loan_account_id": str(loan.id), "cash_account_id": str(book.checking.id),
                "date": app_today().isoformat(), "total_amount": "2000", "interest_amount": "1200",
                "interest_category_id": str(book.interest.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 409, response.text
    assert "syncs its own payments" in (
        await client.post(
            "/api/loan-payments",
            json={
                "loan_account_id": str(synced.id), "cash_account_id": str(book.checking.id),
                "date": app_today().isoformat(), "total_amount": "800",
            },
            headers=auth_headers,
        )
    ).json()["detail"]

    summary = await client.get(f"/api/accounts/{synced.id}/loan-summary", headers=auth_headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["manual_payments_allowed"] is False
    with pytest.raises(LoanPaymentConflict):
        await record_payment(
            session, book.workspace.id, book.user.id,
            LoanPaymentCreate(
                loan_account_id=synced.id, cash_account_id=book.checking.id, date=app_today(),
                total_amount=Decimal("800"),
            ),
        )


@pytest.mark.asyncio
async def test_credit_card_and_cross_currency_paying_accounts_refused(client: AsyncClient, auth_headers, session, book):
    card = await _checking(session, book.workspace, book.user, name="Card", acc_type="credit_card")
    usd = await _checking(session, book.workspace, book.user, name="USD account", currency="USD")
    other_loan = await _manual_loan(session, book.workspace, book.user, "1000")

    for paying in (card, usd, other_loan, book.loan):
        response = await client.post(
            "/api/loan-payments",
            json={
                "loan_account_id": str(book.loan.id), "cash_account_id": str(paying.id),
                "date": app_today().isoformat(), "total_amount": "2000", "interest_amount": "1200",
                "interest_category_id": str(book.interest.id),
            },
            headers=auth_headers,
        )
        assert response.status_code == 400, (paying.name, response.text)

    not_a_loan = await client.post(
        "/api/loan-payments",
        json={
            "loan_account_id": str(book.checking.id), "cash_account_id": str(usd.id),
            "date": app_today().isoformat(), "total_amount": "2000",
        },
        headers=auth_headers,
    )
    assert not_a_loan.status_code == 400, not_a_loan.text
    assert (await session.execute(select(func.count()).select_from(LoanPayment))).scalar_one() == 0


# ---------------------------------------------------------------------------
# pairing an existing debit, deleting, atomicity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_debit_is_paired_not_duplicated(session, book, sqlite_to_char):  # noqa: F811
    # A synced bank debit, already categorised as spending.
    repayments = await _category(session, book.workspace, book.user, "Debt repayments")
    bank_row = await _tx(session, book.user, book.checking, "2000", "debit", source="sync", category_id=repayments.id)
    summary = await get_summary(session, book.workspace.id, book.user.id)
    assert summary.monthly_expenses == pytest.approx(2000.0)

    payment = await _pay(
        session, book, cash_account_id=None, existing_transaction_id=bank_row.id, date=None
    )

    assert payment.cash_leg_created is False
    assert payment.cash_transaction_id == bank_row.id
    debits = (
        await session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.account_id == book.checking.id, Transaction.type == "debit"
            )
        )
    ).scalar_one()
    assert debits == 1
    await session.refresh(bank_row)
    assert bank_row.transfer_pair_id is not None and bank_row.category_id == repayments.id
    assert await _balance(session, book.workspace, book.checking.id) == pytest.approx(8000.0)
    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-299200.0)
    summary = await get_summary(session, book.workspace.id, book.user.id)
    # The bank row's 2,000 left P&L; only the interest is spending.
    assert summary.monthly_expenses == pytest.approx(float(INTEREST))

    # It can't be used twice, and its amount must match.
    with pytest.raises(ValueError):
        await _pay(session, book, cash_account_id=None, existing_transaction_id=bank_row.id, date=None)
    other = await _tx(session, book.user, book.checking, "1999", "debit", source="sync")
    with pytest.raises(ValueError):
        await _pay(session, book, cash_account_id=None, existing_transaction_id=other.id, date=None)


@pytest.mark.asyncio
async def test_delete_payment_restores_existing_debit(client: AsyncClient, auth_headers, session, book):
    repayments = await _category(session, book.workspace, book.user, "Debt repayments")
    bank_row = await _tx(session, book.user, book.checking, "2000", "debit", source="sync", category_id=repayments.id)
    payment = await _pay(session, book, cash_account_id=None, existing_transaction_id=bank_row.id, date=None)

    response = await client.delete(f"/api/loan-payments/{payment.id}", headers=auth_headers)
    assert response.status_code == 204, response.text

    restored = await _fresh(session, Transaction, bank_row.id)
    assert restored is not None
    assert restored.transfer_pair_id is None and restored.category_id == repayments.id
    left = (
        await session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.account_id == book.loan.id, Transaction.source != "opening_balance"
            )
        )
    ).scalar_one()
    assert left == 0
    assert (await session.execute(select(func.count()).select_from(LoanPayment))).scalar_one() == 0
    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-300000.0)


@pytest.mark.asyncio
async def test_delete_payment_removes_rows_it_wrote(client: AsyncClient, auth_headers, session, book):
    payment = await _pay(session, book)

    response = await client.delete(f"/api/loan-payments/{payment.id}", headers=auth_headers)
    assert response.status_code == 204, response.text

    for tx_id in (payment.cash_transaction_id, payment.loan_transaction_id, payment.interest_transaction_id):
        assert tx_id is not None
        assert await _fresh(session, Transaction, tx_id) is None
    assert await _balance(session, book.workspace, book.checking.id) == pytest.approx(10000.0)
    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-300000.0)


@pytest.mark.asyncio
async def test_atomic_on_failure(session, book, monkeypatch):
    """A failure after the first leg leaves no rows behind."""

    async def boom(*args, **kwargs):
        raise RuntimeError("interest stamping failed")

    workspace_id = book.workspace.id
    checking_id = book.checking.id
    rows_before = (await session.execute(select(func.count()).select_from(Transaction))).scalar_one()
    monkeypatch.setattr(loan_payment_service, "stamp_primary_amount", boom)

    with pytest.raises(RuntimeError):
        await _pay(session, book)
    # What the request's session does on an error: close without committing.
    await session.rollback()

    rows_after = (await session.execute(select(func.count()).select_from(Transaction))).scalar_one()
    assert rows_after == rows_before
    assert (await session.execute(select(func.count()).select_from(LoanPayment))).scalar_one() == 0
    accounts = await account_service.get_accounts(session, workspace_id)
    assert next(a["current_balance"] for a in accounts if a["id"] == checking_id) == pytest.approx(10000.0)


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
    assert group.name == "Housing"


@pytest.mark.asyncio
async def test_payment_legs_land_in_loan_repayments(session, seeded):
    payment = await _pay(session, seeded)

    cash = await _fresh(session, Transaction, payment.cash_transaction_id)
    leg = await _fresh(session, Transaction, payment.loan_transaction_id)
    interest = await _fresh(session, Transaction, payment.interest_transaction_id)
    assert cash is not None and leg is not None and interest is not None
    assert cash.category_id == seeded.repayments.id and leg.category_id == seeded.repayments.id
    assert interest.category_id == seeded.interest.id


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", list(PROBES), ids=list(PROBES))
async def test_paired_legs_in_loan_repayments_leave_only_interest(session, seeded, sqlite_to_char, probe):  # noqa: F811
    measure = PROBES[probe]
    by_repayments = seeded.measuring(seeded.repayments)
    before = await measure(session, seeded)
    repayments_before = (await measure(session, by_repayments))[2]

    await _pay(session, seeded)

    income, expenses, interest = (a - b for a, b in zip(await measure(session, seeded), before))
    assert income == pytest.approx(0.0)
    assert expenses == pytest.approx(float(INTEREST))
    assert interest == pytest.approx(float(INTEREST))
    # Nothing lands in the Loan repayments bucket. (The cash flow chart and
    # the account stats have no category split: their third figure is all
    # outflow, which is the interest.)
    in_repayments = (await measure(session, by_repayments))[2] - repayments_before
    expected = float(INTEREST) if probe in ("report_cash_flow", "account_stats") else 0.0
    assert in_repayments == pytest.approx(expected)


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


def test_aggregation_modules_never_read_loan_payments():
    """Static guard: a principal amount derived from loan_payments (or a
    provider figure in loan_details) must never feed a P&L total."""
    assert len(_AGGREGATION_MODULES) > 4
    for path in _AGGREGATION_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.alias):
                names.add(node.name)
                names.update(node.name.split("."))
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.update(node.module.split("."))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                names.add(node.value)
        hits = {forbidden for forbidden in _FORBIDDEN for name in names if forbidden in name}
        assert not hits, f"{path.name} references {sorted(hits)}"
