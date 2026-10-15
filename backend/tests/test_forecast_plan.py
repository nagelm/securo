"""Fork: the Forecast page as a household plan. Expected transfers between
own accounts, repeating expected payments, the account view and plan
budgets. Every figure and name is made up.

Runs on SQLite here and again on PostgreSQL from test_forecast_postgres.py.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.services import forecast_service
from app.services.report_service import get_cash_flow_report
from tests.test_forecast import MONTHS, _account, _expected, _forecast, _scenario, _tx

pytestmark = pytest.mark.asyncio


async def _pair(session, user_id, from_id, to_id, amount: float, when, description: str = "Transfer") -> Transaction:
    """A bank transfer: a debit on ``from_id`` and a credit on ``to_id``
    sharing one transfer_pair_id. Returns the debit leg."""
    pair_id = uuid.uuid4()
    out = await _tx(session, user_id, from_id, amount, "debit", when, description=description)
    into = await _tx(session, user_id, to_id, amount, "credit", when, description=description)
    out.transfer_pair_id = pair_id
    into.transfer_pair_id = pair_id
    await session.commit()
    return out


# ---------------------------------------------------------------------------
# 1. expected transfers
# ---------------------------------------------------------------------------


async def test_expected_transfer_between_cash_accounts_is_not_counted_twice(session, test_user, test_workspace):
    """Both legs in scope cancel in the all-accounts line; a view of one
    account sees its own leg."""
    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("300"), type="debit",
                    date=today + timedelta(days=5), account_id=joint.id, transfer_account_id=savings.id,
                    description="To savings")

    result = await _forecast(session, test_user, test_workspace)
    assert result.starting_balance == pytest.approx(2000)
    assert result.summary.end_balance == pytest.approx(2000)
    assert result.summary.lowest_balance == pytest.approx(2000)

    report = await get_cash_flow_report(session, test_workspace.id, test_user.id, months=MONTHS, interval="daily")
    assert report.summary.primary_value == pytest.approx(2000)
    expenses = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    assert expenses.value == 0

    only_joint = await get_cash_flow_report(
        session, test_workspace.id, test_user.id, months=MONTHS, interval="daily", account_ids=[joint.id],
    )
    assert only_joint.summary.primary_value == pytest.approx(700)
    only_savings = await get_cash_flow_report(
        session, test_workspace.id, test_user.id, months=MONTHS, interval="daily", account_ids=[savings.id],
    )
    assert only_savings.summary.primary_value == pytest.approx(1300)


async def test_expected_transfer_to_a_loan_leaves_cash(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    loan = await _account(session, test_user.id, opening=0, type_="loan")
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("300"), type="debit",
                    date=today + timedelta(days=5), account_id=joint.id, transfer_account_id=loan.id)

    result = await _forecast(session, test_user, test_workspace)

    assert result.summary.end_balance == pytest.approx(700)


async def test_scenario_transfer_counts_its_legs_in_scope(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    loan = await _account(session, test_user.id, opening=0, type_="loan")
    await _scenario(
        session, test_user.id, test_workspace.id,
        {"kind": "one_off", "amount": Decimal("100"), "type": "debit", "date": today + timedelta(days=3),
         "account_id": joint.id, "transfer_account_id": savings.id},
        {"kind": "one_off", "amount": Decimal("250"), "type": "debit", "date": today + timedelta(days=3),
         "account_id": joint.id, "transfer_account_id": loan.id},
    )

    result = await _forecast(session, test_user, test_workspace)

    assert result.scenarios[0].difference == pytest.approx(-250)


async def test_expected_transfer_matches_the_debit_leg_of_a_bank_transfer(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    other = await _account(session, test_user.id)
    item = await _expected(session, test_user.id, test_workspace.id, amount=Decimal("300"), type="debit",
                           date=today - timedelta(days=1), account_id=joint.id, transfer_account_id=savings.id,
                           description="To savings")
    # Not it: a plain payment of the amount, and a transfer into another account.
    await _tx(session, test_user.id, joint.id, 300, "debit", today, description="Transfer")
    await _pair(session, test_user.id, joint.id, other.id, 300, today)
    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 0

    debit_leg = await _pair(session, test_user.id, joint.id, savings.id, 300, today)
    assert await forecast_service.pending_expected_matches(session, test_workspace.id) == {item.id}
    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 1
    await session.refresh(item)
    assert item.matched_transaction_id == debit_leg.id


async def test_api_expected_transfer(client, auth_headers, session, test_user, test_workspace):
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    base = {"kind": "one_off", "amount": "300", "date": "2030-01-01", "account_id": str(joint.id)}

    resp = await client.post("/api/forecast/items", json={**base, "transfer_account_id": str(savings.id)},
                             headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert (body["type"], body["transfer_account_id"]) == ("debit", str(savings.id))

    for payload, message in [
        ({**base, "transfer_account_id": str(joint.id)}, "A transfer needs two different accounts"),
        ({**base, "transfer_account_id": str(savings.id), "type": "credit"},
         "A transfer is money out of the from-account (type debit)"),
        ({**base, "transfer_account_id": str(uuid.uuid4())}, "Transfer account not found"),
        ({**base, "account_id": None, "type": "debit", "transfer_account_id": str(savings.id)},
         "A transfer needs the account it leaves"),
    ]:
        resp = await client.post("/api/forecast/items", json=payload, headers=auth_headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == message
