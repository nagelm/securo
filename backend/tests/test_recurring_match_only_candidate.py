"""The only candidate that fits is linked, whatever the bank called it.

A schedule is named by a person ("Example Cloud (2 TB plan)") and the
charge that pays it is named by a bank (`XYZ*data Store 0800`).
The two share no words, so the description rule never pairs them and the
charge used to land as a second row next to the placeholder written for it.

The rules after the description rule (`placeholder_only_candidate` and
`bill_only_candidate`) link on the account, the exact amount and two days
either side, and only when exactly one placeholder or bill fits.

These drive the real sync path with a mocked provider, as
`test_recurring_match_sync` does, so they cover the wiring in
`connection_service` and not just the matcher.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.schemas.recurring_transaction import RecurringTransactionCreate
from app.services import reconciliation_policy
from app.services import recurring_match_service as rms
from app.services.connection_service import sync_connection
from app.services.recurring_transaction_service import (
    create_recurring_transaction,
    generate_pending,
)
from app.services.text_similarity import token_overlap

SCHEDULE = "Example Cloud (2 TB plan)"
BANK_TEXT = "XYZ*data Store 0800"
#: Shares four of five words with SCHEDULE: enough for the description rule.
CLOSE_TEXT = "EXAMPLE CLOUD 2 TB PLAN"
OTHER_SCHEDULE = "Example Music Family"
AMOUNT = Decimal("12.99")


@pytest_asyncio.fixture
async def conn_account(session: AsyncSession, test_user, test_workspace):
    conn = BankConnection(
        id=uuid.uuid4(), user_id=test_user.id, provider="test",
        external_id=f"ext-{uuid.uuid4().hex[:8]}",
        institution_name="Example Bank", credentials={"token": "fake"},
        status="active", last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, connection_id=conn.id,
        workspace_id=test_workspace.id, name="Everyday", type="checking",
        external_id="acc-ext-1", balance=Decimal("0"), currency="NZD",
    )
    session.add(account)
    await session.commit()
    await session.refresh(conn)
    await session.refresh(account)
    return conn, account


def _provider(transactions):
    from app.providers.base import AccountData

    p = AsyncMock()
    p.refresh_credentials = AsyncMock(return_value={"token": "t"})
    p.get_accounts = AsyncMock(return_value=[
        AccountData(external_id="acc-ext-1", name="Everyday",
                    type="checking", balance=Decimal("0"), currency="NZD"),
    ])
    p.get_transactions = AsyncMock(return_value=transactions)
    return p


def _charge(when: date, description: str = BANK_TEXT, external_id: str = "s1"):
    from app.providers.base import TransactionData

    return TransactionData(
        external_id=external_id, description=description, amount=AMOUNT,
        date=when, currency="NZD", type="debit", status="posted",
    )


async def _run_sync(session, conn_id, test_workspace, test_user, provider):
    with patch(
        "app.services.connection_service.get_provider", return_value=provider
    ), patch(
        "app.services.connection_service.detect_transfer_pairs",
        new_callable=AsyncMock,
    ), patch(
        "app.services.connection_service.stamp_primary_amount",
        new_callable=AsyncMock,
    ), patch(
        "app.services.connection_service.apply_rules_to_transaction",
        new_callable=AsyncMock,
    ):
        return await sync_connection(
            session, conn_id, test_workspace.id, test_user.id
        )


async def _make_bill(session, test_workspace, test_user, account, **ov):
    data = RecurringTransactionCreate(
        description=ov.pop("description", SCHEDULE),
        amount=ov.pop("amount", AMOUNT),
        currency="NZD",
        type="debit",
        frequency=ov.pop("frequency", "monthly"),
        start_date=ov.pop("start_date", date(2025, 1, 10)),
        account_id=account.id,
        **ov,
    )
    return await create_recurring_transaction(
        session, test_workspace.id, test_user.id, data
    )


async def _rows(session, account_id):
    r = await session.execute(
        select(Transaction).where(
            Transaction.account_id == account_id,
            Transaction.source != "opening_balance",
        )
    )
    return list(r.scalars().all())


def test_the_wording_really_does_not_match():
    """The premise: the description rule cannot pair these on its own."""
    assert token_overlap(BANK_TEXT, SCHEDULE) < 0.6
    assert 0.6 <= token_overlap(CLOSE_TEXT, SCHEDULE) < 1.0


def test_the_new_rules_come_after_the_description_rules():
    placeholder = reconciliation_policy.default_policy(
        reconciliation_policy.MATCH_PLACEHOLDER["node"]
    )
    assert [s["id"] for s in placeholder["strategies"]] == [
        "placeholder_same_account_exact",
        "placeholder_only_candidate",
        "placeholder_same_payee_varying_amount",
    ]
    only = placeholder["strategies"][1]["when"]
    assert only["date"] == {"before_days": 2, "after_days": 2}
    assert only["unique_candidate"] is True
    assert "description_similarity" not in only

    recurring = reconciliation_policy.default_policy(
        reconciliation_policy.MATCH_RECURRING["node"]
    )
    assert [s["id"] for s in recurring["strategies"]] == [
        "same_account_exact",
        "bill_only_candidate",
        "same_payee_varying_amount",
    ]
    assert "description_similarity" not in recurring["strategies"][1]["when"]


@pytest.mark.parametrize("frequency", ["weekly", "monthly", "yearly"])
def test_the_bill_rule_keeps_two_days_at_every_frequency(frequency):
    """Two days is already the weekly window, so narrowing for a weekly
    bill leaves it alone and nothing widens it for the others."""
    rule = reconciliation_policy.for_recurring(frequency)["strategies"][1]["when"]
    assert rule["date"] == {"before_days": 2, "after_days": 2}


# ---------------------------------------------------------------------------
# A placeholder is already standing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_only_placeholder_is_upgraded_in_place(
    session, test_user, test_workspace, conn_account
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account)
    bill_id = bill.id
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1
    placeholder_id = (await session.execute(
        select(Transaction.id).where(Transaction.source == "recurring")
    )).scalar_one()

    await _run_sync(
        session, conn_id, test_workspace, test_user,
        _provider([_charge(date(2025, 1, 12))]),
    )

    rows = await _rows(session, account_id)
    assert len(rows) == 1
    merged = rows[0]
    assert merged.id == placeholder_id
    assert merged.external_id == "s1"
    assert merged.source == "sync"
    assert merged.recurring_transaction_id == bill_id
    assert merged.original_description == BANK_TEXT


@pytest.mark.asyncio
async def test_two_placeholders_of_that_amount_link_neither(
    session, test_user, test_workspace, conn_account
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    await _make_bill(session, test_workspace, test_user, account)
    await _make_bill(
        session, test_workspace, test_user, account, description=OTHER_SCHEDULE
    )
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 2

    await _run_sync(
        session, conn_id, test_workspace, test_user,
        _provider([_charge(date(2025, 1, 11))]),
    )

    rows = await _rows(session, account_id)
    assert len(rows) == 3
    placeholders = [r for r in rows if r.source == "recurring"]
    assert len(placeholders) == 2
    assert all(p.external_id is None for p in placeholders)
    synced = [r for r in rows if r.source == "sync"]
    assert len(synced) == 1
    assert synced[0].recurring_transaction_id is None


@pytest.mark.asyncio
async def test_the_description_rule_still_wins_over_an_ambiguous_pair(
    session, test_user, test_workspace, conn_account
):
    """Two placeholders of one amount, one of them named like the charge.
    The description rule runs first and picks it; the wording-blind rule,
    which would call the pair ambiguous, never gets asked.

    The wording is close rather than identical (0.8), so a matcher that
    ranked by score alone would prefer the wording-blind rule's flat 1.0
    on the other placeholder."""
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account)
    bill_id = bill.id
    await _make_bill(
        session, test_workspace, test_user, account, description=OTHER_SCHEDULE
    )
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 2

    await _run_sync(
        session, conn_id, test_workspace, test_user,
        _provider([_charge(date(2025, 1, 11), description=CLOSE_TEXT)]),
    )

    rows = await _rows(session, account_id)
    assert len(rows) == 2
    merged = [r for r in rows if r.external_id == "s1"]
    assert len(merged) == 1
    assert merged[0].recurring_transaction_id == bill_id


@pytest.mark.asyncio
async def test_three_days_apart_needs_the_wording(
    session, test_user, test_workspace, conn_account
):
    """Outside two days only the description rule reaches, so a charge
    worded differently stays a row of its own."""
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    await _make_bill(session, test_workspace, test_user, account)
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1

    await _run_sync(
        session, conn_id, test_workspace, test_user,
        _provider([_charge(date(2025, 1, 13))]),
    )

    rows = await _rows(session, account_id)
    assert len(rows) == 2
    assert [r.external_id for r in rows if r.source == "recurring"] == [None]


@pytest.mark.asyncio
async def test_three_days_apart_with_the_wording_still_links(
    session, test_user, test_workspace, conn_account
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account)
    bill_id = bill.id
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1

    await _run_sync(
        session, conn_id, test_workspace, test_user,
        _provider([_charge(date(2025, 1, 13), description=SCHEDULE.upper())]),
    )

    rows = await _rows(session, account_id)
    assert len(rows) == 1
    assert rows[0].external_id == "s1"
    assert rows[0].recurring_transaction_id == bill_id


@pytest.mark.asyncio
async def test_the_placeholder_matcher_directly(
    session, test_user, test_workspace, conn_account
):
    """The same three cases against `find_placeholder_for_incoming` alone,
    without the sync around it."""
    _, account = conn_account
    await _make_bill(session, test_workspace, test_user, account)
    assert await generate_pending(session, test_user.id, up_to=date(2025, 1, 10)) == 1

    async def find(when):
        return await rms.find_placeholder_for_incoming(
            session, account.id, AMOUNT, "NZD", "debit", when, BANK_TEXT
        )

    assert await find(date(2025, 1, 8)) is not None
    assert await find(date(2025, 1, 12)) is not None
    assert await find(date(2025, 1, 7)) is None
    assert await find(date(2025, 1, 13)) is None


# ---------------------------------------------------------------------------
# The charge arrives before any placeholder was written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_only_bill_is_linked_and_advanced(
    session, test_user, test_workspace, conn_account
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    bill = await _make_bill(session, test_workspace, test_user, account)
    bill_id = bill.id

    await _run_sync(
        session, conn_id, test_workspace, test_user,
        _provider([_charge(date(2025, 1, 11))]),
    )

    rows = await _rows(session, account_id)
    assert len(rows) == 1
    assert rows[0].recurring_transaction_id == bill_id
    refreshed = await session.get(RecurringTransaction, bill_id)
    assert refreshed.next_occurrence == date(2025, 2, 10)

    # Generating past the fulfilled occurrence writes nothing new.
    await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))
    assert len(await _rows(session, account_id)) == 1


@pytest.mark.asyncio
async def test_two_bills_of_that_amount_link_neither(
    session, test_user, test_workspace, conn_account
):
    conn, account = conn_account
    conn_id, account_id = conn.id, account.id
    first = await _make_bill(session, test_workspace, test_user, account)
    second = await _make_bill(
        session, test_workspace, test_user, account, description=OTHER_SCHEDULE
    )
    first_id, second_id = first.id, second.id

    await _run_sync(
        session, conn_id, test_workspace, test_user,
        _provider([_charge(date(2025, 1, 10))]),
    )

    rows = await _rows(session, account_id)
    assert len(rows) == 1
    assert rows[0].recurring_transaction_id is None
    for bill_id in (first_id, second_id):
        refreshed = await session.get(RecurringTransaction, bill_id)
        assert refreshed.next_occurrence == date(2025, 1, 10)


@pytest.mark.asyncio
async def test_a_bill_three_days_out_needs_the_wording(
    session, test_user, test_workspace, conn_account
):
    _, account = conn_account
    bill = await _make_bill(session, test_workspace, test_user, account)

    async def find(when, description=BANK_TEXT):
        return await rms.find_bill_for_incoming(
            session, test_user.id, account.id, AMOUNT, "NZD", "debit",
            when, description,
        )

    assert (await find(date(2025, 1, 8))).id == bill.id
    assert await find(date(2025, 1, 13)) is None
    # The description rule's own window is unchanged: three days late with
    # the schedule's wording still links.
    assert (await find(date(2025, 1, 13), SCHEDULE.upper())).id == bill.id


@pytest.mark.asyncio
async def test_a_weekly_bill_keeps_its_two_day_window(
    session, test_user, test_workspace, conn_account
):
    _, account = conn_account
    bill = await _make_bill(
        session, test_workspace, test_user, account,
        frequency="weekly", start_date=date(2025, 1, 6),
    )

    async def find(when):
        return await rms.find_bill_for_incoming(
            session, test_user.id, account.id, AMOUNT, "NZD", "debit",
            when, BANK_TEXT,
        )

    assert (await find(date(2025, 1, 8))).id == bill.id
    assert await find(date(2025, 1, 9)) is None


@pytest.mark.asyncio
async def test_generation_claims_the_only_real_charge(
    session, test_user, test_workspace, conn_account
):
    """The same rule from the other side: a charge already on the account
    when the occurrence is generated is linked instead of being joined by
    a placeholder, when it is the only one that fits."""
    _, account = conn_account
    account_id = account.id
    session.add(Transaction(
        user_id=test_user.id, workspace_id=test_workspace.id,
        account_id=account_id, description=BANK_TEXT, amount=AMOUNT,
        currency="NZD", date=date(2025, 1, 11), type="debit",
        source="sync", status="posted", external_id="bank-1",
    ))
    await session.commit()
    bill = await _make_bill(session, test_workspace, test_user, account)
    bill_id = bill.id

    await generate_pending(session, test_user.id, up_to=date(2025, 1, 20))

    rows = await _rows(session, account_id)
    assert len(rows) == 1
    assert rows[0].recurring_transaction_id == bill_id
