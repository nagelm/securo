"""Recurring loan payments: only the interest is ever a projected expense.

A schedule of 2,000 a period with 1,200 fixed interest stands for a transfer
of 2,000 from the paying account to the loan plus a 1,200 interest debit on
the loan. Every projection that feeds P&L (dashboard, budgets, reports, cash
flow, calendar) must rise by exactly 1,200, all in the interest category;
the balance projection nets to minus the interest. Manual loans generate the
real payment; bank-connected ones are projection only, fulfilled by the
synced rows. Every amount is made up.
"""

import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.loan_payment import LoanPayment
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate, RecurringTransactionUpdate
from app.services import account_service, category_service, loan_payment_service
from app.services.dashboard_service import (
    _get_recurring_projections,
    get_projected_transactions,
)
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
    update_recurring_transaction,
)
from app.services.report_service import get_cash_flow_report, get_income_expenses_report
from app.services.transaction_calendar_service import get_transaction_calendar
from tests.test_loan_accounts import _balance, _connection
from tests.test_loan_pnl_guard import sqlite_to_char  # noqa: F401  (fixture)
from tests import test_loan_pnl_invariant as invariant
from tests.test_loan_pnl_invariant import (
    INTEREST,
    TOTAL,
    Book,
    _budget_projected,
    _category,
    _checking,
    _dashboard_projected,
    _fresh,
)
from tests.test_recurring_match_sync import _provider, _run_sync, _tx

# Checking +10,000, manual loan -300,000, "Loan interest" category.
book = invariant.book


async def _schedule(session: AsyncSession, book: Book, **overrides) -> RecurringTransaction:
    payload = dict(
        description="Mortgage payment",
        amount=TOTAL,
        currency="BRL",
        type="debit",
        # Yearly: exactly one occurrence in every window under test.
        frequency="yearly",
        start_date=app_today(),
        account_id=book.checking.id,
        kind="loan_payment",
        transfer_account_id=book.loan.id,
        interest_amount=INTEREST,
        interest_category_id=book.interest.id,
    )
    payload.update(overrides)
    return await create_recurring_transaction(
        session, book.workspace.id, book.user.id, RecurringTransactionCreate.model_validate(payload)
    )


# ---------------------------------------------------------------------------
# P&L projections: interest only
# ---------------------------------------------------------------------------


async def _report_forecast(session, book):
    report = await get_income_expenses_report(
        session, book.workspace.id, book.user.id, months=1, interval="monthly", currency="BRL"
    )
    totals = {b.key: b.value for b in report.summary.breakdowns}
    cat = sum(
        c.value for c in report.composition
        if c.key == str(book.interest.id) and c.group == "expenses"
    )
    return totals["projectedIncome"], totals["projectedExpenses"], cat


PROJECTION_PROBES = {
    # (income, expenses, expenses in the interest category)
    "dashboard_projected": _dashboard_projected,
    "budget_projected": _budget_projected,
    "report_forecast": _report_forecast,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", list(PROJECTION_PROBES), ids=list(PROJECTION_PROBES))
async def test_projected_expense_rises_by_the_interest_only(session, book, sqlite_to_char, probe):  # noqa: F811
    measure = PROJECTION_PROBES[probe]
    income_before, expenses_before, cat_before = await measure(session, book)

    await _schedule(session, book)

    income_after, expenses_after, cat_after = await measure(session, book)
    assert income_after - income_before == pytest.approx(0.0)
    assert expenses_after - expenses_before == pytest.approx(float(INTEREST))
    assert cat_after - cat_before == pytest.approx(float(INTEREST))


@pytest.mark.asyncio
async def test_cash_flow_projection_shows_interest_only(session, book, sqlite_to_char):  # noqa: F811
    async def outflow_after_today() -> tuple[float, float]:
        report = await get_cash_flow_report(
            session, book.workspace.id, book.user.id, months=1, interval="daily", currency="BRL"
        )
        today = app_today().isoformat()
        future = [p for p in report.trend if p.date > today]
        cat = sum(
            c.value for c in report.composition
            if c.key == str(book.interest.id) and c.group == "expenses"
        )
        return sum(p.breakdowns["outflow"] for p in future), cat

    outflow_before, cat_before = await outflow_after_today()
    await _schedule(session, book, start_date=app_today() + timedelta(days=1))
    outflow_after, cat_after = await outflow_after_today()

    # Fork (owner 2026-10-14): cash flow leaves loan accounts out, so the whole
    # repayment leaves cash and the interest row, which sits on the loan, isn't
    # in the cash-flow composition. P&L still sees only the interest (below).
    assert outflow_after - outflow_before == pytest.approx(float(TOTAL))
    assert cat_after - cat_before == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_calendar_shows_interest_as_expense_and_the_payment_as_transfer(session, book):
    await _schedule(session, book)

    calendar = await get_transaction_calendar(session, book.workspace.id, book.user.id)
    day = next(d for d in calendar.days if d.date == app_today())
    assert day.projected_expense == pytest.approx(float(INTEREST))
    assert day.projected_income == pytest.approx(0.0)
    # Out of checking, into the loan: nets to zero across both accounts.
    assert day.projected_transfer_net == pytest.approx(0.0)
    projected = [i for i in day.items if i.kind == "projected"]
    assert sorted((i.account_id == book.loan.id, i.type, i.amount, i.is_transfer) for i in projected) == [
        (False, "debit", float(TOTAL), True),
        (True, "credit", float(TOTAL), True),
        (True, "debit", float(INTEREST), False),
    ]

    # Filtered to the paying account: the transfer out, no expense at all.
    only_checking = await get_transaction_calendar(
        session, book.workspace.id, book.user.id, account_ids=[book.checking.id]
    )
    day = next(d for d in only_checking.days if d.date == app_today())
    assert day.projected_expense == pytest.approx(0.0)
    assert day.projected_transfer_net == pytest.approx(-float(TOTAL))


@pytest.mark.asyncio
async def test_pnl_projection_is_one_interest_row_on_the_loan(session, book):
    rec = await _schedule(session, book)
    start = app_today().replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)

    rows = await _get_recurring_projections(session, book.workspace.id, start, end)
    assert rows == [{
        "category_id": book.interest.id,
        "amount": float(INTEREST),
        "type": "debit",
        "currency": "BRL",
        "date": rec.next_occurrence,
    }]
    # The interest lives on the loan, like the real interest row.
    assert await _get_recurring_projections(
        session, book.workspace.id, start, end, [book.checking.id]
    ) == []
    assert len(await _get_recurring_projections(session, book.workspace.id, start, end, [book.loan.id])) == 1


@pytest.mark.asyncio
async def test_balance_projection_nets_to_minus_the_interest(session, book):
    await _schedule(session, book)
    start = app_today().replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)

    async def net(account_ids):
        rows = await _get_recurring_projections(
            session, book.workspace.id, start, end, account_ids, include_transfer_like=True
        )
        return sum(r["amount"] if r["type"] == "credit" else -r["amount"] for r in rows)

    assert await net(None) == pytest.approx(-float(INTEREST))
    assert await net([book.checking.id]) == pytest.approx(-float(TOTAL))
    assert await net([book.loan.id]) == pytest.approx(float(TOTAL - INTEREST))


@pytest.mark.asyncio
async def test_interest_category_turned_transfer_later_leaves_pnl_without_principal(session, book):
    rec = await _schedule(session, book)
    start = app_today().replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    book.interest.treat_as_transfer = True
    await session.commit()

    assert await _get_recurring_projections(session, book.workspace.id, start, end) == []
    balance = await _get_recurring_projections(
        session, book.workspace.id, start, end, include_transfer_like=True
    )
    assert len(balance) == 3 and all(r["date"] == rec.next_occurrence for r in balance)

    book.interest.treat_as_transfer = False
    book.interest.is_ignored = True
    await session.commit()
    balance = await _get_recurring_projections(
        session, book.workspace.id, start, end, include_transfer_like=True
    )
    # Ignored never moves a balance; the transfer legs still do.
    assert sorted(r["type"] for r in balance) == ["credit", "debit"]


@pytest.mark.asyncio
async def test_upcoming_list_has_one_item_with_interest_and_loan(session, book):
    await _schedule(session, book)

    items = await get_projected_transactions(session, book.workspace.id, book.user.id)
    assert len(items) == 1
    item = items[0]
    assert (item.kind, item.amount, item.type, item.category_id) == ("loan_payment", float(TOTAL), "debit", None)
    assert item.account_id == str(book.checking.id)
    assert item.transfer_account_id == str(book.loan.id)
    assert item.interest_amount == pytest.approx(float(INTEREST))
    assert item.interest_category_id == str(book.interest.id)
    assert item.interest_category_name == "Loan interest"


# ---------------------------------------------------------------------------
# generation: manual loans write the real payment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_generation_writes_the_pair_and_the_interest(session, book, sqlite_to_char):  # noqa: F811
    _, expenses_before, cat_before = await _dashboard_projected(session, book)
    rec = await _schedule(session, book, frequency="monthly")
    rec_id = rec.id

    written = await generate_pending(session, book.user.id, up_to=app_today())

    assert written == 3
    payments = await loan_payment_service.list_payments(session, book.workspace.id, loan_account_id=book.loan.id)
    assert len(payments) == 1
    payment = payments[0]
    assert (payment.total, payment.interest, payment.principal) == (TOTAL, INTEREST, TOTAL - INTEREST)
    assert payment.recurring_transaction_id == rec_id
    assert payment.date == app_today()
    cash = await _fresh(session, Transaction, payment.cash_transaction_id)
    leg = await _fresh(session, Transaction, payment.loan_transaction_id)
    interest = await _fresh(session, Transaction, payment.interest_transaction_id)
    assert cash is not None and leg is not None and interest is not None
    assert cash.transfer_pair_id is not None and cash.transfer_pair_id == leg.transfer_pair_id
    assert (cash.recurring_transaction_id, interest.recurring_transaction_id) == (rec_id, rec_id)
    assert (cash.status, leg.status, interest.status) == ("posted", "posted", "posted")
    assert interest.category_id == book.interest.id

    assert await _balance(session, book.workspace, book.checking.id) == pytest.approx(8000.0)
    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-299200.0)

    # The occurrence is now real: projected expense still rose by the
    # interest once, not twice, and never by the principal.
    _, expenses_after, cat_after = await _dashboard_projected(session, book)
    assert expenses_after - expenses_before == pytest.approx(float(INTEREST))
    assert cat_after - cat_before == pytest.approx(float(INTEREST))

    # Generating again is a no-op until the next occurrence is due.
    assert await generate_pending(session, book.user.id, up_to=app_today()) == 0
    refreshed = await _fresh(session, RecurringTransaction, rec_id)
    assert refreshed is not None and refreshed.next_occurrence > app_today()


@pytest.mark.asyncio
async def test_generation_pairs_an_existing_debit_instead_of_duplicating(session, book):
    existing = Transaction(
        id=uuid.uuid4(), user_id=book.user.id, workspace_id=book.workspace.id,
        account_id=book.checking.id, description="Mortgage payment", amount=TOTAL,
        currency="BRL", date=app_today(), type="debit", source="csv", status="posted",
    )
    session.add(existing)
    await session.commit()
    await _schedule(session, book)

    assert await generate_pending(session, book.user.id, up_to=app_today()) == 2

    payments = await loan_payment_service.list_payments(session, book.workspace.id, loan_account_id=book.loan.id)
    assert len(payments) == 1
    assert payments[0].cash_transaction_id == existing.id
    assert payments[0].cash_leg_created is False
    debits = (
        await session.execute(
            select(func.count()).select_from(Transaction).where(
                Transaction.account_id == book.checking.id, Transaction.type == "debit"
            )
        )
    ).scalar_one()
    assert debits == 1
    assert await _balance(session, book.workspace, book.loan.id) == pytest.approx(-299200.0)


@pytest.mark.asyncio
async def test_generation_stops_without_writing_when_the_loan_is_closed(session, book):
    rec = await _schedule(session, book)
    book.loan.is_closed = True
    await session.commit()

    assert await generate_pending(session, book.user.id, up_to=app_today()) == 0
    assert (await session.execute(select(func.count()).select_from(LoanPayment))).scalar_one() == 0
    refreshed = await _fresh(session, RecurringTransaction, rec.id)
    assert refreshed is not None and refreshed.next_occurrence == app_today()


# ---------------------------------------------------------------------------
# connected accounts: projection only, the synced rows fulfil it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_connected_loan_schedule_is_projection_only(session, book):
    conn = await _connection(session, book.user)
    book.loan.connection_id = conn.id
    await session.commit()

    rec = await _schedule(session, book, auto_generate=True)
    assert rec.auto_generate is False
    assert await generate_pending(session, book.user.id, up_to=app_today()) == 0
    assert (await session.execute(select(func.count()).select_from(LoanPayment))).scalar_one() == 0

    # Turning generation back on is refused the same way.
    updated = await update_recurring_transaction(
        session, rec.id, book.workspace.id, RecurringTransactionUpdate(auto_generate=True)
    )
    assert updated is not None and updated.auto_generate is False


@pytest.mark.asyncio
async def test_synced_payment_fulfils_the_projection_without_double_counting(
    session, test_user, test_workspace, sqlite_to_char  # noqa: F811
):
    # Both accounts sync from the same bank: the cash debit arrives through
    # the real sync path, the loan's credit and interest the way Akahu
    # writes them (transfer pair + interest debit).
    conn = await _connection(session, test_user)
    conn.workspace_id = test_workspace.id
    conn.credentials = {"token": "fake"}
    checking = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id, workspace_id=test_workspace.id,
        name="Checking", type="checking", external_id="acc-ext-1", balance=Decimal("0"), currency="BRL",
    )
    loan = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id, workspace_id=test_workspace.id,
        name="Mortgage", type="loan", external_id="loan-ext-1", balance=Decimal("0"), currency="BRL",
    )
    session.add_all([checking, loan])
    await session.commit()
    interest_cat = await _category(session, test_workspace, test_user, "Loan interest")
    synced_book = Book(test_user, test_workspace, checking, loan, interest_cat)
    conn_id, checking_id, loan_id, interest_id = conn.id, checking.id, loan.id, interest_cat.id

    _, expenses_baseline, _ = await _dashboard_projected(session, synced_book)
    rec = await _schedule(session, synced_book)
    rec_id = rec.id
    assert rec.auto_generate is False
    _, expenses_projected, cat_projected = await _dashboard_projected(session, synced_book)
    assert expenses_projected - expenses_baseline == pytest.approx(float(INTEREST))
    assert cat_projected == pytest.approx(float(INTEREST))

    provider = _provider([
        _tx(external_id="s1", description="MORTGAGE PAYMENT", amount=TOTAL, date=app_today()),
    ])
    await _run_sync(session, conn_id, test_workspace, test_user, provider)

    cash = (
        await session.execute(
            select(Transaction).where(Transaction.account_id == checking_id, Transaction.source == "sync")
        )
    ).scalar_one()
    assert cash.recurring_transaction_id == rec_id
    pair = uuid.uuid4()
    cash.transfer_pair_id = pair
    session.add_all([
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=loan_id,
            description="Repayment", amount=TOTAL, currency="BRL", date=app_today(), type="credit",
            source="sync", status="posted", transfer_pair_id=pair,
        ),
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=loan_id,
            description="Interest", amount=INTEREST, currency="BRL", date=app_today(), type="debit",
            source="sync", status="posted", category_id=interest_id,
        ),
    ])
    await session.commit()

    # The projection is gone and the real interest replaces it: the month
    # still shows 1,200 of expense, not 2,400 and never the principal.
    _, expenses_synced, cat_synced = await _dashboard_projected(session, synced_book)
    assert expenses_synced - expenses_baseline == pytest.approx(float(INTEREST))
    assert cat_synced == pytest.approx(float(INTEREST))
    start = app_today().replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    assert await _get_recurring_projections(session, test_workspace.id, start, end) == []


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "category_set", "credit", "no_loan", "not_a_loan", "interest_over_total",
        "transfer_like_interest", "ignored_interest", "missing_interest_category",
        "credit_card_pays", "cross_currency", "closed_loan",
    ],
)
async def test_invalid_loan_payment_schedules_are_refused(session, book, case):
    overrides: dict = {}
    if case == "category_set":
        overrides["category_id"] = book.interest.id
    elif case == "credit":
        overrides["type"] = "credit"
    elif case == "no_loan":
        overrides["transfer_account_id"] = None
    elif case == "not_a_loan":
        other = await _checking(session, book.workspace, book.user, name="Savings")
        overrides["transfer_account_id"] = other.id
    elif case == "interest_over_total":
        overrides["interest_amount"] = TOTAL + 1
    elif case == "transfer_like_interest":
        cat = await _category(session, book.workspace, book.user, "Moves", treat_as_transfer=True)
        overrides["interest_category_id"] = cat.id
    elif case == "ignored_interest":
        cat = await _category(session, book.workspace, book.user, "Hidden", is_ignored=True)
        overrides["interest_category_id"] = cat.id
    elif case == "missing_interest_category":
        overrides["interest_category_id"] = None
    elif case == "credit_card_pays":
        card = await _checking(session, book.workspace, book.user, name="Card", acc_type="credit_card")
        overrides["account_id"] = card.id
    elif case == "cross_currency":
        usd = await _checking(session, book.workspace, book.user, name="USD", currency="USD")
        overrides["account_id"] = usd.id
    elif case == "closed_loan":
        book.loan.is_closed = True
        await session.commit()

    with pytest.raises(ValueError):
        await _schedule(session, book, **overrides)
    assert (await session.execute(select(func.count()).select_from(RecurringTransaction))).scalar_one() == 0


@pytest.mark.asyncio
async def test_standard_schedule_refuses_loan_fields(session, book):
    with pytest.raises(ValueError):
        await _schedule(session, book, kind="standard")


@pytest.mark.asyncio
async def test_update_revalidates_the_split(session, book):
    rec = await _schedule(session, book)
    rec_id = rec.id

    # Refused before anything changes.
    with pytest.raises(ValueError):
        await update_recurring_transaction(
            session, rec_id, book.workspace.id, RecurringTransactionUpdate(interest_amount=TOTAL + 1)
        )
    assert rec.interest_amount == INTEREST
    updated = await update_recurring_transaction(
        session, rec_id, book.workspace.id,
        RecurringTransactionUpdate(amount=Decimal("2100"), interest_amount=Decimal("1100")),
    )
    assert updated is not None
    assert (updated.amount, updated.interest_amount) == (Decimal("2100"), Decimal("1100"))


@pytest.mark.asyncio
async def test_api_round_trip_and_400(client: AsyncClient, auth_headers, book):
    body = {
        "description": "Mortgage payment",
        "amount": "2000.00",
        "currency": "BRL",
        "type": "debit",
        "frequency": "monthly",
        "start_date": (app_today() + timedelta(days=3)).isoformat(),
        "account_id": str(book.checking.id),
        "kind": "loan_payment",
        "transfer_account_id": str(book.loan.id),
        "interest_amount": "1200.00",
        "interest_category_id": str(book.interest.id),
    }
    created = await client.post("/api/recurring-transactions", json=body, headers=auth_headers)
    assert created.status_code == 201, created.text
    data = created.json()
    assert data["kind"] == "loan_payment"
    assert data["transfer_account_id"] == str(book.loan.id)
    assert Decimal(data["interest_amount"]) == INTEREST
    assert data["interest_category_id"] == str(book.interest.id)
    assert data["category_id"] is None

    refused = await client.post(
        "/api/recurring-transactions", json={**body, "interest_amount": "2500.00"}, headers=auth_headers
    )
    assert refused.status_code == 400


# ---------------------------------------------------------------------------
# the loan and the interest category going away
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deleting_the_loan_deletes_its_schedules(session, book):
    rec = await _schedule(session, book)
    rec_id = rec.id

    assert await account_service.delete_account(session, book.loan.id, book.workspace.id) is True
    assert await _fresh(session, RecurringTransaction, rec_id) is None


@pytest.mark.asyncio
async def test_interest_category_counts_as_used_and_moves_with_a_merge(session, book):
    rec = await _schedule(session, book)
    usage = await category_service.get_category_usage(session, book.workspace.id, book.interest.id)
    assert usage.recurring_transactions == 1

    other = await _category(session, book.workspace, book.user, "Mortgage interest")
    await category_service.delete_category(
        session, book.interest.id, book.workspace.id, transfer_to_id=other.id
    )
    refreshed = await _fresh(session, RecurringTransaction, rec.id)
    assert refreshed is not None and refreshed.interest_category_id == other.id
