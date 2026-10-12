"""Transfer pairing on Akahu/NZ-bank-shaped rows, using the shipped matching
rules (destination named in the description, description similarity) as a
workspace would configure them, and retiring questions that can no longer be
answered once a pair is linked.

Fictional amounts and numbers.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.reconciliation import ReconciliationSuggestion
from app.models.transaction import Transaction
from app.services import reconciliation_rule_service as rule_service
from app.services.transfer_detection_service import (
    NODE,
    detect_transfer_pairs,
    recheck_after_link,
)

DAY = date(2026, 3, 2)

SAME_DESCRIPTION_RULE = {
    "outcome": "link",
    "trigger": "both",
    "when": {
        "different_account": True,
        "amount": {"match": "exact"},
        "date": {"before_days": 2, "after_days": 2},
        "description_similarity": {"min": "0.9"},
        "tie_break": "closest_date",
        "unique_candidate": True,
    },
}


async def _configure(session, workspace_id, user_id) -> None:
    """The workspace setup: named-destination rule on, same-description rule above the default."""
    await rule_service.upsert_override(
        session, workspace_id, user_id, NODE, "destination_named_in_description", {"enabled": True}
    )
    custom = await rule_service.create_custom(
        session, workspace_id, user_id, NODE, "Exact amount, same description", SAME_DESCRIPTION_RULE
    )
    await rule_service.reorder(
        session, workspace_id, user_id, NODE,
        ["destination_named_in_description", custom.strategy_id, "exact_amount_nearby", "close_amount_wider_window"],
    )
    await session.commit()


async def _account(session, user_id, name, last4, type_="checking") -> Account:
    acc = Account(
        id=uuid.uuid4(), user_id=user_id, name=name, type=type_,
        balance=Decimal("0"), currency="NZD", masked_number=last4,
    )
    session.add(acc)
    await session.commit()
    return acc


async def _txn(session, user_id, account, amount, kind, text) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account.id,
        description=text, original_description=text, amount=Decimal(str(amount)),
        date=DAY, type=kind, source="sync", currency="NZD", raw_data={"description": text},
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()
    return tx


async def _pending(session) -> list[ReconciliationSuggestion]:
    return list(
        (await session.execute(
            select(ReconciliationSuggestion).where(ReconciliationSuggestion.status == "pending")
        )).scalars().all()
    )


@pytest.mark.asyncio
async def test_payment_naming_the_card_issuer_wins_over_a_transfer_to_another_card(
    session: AsyncSession, test_user, test_workspace
):
    """Example: the Amex credit pairs with the debit that names American Express,
    not with the transfer to the card ending 1234."""
    await _configure(session, test_workspace.id, test_user.id)
    current = await _account(session, test_user.id, "Joint Everyday", "1001")
    amex = await _account(session, test_user.id, "American Express Card", "1000", "credit_card")
    to_visa = await _txn(session, test_user.id, current, 600, "debit", "TRF 400000******1234")
    to_amex = await _txn(session, test_user.id, current, 600, "debit", "PAYMENT AMERICAN EXPRESS CARD")
    amex_credit = await _txn(session, test_user.id, amex, 600, "credit", "PAYMENT - THANK YOU")

    assert await detect_transfer_pairs(session, test_workspace.id) == 1
    await session.commit()
    for tx in (to_visa, to_amex, amex_credit):
        await session.refresh(tx)
    assert to_amex.transfer_pair_id == amex_credit.transfer_pair_id is not None
    assert to_visa.transfer_pair_id is None
    assert await _pending(session) == []


@pytest.mark.asyncio
async def test_same_automatic_payment_text_settles_the_pair(session: AsyncSession, test_user, test_workspace):
    await _configure(session, test_workspace.id, test_user.id)
    current = await _account(session, test_user.id, "Joint Everyday", "1001")
    car = await _account(session, test_user.id, "Car Fund", "1003")
    out_car = await _txn(session, test_user.id, current, 20, "debit", "AP#10000001 TO A SMITH, B JONES Car Fund Weekly")
    await _txn(session, test_user.id, current, 20, "debit", "ATM W/D Example St -12:00")
    in_car = await _txn(session, test_user.id, car, 20, "credit", "AP#10000001 FROM A SMITH, B JONES Car Fund Weekly")

    await detect_transfer_pairs(session, test_workspace.id)
    await session.commit()
    await session.refresh(out_car)
    await session.refresh(in_car)
    assert out_car.transfer_pair_id == in_car.transfer_pair_id is not None


@pytest.mark.asyncio
async def test_linking_one_pair_retires_rivals_and_pairs_the_rest(session: AsyncSession, test_user, test_workspace):
    """Example: two identical payments each way. Linking one pair expires the
    crossing questions, and the remaining two rows link themselves."""
    current = await _account(session, test_user.id, "Current", "1001")
    personal = await _account(session, test_user.id, "Personal", "1002")
    amex = await _account(session, test_user.id, "Card", "1000", "credit_card")
    d1 = await _txn(session, test_user.id, current, 400, "debit", "PAYMENT AMERICAN EXPRESS")
    d2 = await _txn(session, test_user.id, personal, 400, "debit", "AMERICAN EXPRESS CARD PAYMENT")
    c1 = await _txn(session, test_user.id, amex, 400, "credit", "PAYMENT - THANK YOU")
    c2 = await _txn(session, test_user.id, amex, 400, "credit", "PAYMENT - THANK YOU")

    assert await detect_transfer_pairs(session, test_workspace.id) == 0
    await session.commit()
    assert len(await _pending(session)) >= 1

    d1.transfer_pair_id = c1.transfer_pair_id = uuid.uuid4()
    await session.flush()
    created = await recheck_after_link(session, test_workspace.id, {d1.id, c1.id})
    await session.commit()

    await session.refresh(d2)
    await session.refresh(c2)
    assert created == 1
    assert d2.transfer_pair_id == c2.transfer_pair_id is not None
    assert await _pending(session) == []


@pytest.mark.asyncio
async def test_ignored_copy_is_not_a_transfer_candidate(session: AsyncSession, test_user, test_workspace):
    """A duplicate marked Ignored no longer makes its real twin ambiguous."""
    current = await _account(session, test_user.id, "Current", "1001")
    rainy = await _account(session, test_user.id, "Rainy 90 Day", "1004")
    debit = await _txn(session, test_user.id, current, 20, "debit", "AP#10000002 TO B JONES, A SMITH Rainy")
    copy = await _txn(session, test_user.id, current, 20, "debit", "AP#10000002 TO B JONES, A SMITH Rainy")
    credit = await _txn(session, test_user.id, rainy, 20, "credit", "AP#10000002 FROM A SMITH, B JONES Rainy")
    copy.is_ignored = True
    await session.commit()

    assert await detect_transfer_pairs(session, test_workspace.id) == 1
    await session.commit()
    for tx in (debit, copy, credit):
        await session.refresh(tx)
    assert debit.transfer_pair_id == credit.transfer_pair_id is not None
    assert copy.transfer_pair_id is None
    assert await _pending(session) == []
