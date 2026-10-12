"""Principal repayment never reaches P&L: a credit on a loan is never income.

A recorded repayment is a transfer pair, which every P&L site already
drops. A credit on a loan that was never paired (a synced repayment whose
cash leg was not matched) is kept out by `is_loan_credit` in the SQL filter
`counts_as_pnl` and by its Python twin `_counts_as_user_pnl_row`, which the
forecast paths use. Every amount here is made up.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.transaction import Transaction
from app.models.user import User
from app.services._query_filters import counts_as_pnl, counts_as_user_pnl
from app.services.account_service import get_account_summary
from app.services.dashboard_service import (
    _counts_as_user_pnl_row,
    _get_forecast_transactions,
    get_summary,
)
from app.services.report_service import get_income_expenses_report
from tests.test_loan_accounts import _account, _balance, _manual_loan, _tx


async def _category(session: AsyncSession, user: User, name: str) -> Category:
    cat = Category(id=uuid.uuid4(), user_id=user.id, name=name, icon="tag", color="#000000")
    session.add(cat)
    await session.commit()
    await session.refresh(cat)
    return cat


def _py_to_char(value, fmt):
    """PostgreSQL `to_char(date, fmt)` for the formats the reports use, so the
    income/expenses report runs on the SQLite test engine."""
    if value is None:
        return None
    y, m, d = str(value)[:10].split("-")
    return {"YYYY-MM-DD": f"{y}-{m}-{d}", "YYYY-MM": f"{y}-{m}", "YYYY": y}.get(fmt, f"{y}-{m}-{d}")


def _register_to_char(sync_conn) -> None:
    dbapi = sync_conn.connection.dbapi_connection
    assert dbapi is not None
    dbapi.create_function("to_char", 2, _py_to_char)


@pytest.fixture
async def sqlite_to_char(session: AsyncSession):
    conn = await session.connection()
    await conn.run_sync(_register_to_char)


@pytest.mark.asyncio
async def test_unpaired_loan_credit_never_income(session, test_user, test_workspace, sqlite_to_char):
    """A repayment credit on a loan that was never paired stays out of every
    income figure, while the loan's interest debit is still an expense."""
    loan = await _manual_loan(session, test_workspace, test_user, "300000")
    checking = await _account(session, test_user, name="Everyday", acc_type="checking")
    interest = await _category(session, test_user, "Loan interest")

    # A synced repayment credit whose cash leg was never matched, plus one
    # left uncategorised by a manual entry.
    await _tx(session, test_user, loan, "2000", "credit", source="sync")
    await _tx(session, test_user, loan, "500", "credit")
    await _tx(session, test_user, loan, "1200", "debit", category_id=interest.id)
    # Real income on another account still counts.
    await _tx(session, test_user, checking, "100", "credit")

    summary = await get_summary(session, test_workspace.id, test_user.id)
    assert summary.monthly_income == pytest.approx(100.0)
    assert summary.monthly_expenses == pytest.approx(1200.0)

    report = await get_income_expenses_report(
        session, test_workspace.id, test_user.id, months=1, currency="BRL"
    )
    breakdowns = {b.key: b.value for b in report.summary.breakdowns}
    assert breakdowns["income"] == pytest.approx(100.0)
    assert breakdowns["expenses"] == pytest.approx(1200.0)

    loan_stats = await get_account_summary(session, loan.id, test_workspace.id)
    assert loan_stats is not None
    assert loan_stats["monthly_income"] == pytest.approx(0.0)
    assert loan_stats["monthly_expenses"] == pytest.approx(1200.0)

    # The credits still move the balance: they are repayments.
    assert await _balance(session, test_workspace, loan.id) == pytest.approx(-300000 + 2000 + 500 - 1200)


@pytest.mark.asyncio
async def test_pending_and_forecast_loan_credits_never_projected_income(session, test_user, test_workspace):
    """The forecast path runs the Python twin, not the SQL filter."""
    loan = await _manual_loan(session, test_workspace, test_user, "300000")
    checking = await _account(session, test_user, name="Everyday", acc_type="checking")
    today = app_today()
    # Inside this month whatever day it is: pending today, or posted later
    # this month when there is a later day left.
    future = today + timedelta(days=1) if (today + timedelta(days=1)).month == today.month else today

    await _tx(session, test_user, loan, "2000", "credit", status="pending")
    await _tx(session, test_user, loan, "700", "credit", when=future, status="pending")
    await _tx(session, test_user, checking, "40", "credit", status="pending")

    summary = await get_summary(session, test_workspace.id, test_user.id)

    assert summary.projected_income == pytest.approx(40.0)
    assert summary.monthly_income == pytest.approx(0.0)

    rows = await _get_forecast_transactions(
        session, test_workspace.id, today.replace(day=1), today + timedelta(days=40)
    )
    loan_rows = [tx for tx in rows if tx.account_id == loan.id]
    assert len(loan_rows) == 2
    assert not any(_counts_as_user_pnl_row(tx) for tx in loan_rows)


def test_python_twin_drops_loan_credits_only():
    loan = Account(id=uuid.uuid4(), name="Loan", type="loan", currency="BRL")
    checking = Account(id=uuid.uuid4(), name="Everyday", type="checking", currency="BRL")

    def row(account: Account, tx_type: str) -> Transaction:
        return Transaction(
            id=uuid.uuid4(), account=account, account_id=account.id, type=tx_type,
            amount=Decimal("10"), source="manual", status="posted",
            transfer_pair_id=None, is_ignored=False, exclude_from_pnl=False, category=None,
        )

    assert _counts_as_user_pnl_row(row(loan, "credit")) is False
    assert _counts_as_user_pnl_row(row(loan, "debit")) is True
    assert _counts_as_user_pnl_row(row(checking, "credit")) is True
    assert _counts_as_user_pnl_row(row(checking, "debit")) is True


@pytest.mark.asyncio
async def test_python_twin_agrees_with_sql_filter(session, test_user, test_workspace):
    """For every shape of loan row, the SQL filter and its twin agree, with
    and without the Account table joined into the outer query."""
    loan = await _manual_loan(session, test_workspace, test_user, "300000")
    card = await _account(session, test_user, name="Card", acc_type="credit_card")
    checking = await _account(session, test_user, name="Everyday", acc_type="checking")
    interest = await _category(session, test_user, "Loan interest")
    pair = uuid.uuid4()

    await _tx(session, test_user, loan, "2000", "credit")  # unpaired repayment
    await _tx(session, test_user, loan, "2000", "credit", source="sync", status="pending")
    await _tx(session, test_user, loan, "800", "credit", transfer_pair_id=pair)  # paired leg
    await _tx(session, test_user, checking, "800", "debit", transfer_pair_id=pair)
    await _tx(session, test_user, loan, "1200", "debit", category_id=interest.id)
    await _tx(session, test_user, loan, "30", "debit")  # uncategorised fee
    await _tx(session, test_user, checking, "100", "credit")
    await _tx(session, test_user, checking, "60", "debit")
    await _tx(session, test_user, card, "25", "credit")  # merchant refund on a card

    scoped = Transaction.workspace_id == test_workspace.id
    sql_ids = set(
        (await session.execute(select(Transaction.id).where(scoped, counts_as_user_pnl()))).scalars()
    )
    joined_ids = set(
        (
            await session.execute(
                select(Transaction.id)
                .join(Account, Transaction.account_id == Account.id)
                .outerjoin(Category, Transaction.category_id == Category.id)
                .where(scoped, counts_as_user_pnl())
            )
        ).scalars()
    )
    rows = (
        await session.execute(
            select(Transaction)
            .where(scoped)
            .options(selectinload(Transaction.account), selectinload(Transaction.category))
        )
    ).scalars().all()
    twin_ids = {tx.id for tx in rows if _counts_as_user_pnl_row(tx)}

    assert sql_ids == twin_ids
    assert joined_ids == twin_ids
    loan_credit_ids = {tx.id for tx in rows if tx.account_id == loan.id and tx.type == "credit"}
    assert loan_credit_ids and not (loan_credit_ids & twin_ids)
    # Loan debits (interest, fees) still count; the opening row is not a
    # P&L concern of these filters and is dropped by every caller.
    loan_debits = {
        tx.id for tx in rows
        if tx.account_id == loan.id and tx.type == "debit" and tx.source != "opening_balance"
    }
    assert loan_debits and loan_debits <= twin_ids

    # counts_as_pnl (per-account stats) carries the same clause.
    pnl_loan_credit_total = (
        await session.execute(
            select(func.coalesce(func.sum(Transaction.amount), 0)).where(
                scoped, Transaction.account_id == loan.id, Transaction.type == "credit", counts_as_pnl()
            )
        )
    ).scalar_one()
    assert Decimal(str(pnl_loan_credit_total)) == Decimal("0")
