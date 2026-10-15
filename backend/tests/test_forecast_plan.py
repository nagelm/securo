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
from app.models.forecast import ForecastItem
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
    assert await forecast_service.pending_expected_matches(session, test_workspace.id) == {(item.id, None)}
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


# ---------------------------------------------------------------------------
# 2. repeating expected payments
# ---------------------------------------------------------------------------


async def _series(session, user_id, workspace_id, account_id, start, end, amount="100", **values):
    values.setdefault("type", "debit")
    values.setdefault("description", "Allowance")
    item = ForecastItem(
        user_id=user_id, workspace_id=workspace_id, kind="repeating", currency="BRL", frequency="weekly",
        amount=Decimal(amount), start_date=start, end_date=end, account_id=account_id, **values,
    )
    session.add(item)
    await session.commit()
    return item


async def test_repeating_expected_payment_projects_each_occurrence(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    first = today + timedelta(days=1)
    await _series(session, test_user.id, test_workspace.id, joint.id, first, first + timedelta(days=21))

    result = await _forecast(session, test_user, test_workspace)

    assert result.summary.end_balance == pytest.approx(600)
    report = await get_cash_flow_report(session, test_workspace.id, test_user.id, months=MONTHS, interval="daily")
    assert report.summary.primary_value == pytest.approx(600)


async def test_overdue_occurrences_are_still_expected_tomorrow(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    await _series(session, test_user.id, test_workspace.id, joint.id,
                  today - timedelta(days=14), today + timedelta(days=7))

    result = await _forecast(session, test_user, test_workspace)

    # today-14, today-7 and today land tomorrow; today+7 on its date.
    assert result.summary.end_balance == pytest.approx(600)
    assert result.summary.lowest_date == today + timedelta(days=7)


async def test_each_occurrence_is_matched_on_its_own(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    item = await _series(session, test_user.id, test_workspace.id, joint.id,
                         today - timedelta(days=14), today + timedelta(days=7))
    first = await _tx(session, test_user.id, joint.id, 100, "debit", today - timedelta(days=13),
                      description="ALLOWANCE")
    second = await _tx(session, test_user.id, joint.id, 100, "debit", today - timedelta(days=7),
                       description="ALLOWANCE")

    # Viewing leaves the arrived occurrences out without saving anything.
    viewed = await _forecast(session, test_user, test_workspace)
    assert viewed.summary.end_balance == pytest.approx(1000 - 200 - 200)
    assert await forecast_service.pending_expected_matches(session, test_workspace.id) == {
        (item.id, today - timedelta(days=14)), (item.id, today - timedelta(days=7)),
    }

    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 2
    await session.commit()
    await session.refresh(item, ["matches"])
    assert [(m.occurrence_date, m.transaction_id) for m in item.matches] == [
        (today - timedelta(days=14), first.id), (today - timedelta(days=7), second.id),
    ]
    assert item.occurrence_count == 4
    # Nothing more to match, and the matched occurrences stay out of the line.
    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 0
    result = await _forecast(session, test_user, test_workspace)
    assert result.summary.end_balance == pytest.approx(600)


async def test_a_series_date_past_when_it_was_entered_may_match_an_older_row(session, test_user, test_workspace):
    """The money for a date already past when the series was entered was in
    the bank before then; a date still to come needs a row stored later."""
    today = app_today()
    joint = await _account(session, test_user.id)
    # Dates today-5 and today+2 (a weekly series matches within 3 days).
    item = await _series(session, test_user.id, test_workspace.id, joint.id,
                         today - timedelta(days=5), today + timedelta(days=2))
    await session.refresh(item)
    created = item.created_at
    past = await _tx(session, test_user.id, joint.id, 100, "debit", today - timedelta(days=5))
    past.created_at = created - timedelta(days=4)
    early = await _tx(session, test_user.id, joint.id, 100, "debit", today)
    early.created_at = created - timedelta(hours=1)
    await session.commit()

    assert await forecast_service.pending_expected_matches(session, test_workspace.id) == {
        (item.id, today - timedelta(days=5)),
    }
    early.created_at = created + timedelta(hours=1)
    await session.commit()
    assert await forecast_service.pending_expected_matches(session, test_workspace.id) == {
        (item.id, today - timedelta(days=5)), (item.id, today + timedelta(days=2)),
    }


async def test_repeating_transfer_between_cash_accounts_leaves_the_line_unchanged(
    session, test_user, test_workspace,
):
    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    item = await _series(session, test_user.id, test_workspace.id, joint.id,
                         today - timedelta(days=7), today + timedelta(days=14), transfer_account_id=savings.id)

    result = await _forecast(session, test_user, test_workspace)
    assert result.summary.end_balance == pytest.approx(2000)
    only_joint = await get_cash_flow_report(
        session, test_workspace.id, test_user.id, months=MONTHS, interval="daily", account_ids=[joint.id],
    )
    assert only_joint.summary.primary_value == pytest.approx(1000 - 4 * 100)

    leg = await _pair(session, test_user.id, joint.id, savings.id, 100, today - timedelta(days=7))
    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 1
    await session.commit()
    await session.refresh(item, ["matches"])
    assert [(m.occurrence_date, m.transaction_id) for m in item.matches] == [(today - timedelta(days=7), leg.id)]


async def test_api_repeating_expected_payment(client, auth_headers, session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    start = today - timedelta(days=7)
    resp = await client.post("/api/forecast/items", json={
        "kind": "repeating", "amount": "100", "type": "debit", "frequency": "weekly", "description": "Allowance",
        "start_date": str(start), "end_date": str(start + timedelta(days=21)), "account_id": str(joint.id),
    }, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    item_url = f"/api/forecast/items/{body['id']}"
    assert (body["kind"], body["occurrence_count"], body["matches"]) == ("repeating", 4, [])

    tx = await _tx(session, test_user.id, joint.id, 100, "debit", start, description="Allowance")
    resp = await client.patch(item_url, json={"description": "Allowance"}, headers=auth_headers)
    assert resp.json()["matches"] == []  # the wording isn't a reason to look again
    resp = await client.patch(item_url, json={"end_date": str(start + timedelta(days=14))}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["occurrence_count"] == 3
    assert resp.json()["matches"] == [{"occurrence_date": str(start), "transaction_id": str(tx.id)}]
    resp = await client.patch(item_url, json={"end_date": str(start + timedelta(days=1))}, headers=auth_headers)
    assert resp.json()["matches"] == [{"occurrence_date": str(start), "transaction_id": str(tx.id)}]
    resp = await client.patch(item_url, json={"amount": "250"}, headers=auth_headers)
    assert resp.json()["matches"] == []  # a new amount is a new promise

    resp = await client.get("/api/forecast/items", params={"expected": "true"}, headers=auth_headers)
    assert [(i["id"], i["occurrence_count"]) for i in resp.json()] == [(body["id"], 1)]

    resp = await client.post("/api/forecast/items", json={
        "kind": "repeating", "amount": "100", "type": "debit", "start_date": str(start),
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "Frequency and start date are required"
