"""A row in another currency moves its account in the account's own currency."""
import uuid
from types import SimpleNamespace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services import account_service, dashboard_service
from app.services.fx_rate_service import stamp_primary_amount
from app.services.payee_service import get_payee_summary
from app.services.transaction_calendar_service import _signed_balance_delta_primary
from app.services.transaction_match_service import find_unique_transaction_match

ROW_DATE = date(2026, 3, 10)


@pytest_asyncio.fixture
async def usd_rates(session):
    """USD 1 = BRL 5 = EUR 0.5; test_user's primary currency is the app default."""
    for quote, rate in (("BRL", "5"), ("EUR", "0.5")):
        session.add(
            FxRate(
                id=uuid.uuid4(),
                base_currency="USD",
                quote_currency=quote,
                date=ROW_DATE,
                rate=Decimal(rate),
                source="test",
            )
        )
    await session.commit()


@pytest_asyncio.fixture
async def eur_account(session, test_user, test_workspace):
    account = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Invented euro account",
        type="savings",
        balance=Decimal("0"),
        currency="EUR",
    )
    session.add(account)
    await session.commit()
    return account


def _row(user, account, amount, currency, type_="credit", when=ROW_DATE) -> Transaction:
    return Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description="Invented foreign row",
        amount=Decimal(amount),
        currency=currency,
        date=when,
        type=type_,
        source="manual",
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_stamping_converts_a_foreign_row_to_its_accounts_currency(
    session, test_user, eur_account, usd_rates
):
    row = _row(test_user, eur_account, "100.00", "USD")
    session.add(row)
    await session.flush()
    await stamp_primary_amount(session, test_user.id, row, allow_fetch=False)
    assert row.amount_account == Decimal("50.00")
    assert row.amount_primary == Decimal("500.00")


@pytest.mark.asyncio
async def test_a_same_currency_row_stores_no_account_amount(
    session, test_user, eur_account, usd_rates
):
    row = _row(test_user, eur_account, "100.00", "EUR")
    session.add(row)
    await session.flush()
    await stamp_primary_amount(session, test_user.id, row, allow_fetch=False)
    assert row.amount_account is None


@pytest.mark.asyncio
async def test_account_balance_adds_the_account_currency_not_the_primary_one(
    session, test_user, test_workspace, eur_account, usd_rates
):
    row = _row(test_user, eur_account, "100.00", "USD")
    session.add(row)
    await session.flush()
    await stamp_primary_amount(session, test_user.id, row, allow_fetch=False)
    await session.commit()

    accounts = await account_service.get_accounts(session, test_workspace.id)
    balance = next(a for a in accounts if a["id"] == eur_account.id)["current_balance"]
    assert float(balance) == 50.0


@pytest.mark.asyncio
async def test_calendar_does_not_convert_a_primary_amount_twice(
    session, test_user, eur_account, usd_rates
):
    row = _row(test_user, eur_account, "100.00", "USD")
    session.add(row)
    await session.flush()
    await stamp_primary_amount(session, test_user.id, row, allow_fetch=False)
    await session.commit()
    row.account = eur_account

    delta = await _signed_balance_delta_primary(session, row, "BRL")
    # 100 USD = 50 EUR in the account = 500 BRL, not 500 BRL converted again as euros.
    assert delta == pytest.approx(500.0)


@pytest.mark.asyncio
async def test_signed_amount_helper_uses_the_account_amount(session, test_user, eur_account):
    row = _row(test_user, eur_account, "100.00", "USD", type_="debit")
    row.amount_account = Decimal("50.00")
    row.amount_primary = Decimal("500.00")
    row.account = eur_account
    assert dashboard_service._signed_transaction_amount(row) == -50.0


@pytest.mark.asyncio
async def test_monthly_totals_keep_rows_without_a_stamp(
    session, test_user, test_workspace, test_account
):
    """One stamped row used to make every unstamped row in the month disappear."""
    day = date.today().replace(day=1)
    test_account.workspace_id = test_workspace.id
    stamped = _row(test_user, test_account, "10.00", test_account.currency, "debit", day)
    stamped.amount_primary = Decimal("10.00")
    unstamped = _row(test_user, test_account, "30.00", test_account.currency, "debit", day)
    session.add_all([stamped, unstamped])
    await session.commit()

    summary = await dashboard_service.get_summary(session, test_workspace.id, test_user.id)
    assert summary.monthly_expenses_primary == pytest.approx(40.0)


@pytest.mark.asyncio
async def test_opening_row_is_not_an_expense_in_the_account_summary(
    session, test_user, test_workspace
):
    loan = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="Invented manual loan",
        type="loan",
        balance=Decimal("0"),
        currency="USD",
    )
    session.add(loan)
    today = date.today()
    opening = _row(test_user, loan, "900.00", "USD", "debit", today.replace(day=1))
    opening.source = "opening_balance"
    future = _row(test_user, loan, "900.00", "USD", "debit", today + timedelta(days=40))
    future.source = "opening_balance"
    session.add_all([opening, future])
    await session.commit()

    summary = await account_service.get_account_summary(
        session, loan.id, test_workspace.id, date_to=today + timedelta(days=60)
    )
    assert summary["monthly_expenses"] == 0
    assert summary["projected_expenses"] == 0


@pytest.mark.asyncio
async def test_payee_summary_applies_its_dates_and_skips_ignored_rows(
    session, test_user, test_workspace, test_account
):
    payee = Payee(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Invented shop"
    )
    session.add(payee)
    test_account.workspace_id = test_workspace.id
    inside = _row(test_user, test_account, "10.00", test_account.currency, "debit", date(2026, 3, 1))
    outside = _row(test_user, test_account, "20.00", test_account.currency, "debit", date(2026, 1, 1))
    ignored = _row(test_user, test_account, "40.00", test_account.currency, "debit", date(2026, 3, 2))
    ignored.is_ignored = True
    foreign = _row(test_user, test_account, "5.00", "USD", "debit", date(2026, 3, 3))
    foreign.amount_primary = Decimal("25.00")
    for row in (inside, outside, ignored, foreign):
        row.payee_id = payee.id
    session.add_all([inside, outside, ignored, foreign])
    await session.commit()

    summary = await get_payee_summary(
        session, payee.id, test_workspace.id, date(2026, 2, 1), date(2026, 4, 1)
    )
    assert summary["total_spent"] == Decimal("35.00")
    assert summary["transaction_count"] == 2


@pytest.mark.asyncio
async def test_a_match_among_identical_rows_takes_the_closest_date(session, test_user, test_workspace, test_account):
    far = _row(test_user, test_account, "12.00", test_account.currency, "debit", date(2026, 3, 8))
    near = _row(test_user, test_account, "12.00", test_account.currency, "debit", date(2026, 3, 11))
    far.id = uuid.UUID(int=1)  # the lowest id, which used to win
    near.id = uuid.UUID(int=2)
    far.description = near.description = "INVENTED WEEKLY PAYMENT"
    session.add_all([far, near])
    await session.commit()

    incoming = SimpleNamespace(
        date=date(2026, 3, 10), amount=Decimal("12.00"), type="debit",
        description="INVENTED WEEKLY PAYMENT", payee=None, payee_raw=None,
    )
    match = await find_unique_transaction_match(session, test_account.id, incoming, ["manual"])
    assert match.id == near.id
