"""Fork: the Forecast page as a household plan. Expected transfers between
own accounts, plan recurring items (the plan's repeating payments), the account view and the
chosen canonical budgets. Every figure and name is made up.

Runs on SQLite here and again on PostgreSQL from test_forecast_postgres.py.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.app_clock import app_today
from app.models.transaction import Transaction
from app.schemas.forecast import ForecastBudgetChoices
from app.services import forecast_service
from app.services.report_service import _add_months, get_cash_flow_report
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


# ---------------------------------------------------------------------------
# 2. plan recurring items (the plan's repeating payments)
# ---------------------------------------------------------------------------


async def _plan(session, user, workspace, account_id, start, end, amount="100", **values):
    """A Forecast plan item: a weekly recurring item with ``plan`` set,
    made through the recurring service as the API makes it."""
    from app.schemas.recurring_transaction import RecurringTransactionCreate
    from app.services.recurring_transaction_service import create_recurring_transaction

    data = RecurringTransactionCreate(
        description=values.pop("description", "Allowance"), amount=Decimal(amount), currency="BRL",
        type=values.pop("type", "debit"), frequency=values.pop("frequency", "weekly"), start_date=start,
        end_date=end, account_id=account_id, plan=True, **values,
    )
    return await create_recurring_transaction(session, workspace.id, user.id, data)


async def _linked(session, rec_id) -> list[uuid.UUID]:
    from sqlalchemy import select

    rows = await session.execute(
        select(Transaction.id).where(Transaction.recurring_transaction_id == rec_id).order_by(Transaction.date)
    )
    return list(rows.scalars())


async def test_plan_item_projects_each_occurrence(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    first = today + timedelta(days=1)
    rec = await _plan(session, test_user, test_workspace, joint.id, first, first + timedelta(days=21))
    assert rec.auto_generate is False and rec.plan is True

    result = await _forecast(session, test_user, test_workspace)

    assert result.summary.end_balance == pytest.approx(600)
    # Listed with the expected payments, not with the recurring payments.
    assert result.recurring_payments == []
    report = await get_cash_flow_report(session, test_workspace.id, test_user.id, months=MONTHS, interval="daily")
    assert report.summary.primary_value == pytest.approx(600)


async def test_each_occurrence_of_a_plan_item_is_linked_on_its_own(session, test_user, test_workspace):
    from app.services.recurring_transaction_service import get_recurring_transactions

    today = app_today()
    joint = await _account(session, test_user.id)
    rec = await _plan(session, test_user, test_workspace, joint.id,
                      today - timedelta(days=14), today + timedelta(days=7))
    first = await _tx(session, test_user.id, joint.id, 100, "debit", today - timedelta(days=13),
                      description="ALLOWANCE")
    second = await _tx(session, test_user.id, joint.id, 100, "debit", today - timedelta(days=7),
                       description="ALLOWANCE")
    # Not it: another direction, and an amount too far off.
    await _tx(session, test_user.id, joint.id, 100, "credit", today - timedelta(days=7))
    await _tx(session, test_user.id, joint.id, 180, "debit", today, description="ALLOWANCE")

    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 2
    await session.commit()
    assert await _linked(session, rec.id) == [first.id, second.id]
    [listed] = await get_recurring_transactions(session, test_workspace.id, plan=True)
    assert (listed.arrived_count, listed.occurrence_count) == (2, 4)
    # Nothing more to link; the next payment still to come projects.
    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 0
    result = await _forecast(session, test_user, test_workspace)
    assert result.summary.end_balance == pytest.approx(1000 - 200 + 100 - 180 - 100)


async def test_a_plan_date_past_when_it_was_entered_may_link_an_older_row(session, test_user, test_workspace):
    """The money for a date already past when the item was entered was in
    the bank before then; a date still to come needs a row stored later."""
    today = app_today()
    joint = await _account(session, test_user.id)
    # Dates today-5 and today+2 (a weekly item links within 3 days).
    rec = await _plan(session, test_user, test_workspace, joint.id,
                      today - timedelta(days=5), today + timedelta(days=2))
    await session.refresh(rec)
    created = rec.created_at
    past = await _tx(session, test_user.id, joint.id, 100, "debit", today - timedelta(days=5))
    past.created_at = created - timedelta(days=4)
    early = await _tx(session, test_user.id, joint.id, 100, "debit", today)
    early.created_at = created - timedelta(hours=1)
    await session.commit()

    assert await forecast_service.link_plan_payments(session, test_workspace.id) == 1
    assert await _linked(session, rec.id) == [past.id]
    early.created_at = created + timedelta(hours=1)
    await session.commit()
    assert await forecast_service.link_plan_payments(session, test_workspace.id) == 1
    assert await _linked(session, rec.id) == [past.id, early.id]


async def test_plan_transfer_between_cash_accounts_leaves_the_line_unchanged(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    rec = await _plan(session, test_user, test_workspace, joint.id,
                      today - timedelta(days=7), today + timedelta(days=14), transfer_account_id=savings.id)

    result = await _forecast(session, test_user, test_workspace)
    assert result.summary.end_balance == pytest.approx(2000)
    # One account's view sees its leg of each payment still to come.
    only_joint = await _forecast(session, test_user, test_workspace, account_ids=[joint.id])
    assert only_joint.summary.end_balance == pytest.approx(1000 - 2 * 100)
    only_savings = await get_cash_flow_report(
        session, test_workspace.id, test_user.id, months=MONTHS, interval="daily", account_ids=[savings.id],
    )
    assert only_savings.summary.primary_value == pytest.approx(1000 + 2 * 100)

    # Only the debit leg of a bank transfer pair into savings settles it.
    await _tx(session, test_user.id, joint.id, 100, "debit", today - timedelta(days=7))
    leg = await _pair(session, test_user.id, joint.id, savings.id, 100, today - timedelta(days=7))
    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 1
    await session.commit()
    assert await _linked(session, rec.id) == [leg.id]


async def test_plan_transfer_into_a_loan_leaves_cash(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    loan = await _account(session, test_user.id, opening=0, type_="loan")
    await _plan(session, test_user, test_workspace, joint.id, today + timedelta(days=1), today + timedelta(days=8),
                transfer_account_id=loan.id)

    result = await _forecast(session, test_user, test_workspace)

    assert result.summary.end_balance == pytest.approx(800)


async def test_api_plan_item(client, auth_headers, session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    start = today - timedelta(days=7)
    # Already in the bank: the first payment, as a transfer pair.
    leg = await _pair(session, test_user.id, joint.id, savings.id, 100, start, description="Allowance")
    body = {
        "description": "Allowance", "amount": "100", "currency": "BRL", "type": "debit", "frequency": "weekly",
        "start_date": str(start), "end_date": str(start + timedelta(days=21)), "account_id": str(joint.id),
        "transfer_account_id": str(savings.id), "plan": True,
    }
    resp = await client.post("/api/recurring-transactions", json=body, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    item = resp.json()
    assert (item["plan"], item["auto_generate"], item["transfer_account_id"]) == (True, False, str(savings.id))
    assert (item["arrived_count"], item["occurrence_count"]) == (1, 4)
    assert await _linked(session, uuid.UUID(item["id"])) == [leg.id]

    # The Recurring page lists the regular payments only.
    regular = (await client.get("/api/recurring-transactions", params={"plan": "false"}, headers=auth_headers)).json()
    plan = (await client.get("/api/recurring-transactions", params={"plan": "true"}, headers=auth_headers)).json()
    assert regular == [] and [i["id"] for i in plan] == [item["id"]]
    assert len((await client.get("/api/recurring-transactions", headers=auth_headers)).json()) == 1

    resp = await client.patch(f"/api/recurring-transactions/{item['id']}",
                              json={"end_date": str(start + timedelta(days=14))}, headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["occurrence_count"] == 3
    resp = await client.patch(f"/api/recurring-transactions/{item['id']}",
                              json={"transfer_account_id": None}, headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["transfer_account_id"] is None

    for payload, message in [
        ({**body, "transfer_account_id": str(joint.id)}, "A transfer needs two different accounts"),
        ({**body, "type": "credit"}, "A transfer is money out of the from-account (type debit)"),
        ({**body, "transfer_account_id": str(uuid.uuid4())}, "Transfer account not found"),
        ({**body, "interest_amount": "5"},
         "Only a loan payment has a loan, an interest amount or an interest category"),
    ]:
        resp = await client.post("/api/recurring-transactions", json={**payload, "description": "Other"},
                                 headers=auth_headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == message

    # A plan item that is not a transfer may still ask to generate rows.
    resp = await client.post("/api/recurring-transactions", json={
        **body, "description": "Pocket money", "transfer_account_id": None, "auto_generate": True,
    }, headers=auth_headers)
    assert resp.status_code == 201 and resp.json()["auto_generate"] is True

    # The forecast API has no repeating expected payments any more.
    resp = await client.post("/api/forecast/items", json={
        "kind": "repeating", "amount": "100", "type": "debit", "frequency": "weekly", "start_date": str(start),
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "An expected payment is a one-off payment"


# ---------------------------------------------------------------------------
# 3. the account view
# ---------------------------------------------------------------------------


async def test_account_view_sees_one_leg_of_an_expected_transfer(session, test_user, test_workspace):
    """The double-counting fix: the all-accounts line is unchanged by a
    transfer between two of its accounts; a one-account view sees its leg."""
    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    before = await _forecast(session, test_user, test_workspace)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("300"), type="debit",
                    date=today + timedelta(days=5), account_id=joint.id, transfer_account_id=savings.id)

    everything = await _forecast(session, test_user, test_workspace)
    assert [p.value for p in everything.base] == [p.value for p in before.base]
    assert set(everything.account_ids) == {joint.id, savings.id}

    only_joint = await _forecast(session, test_user, test_workspace, account_ids=[joint.id])
    assert only_joint.account_ids == [joint.id]
    assert only_joint.starting_balance == pytest.approx(1000)
    assert only_joint.summary.end_balance == pytest.approx(700)
    only_savings = await _forecast(session, test_user, test_workspace, account_ids=[savings.id])
    assert only_savings.summary.end_balance == pytest.approx(1300)


async def test_account_view_history_estimate_and_recurring_keep_to_its_accounts(session, test_user, test_workspace):
    from tests.test_forecast import _recurring, _spending_history

    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    await _spending_history(session, test_user.id, joint.id)
    await _recurring(session, test_user.id, joint.id, 500, "credit", today + timedelta(days=1))

    everything = await _forecast(session, test_user, test_workspace, include_spending=True)
    only_savings = await _forecast(session, test_user, test_workspace, include_spending=True,
                                   account_ids=[savings.id])
    only_joint = await _forecast(session, test_user, test_workspace, include_spending=True, account_ids=[joint.id])

    assert len(everything.spending_estimates) == 1 and everything.recurring_payments
    assert only_savings.spending_estimates == [] and only_savings.recurring_payments == []
    # Savings opened today with 1000 and nothing else moves it.
    assert {p.value for p in only_savings.actuals[:-1]} == {0}
    assert {p.value for p in only_savings.base} == {1000}
    assert only_joint.spending_estimates == everything.spending_estimates
    assert len(only_joint.recurring_payments) == len(everything.recurring_payments)
    # The two views' histories add up to the whole.
    assert [p.value for p in only_joint.actuals] == [
        pytest.approx(e.value - s.value) for e, s in zip(everything.actuals, only_savings.actuals)
    ]
    assert only_joint.actuals[-1].value == only_joint.starting_balance


async def test_account_view_leaves_out_payments_with_no_account(session, test_user, test_workspace):
    today = app_today()
    joint = await _account(session, test_user.id)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
                    date=today + timedelta(days=5))
    await _scenario(
        session, test_user.id, test_workspace.id,
        {"kind": "one_off", "amount": Decimal("50"), "type": "debit", "date": today + timedelta(days=3)},
        {"kind": "one_off", "amount": Decimal("70"), "type": "debit", "date": today + timedelta(days=3),
         "account_id": joint.id},
    )

    everything = await _forecast(session, test_user, test_workspace)
    only_joint = await _forecast(session, test_user, test_workspace, account_ids=[joint.id])

    assert everything.summary.end_balance == pytest.approx(800)
    assert everything.scenarios[0].difference == pytest.approx(-120)
    assert only_joint.summary.end_balance == pytest.approx(1000)
    assert only_joint.scenarios[0].difference == pytest.approx(-70)


async def test_account_view_keeps_to_the_cash_accounts(session, test_user, test_workspace):
    joint = await _account(session, test_user.id)
    loan = await _account(session, test_user.id, opening=0, type_="loan")

    result = await _forecast(session, test_user, test_workspace, account_ids=[joint.id, loan.id])

    assert result.account_ids == [joint.id]
    assert result.starting_balance == pytest.approx(1000)


async def test_api_account_view(client, auth_headers, session, test_user, test_workspace):
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id, opening=250)

    resp = await client.get("/api/forecast", params={"months": 3, "include_spending": "false"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["starting_balance"] == pytest.approx(1250)
    resp = await client.get("/api/forecast", params=[
        ("months", "3"), ("include_spending", "false"), ("account_ids", str(savings.id)),
    ], headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["starting_balance"] == pytest.approx(250)
    assert resp.json()["account_ids"] == [str(savings.id)]
    resp = await client.get("/api/forecast", params=[
        ("months", "3"), ("account_ids", str(savings.id)), ("account_ids", str(joint.id)),
    ], headers=auth_headers)
    assert set(resp.json()["account_ids"]) == {str(savings.id), str(joint.id)}


async def test_account_view_counts_a_loan_change_on_the_account_that_pays_the_loan(
    session, test_user, test_workspace,
):
    from app.models.recurring_transaction import RecurringTransaction
    from tests.test_forecast_loans import REPAYMENT, _base_schedule, _in_horizon, _loan

    today = app_today()
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    first = today + timedelta(days=3)
    session.add(RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=joint.id, transfer_account_id=loan.id,
        kind="loan_payment", description="Home loan", amount=Decimal(str(REPAYMENT)), currency="BRL",
        type="debit", frequency="weekly", start_date=first, next_occurrence=first, is_active=True,
    ))
    await session.commit()
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": loan.id, "change_mode": "amount",
        "amount": Decimal("600"), "start_date": today,
    })
    _, base = _base_schedule()
    weeks = len(_in_horizon(p.date for p in base.periods))

    only_joint = await _forecast(session, test_user, test_workspace, account_ids=[joint.id])
    only_savings = await _forecast(session, test_user, test_workspace, account_ids=[savings.id])

    assert only_joint.scenarios[0].difference == pytest.approx(-(600 - REPAYMENT) * weeks, abs=0.01)
    assert only_savings.scenarios[0].difference == 0
    assert only_savings.summary.end_balance == pytest.approx(1000)
    # The loan's own chart doesn't depend on the view.
    assert only_savings.loans[0].scenarios[0].repayment == 600


# ---------------------------------------------------------------------------
# 4. chosen canonical budgets set the spending estimate
# ---------------------------------------------------------------------------


async def _budget(session, user_id, workspace_id, category_id, amount, month, *, recurring=True, end_month=None,
                  choose=True):
    """A category budget, chosen for the forecast unless ``choose`` is off."""
    from app.models.budget import Budget
    from app.models.forecast import ForecastBudgetChoice

    budget = Budget(
        user_id=user_id, workspace_id=workspace_id, category_id=category_id, amount=Decimal(amount),
        month=month, is_recurring=recurring, end_month=end_month,
    )
    session.add(budget)
    if choose and not await _chosen(session, workspace_id, category_id=category_id):
        session.add(ForecastBudgetChoice(workspace_id=workspace_id, category_id=category_id))
    await session.commit()
    return budget


async def _chosen(session, workspace_id, **target) -> bool:
    from sqlalchemy import select

    from app.models.forecast import ForecastBudgetChoice

    rows = await session.execute(select(ForecastBudgetChoice).filter_by(workspace_id=workspace_id, **target))
    return rows.first() is not None


async def _group(session, user_id, workspace_id, amount, month, *category_ids, recurring=True, end_month=None,
                 choose=True):
    from app.models.budget_group import BudgetGroup, BudgetGroupCategory
    from app.models.forecast import ForecastBudgetChoice

    group = BudgetGroup(
        user_id=user_id, workspace_id=workspace_id, name="Everyday", amount=Decimal(amount), month=month,
        is_recurring=recurring, end_month=end_month,
        members=[BudgetGroupCategory(category_id=c) for c in category_ids],
    )
    session.add(group)
    await session.flush()
    if choose:
        session.add(ForecastBudgetChoice(workspace_id=workspace_id, budget_group_id=group.id))
    await session.commit()
    return group


async def _groceries(session, user_id, account_id):
    """Groceries: 150 on the 2nd of each of the last three full months."""
    from tests.test_forecast import _category

    groceries = await _category(session, user_id, "Groceries")
    this_month = app_today().replace(day=1)
    for back in (1, 2, 3):
        await _tx(session, user_id, account_id, 150, "debit", _add_months(this_month, -back) + timedelta(days=1),
                  category_id=groceries.id)
    return groceries


async def _categories(session, user_id):
    from sqlalchemy import select

    from app.models.category import Category

    return list((await session.execute(select(Category).where(Category.user_id == user_id))).scalars())


def _month_end(d):
    return _add_months(d.replace(day=1), 1) - timedelta(days=1)


async def test_budgets_feed_the_forecast_only_when_chosen(session, test_user, test_workspace):
    from tests.test_forecast import _spending_history

    today = app_today()
    joint = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)
    groceries = await _groceries(session, test_user.id, joint.id)
    without = await _forecast(session, test_user, test_workspace, include_spending=True)
    budget = await _budget(session, test_user.id, test_workspace.id, eating.id, "10", today.replace(day=1),
                           choose=False)
    group = await _group(session, test_user.id, test_workspace.id, "10", today.replace(day=1), groceries.id,
                         choose=False)

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    assert result.summary.end_balance == pytest.approx(without.summary.end_balance)
    assert result.budget_uses == []
    # The override parameter picks them without saving a choice.
    chosen = await _forecast(session, test_user, test_workspace, include_spending=True,
                             budget_choices=ForecastBudgetChoices(category_ids=[budget.category_id],
                                                                  budget_group_ids=[group.id]))
    assert chosen.summary.end_balance > without.summary.end_balance
    assert {u.name for u in chosen.budget_uses} == {"Eating out", "Everyday"}


async def test_a_chosen_budget_replaces_its_estimate_in_the_months_it_covers(session, test_user, test_workspace):
    from tests.test_forecast import _spending_history, _spread_total

    today = app_today()
    this_month = today.replace(day=1)
    joint = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)  # Eating out, 280 a month
    without = await _forecast(session, test_user, test_workspace, include_spending=True)
    off = await _forecast(session, test_user, test_workspace)
    # This month and the next, then no longer.
    await _budget(session, test_user.id, test_workspace.id, eating.id, "100", this_month,
                  end_month=_add_months(this_month, 1))

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    first, last = today + timedelta(days=1), _month_end(_add_months(this_month, 1))
    assert result.summary.end_balance - without.summary.end_balance == pytest.approx(
        _spread_total(280, first, last) - _spread_total(100, first, last), abs=0.05
    )
    # The estimate's list is still the history; with spending off nothing changes.
    assert result.spending_estimates == without.spending_estimates
    assert (await _forecast(session, test_user, test_workspace)).summary.end_balance == off.summary.end_balance
    assert [u.month for u in result.budget_uses] == [m for m in (this_month, _add_months(this_month, 1))
                                                     if _month_end(m) > today]
    assert {(u.amount, u.committed) for u in result.budget_uses} == {(100, 0)}


async def test_a_one_month_budget_wins_over_the_recurring_one(session, test_user, test_workspace):
    from tests.test_forecast import _spending_history, _spread_total

    today = app_today()
    this_month = today.replace(day=1)
    joint = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)
    without = await _forecast(session, test_user, test_workspace, include_spending=True)
    next_month = _add_months(this_month, 1)
    await _budget(session, test_user.id, test_workspace.id, eating.id, "280", this_month)
    await _budget(session, test_user.id, test_workspace.id, eating.id, "0", next_month, recurring=False)

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    assert result.summary.end_balance - without.summary.end_balance == pytest.approx(
        _spread_total(280, next_month, _month_end(next_month)), abs=0.05
    )


async def test_a_recurring_payment_in_a_budgeted_category_counts_toward_the_budget(
    session, test_user, test_workspace,
):
    """Rent has no estimate; a recurring 1200 pays it from next month. A 1000
    budget for it adds only this month's share: from next month the recurring
    payment already takes more than the budget, and is not added on top."""
    from tests.test_forecast import _spending_history, _spread_total

    today = app_today()
    this_month = today.replace(day=1)
    joint = await _account(session, test_user.id)
    await _spending_history(session, test_user.id, joint.id)
    rent = next(c for c in await _categories(session, test_user.id) if c.name == "Rent")
    without = await _forecast(session, test_user, test_workspace, include_spending=True)
    await _budget(session, test_user.id, test_workspace.id, rent.id, "1000", this_month)

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    assert result.summary.end_balance - without.summary.end_balance == pytest.approx(
        -_spread_total(1000, today + timedelta(days=1), _month_end(today)), abs=0.05
    )
    later = [u for u in result.budget_uses if u.month > this_month and _month_end(u.month) <= result.end_date]
    assert later and all(u.committed == 1200 and u.remainder == 0 for u in later)


async def test_an_expected_payment_in_a_budgeted_category_reduces_the_remainder(session, test_user, test_workspace):
    from tests.test_forecast import _expected, _spending_history

    today = app_today()
    this_month = today.replace(day=1)
    next_month = _add_months(this_month, 1)
    joint = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)
    await _budget(session, test_user.id, test_workspace.id, eating.id, "300", next_month,
                  recurring=False)
    before = await _forecast(session, test_user, test_workspace, include_spending=True)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("120"), type="debit",
                    date=next_month + timedelta(days=9), account_id=joint.id, category_id=eating.id)

    after = await _forecast(session, test_user, test_workspace, include_spending=True)

    # The month still spends 300 in Eating out: the payment is part of it.
    assert after.summary.end_balance == pytest.approx(before.summary.end_balance, abs=0.05)
    use = next(u for u in after.budget_uses if u.month == next_month)
    assert (use.committed, use.remainder) == (120, 180)


async def test_payments_outside_chosen_budgets_stay_additive(session, test_user, test_workspace):
    from tests.test_forecast import _recurring, _spending_history

    today = app_today()
    this_month = today.replace(day=1)
    joint = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)
    groceries = await _groceries(session, test_user.id, joint.id)
    await _budget(session, test_user.id, test_workspace.id, eating.id, "100", this_month)
    # A Groceries budget that is not chosen changes nothing either.
    await _budget(session, test_user.id, test_workspace.id, groceries.id, "1", this_month, choose=False)
    before = await _forecast(session, test_user, test_workspace, include_spending=True)
    first = _add_months(this_month, 1) + timedelta(days=4)
    await _recurring(session, test_user.id, joint.id, 40, "debit", first, category_id=groceries.id)

    after = await _forecast(session, test_user, test_workspace, include_spending=True)

    # Groceries has an active recurring debit now, so its estimate goes (as
    # without budgets) and the payments add on top.
    from tests.test_forecast import _count, _spread_total

    end = _add_months(today, MONTHS)
    assert after.summary.end_balance - before.summary.end_balance == pytest.approx(
        -40 * _count(first) + _spread_total(150, today + timedelta(days=1), end), abs=0.05
    )


async def test_a_chosen_group_scales_its_members_to_the_cap(session, test_user, test_workspace):
    from tests.test_forecast import _spending_history, _spread_total

    today = app_today()
    joint = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)  # 280
    groceries = await _groceries(session, test_user.id, joint.id)  # 150
    without = await _forecast(session, test_user, test_workspace, include_spending=True)
    group = await _group(session, test_user.id, test_workspace.id, "215", today.replace(day=1), eating.id,
                         groceries.id)

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    first, end = today + timedelta(days=1), _add_months(today, MONTHS)
    assert result.summary.end_balance - without.summary.end_balance == pytest.approx(
        _spread_total(430, first, end) - _spread_total(215, first, end), abs=0.05
    )
    # Each keeps its share of the estimates, 280:150, halved.
    from app.services.budget_service import load_budget_plan

    budgets, groups = await load_budget_plan(session, test_workspace.id)
    spending, budgeted, _ = forecast_service._budgeted_spending(
        [{"category_id": eating.id, "monthly_amount": 280}, {"category_id": groceries.id, "monthly_amount": 150}],
        budgets, groups, set(), {group.id}, today, end, {}, False,
    )
    per_cat = {eating.id: 0.0, groceries.id: 0.0}
    for cat, _, amount in spending:
        per_cat[cat] += amount
    assert per_cat[eating.id] == pytest.approx(_spread_total(140, first, end), abs=0.05)
    assert per_cat[groceries.id] == pytest.approx(_spread_total(75, first, end), abs=0.05)
    assert budgeted(first, eating.id) and not budgeted(first, None)


async def test_a_group_counts_its_members_own_budgets_and_payments(session, test_user, test_workspace):
    from tests.test_forecast import _spending_history, _spread_total

    today = app_today()
    this_month = today.replace(day=1)
    end = _add_months(today, MONTHS)
    joint = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)
    groceries = await _groceries(session, test_user.id, joint.id)
    rent = next(c for c in await _categories(session, test_user.id) if c.name == "Rent")
    without = await _forecast(session, test_user, test_workspace, include_spending=True)
    await _budget(session, test_user.id, test_workspace.id, eating.id, "200", this_month)
    await _group(session, test_user.id, test_workspace.id, "300", this_month, eating.id, groceries.id)

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    # Eating out 200 (its own budget), Groceries the 100 left of the cap.
    first = today + timedelta(days=1)
    assert result.summary.end_balance - without.summary.end_balance == pytest.approx(
        _spread_total(430, first, end) - _spread_total(300, first, end), abs=0.05
    )

    # Rent joins the group: its recurring 1200 a month takes the whole cap.
    from app.services.budget_service import load_budget_plan

    from types import SimpleNamespace

    budgets, groups = await load_budget_plan(session, test_workspace.id)
    widened = SimpleNamespace(
        id=groups[0].id, amount=groups[0].amount, category_ids=[eating.id, groceries.id, rent.id],
        applies_to=groups[0].applies_to,
    )
    month = _add_months(this_month, 1)
    spending, _, uses = forecast_service._budgeted_spending(
        [{"category_id": eating.id, "monthly_amount": 280}, {"category_id": groceries.id, "monthly_amount": 150}],
        budgets, [widened], {eating.id}, {widened.id}, today, end, {(rent.id, month): 1200.0}, False,
    )
    assert not [a for c, d, a in spending if c == groceries.id and d.replace(day=1) == month]
    group_use = next(u for u in uses if u.budget_group_id and u.month == month)
    assert (group_use.committed, group_use.remainder) == (1200, 0)


async def test_a_chosen_group_with_no_estimates_spreads_its_cap_evenly(session, test_user, test_workspace):
    from tests.test_forecast import _category

    today = app_today()
    await _account(session, test_user.id)
    gifts = await _category(session, test_user.id, "Gifts")
    hobbies = await _category(session, test_user.id, "Hobbies")
    without = await _forecast(session, test_user, test_workspace, include_spending=True)
    # One month only: next month.
    next_month = _add_months(today.replace(day=1), 1)
    group = await _group(session, test_user.id, test_workspace.id, "90", next_month, gifts.id, hobbies.id,
                         recurring=False)

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    assert result.summary.end_balance - without.summary.end_balance == pytest.approx(-90, abs=0.05)
    from app.services.budget_service import load_budget_plan

    budgets, groups = await load_budget_plan(session, test_workspace.id)
    spending, _, _ = forecast_service._budgeted_spending(
        [], budgets, groups, set(), {group.id}, today, _add_months(today, MONTHS), {}, False,
    )
    assert sum(a for c, _, a in spending if c == gifts.id) == pytest.approx(45)
    assert {d.replace(day=1) for _, d, _ in spending} == {next_month}


async def test_a_chosen_budget_switches_off_a_spending_change_in_its_months(session, test_user, test_workspace):
    from tests.test_forecast import _spending_history, _spread_total

    today = app_today()
    this_month = today.replace(day=1)
    joint = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "spending_change", "category_id": eating.id, "change_mode": "percent",
        "percent": Decimal("-50"), "start_date": today + timedelta(days=1),
    })
    await _budget(session, test_user.id, test_workspace.id, eating.id, "100", this_month, end_month=this_month)

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    end = _add_months(today, MONTHS)
    next_month = _add_months(this_month, 1)
    assert result.scenarios[0].difference == pytest.approx(_spread_total(140, next_month, end), abs=0.05)


async def test_an_account_view_budgets_only_the_categories_it_spends_in(session, test_user, test_workspace):
    from tests.test_forecast import _category, _spending_history, _spread_total

    today = app_today()
    this_month = today.replace(day=1)
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)
    gifts = await _category(session, test_user.id, "Gifts")
    before_joint = await _forecast(session, test_user, test_workspace, include_spending=True, account_ids=[joint.id])
    await _budget(session, test_user.id, test_workspace.id, eating.id, "100", this_month)
    await _budget(session, test_user.id, test_workspace.id, gifts.id, "50", this_month)

    only_joint = await _forecast(session, test_user, test_workspace, include_spending=True, account_ids=[joint.id])
    only_savings = await _forecast(session, test_user, test_workspace, include_spending=True,
                                   account_ids=[savings.id])
    everything = await _forecast(session, test_user, test_workspace, include_spending=True)

    first, end = today + timedelta(days=1), _add_months(today, MONTHS)
    assert only_joint.summary.end_balance - before_joint.summary.end_balance == pytest.approx(
        _spread_total(280, first, end) - _spread_total(100, first, end), abs=0.05
    )
    assert only_savings.summary.end_balance == pytest.approx(1000)
    # All accounts: Gifts, with no history, is budgeted too.
    assert everything.summary.end_balance == pytest.approx(
        only_joint.summary.end_balance + 1000 - _spread_total(50, first, end), abs=0.05
    )


async def test_an_account_view_takes_its_share_of_a_household_budget(session, test_user, test_workspace):
    """A budget is the household's: each account view projects the part of it
    that matches the share of the category's spending its accounts carry."""
    from tests.test_forecast import _spending_history, _spread_total, _tx

    today = app_today()
    this_month = today.replace(day=1)
    joint = await _account(session, test_user.id)
    card = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, joint.id)  # 280 a month on the joint account
    for back in (1, 2, 3):
        await _tx(session, test_user.id, card.id, 280, "debit", _add_months(this_month, -back), category_id=eating.id)
    before_joint = await _forecast(session, test_user, test_workspace, include_spending=True, account_ids=[joint.id])
    before_card = await _forecast(session, test_user, test_workspace, include_spending=True, account_ids=[card.id])
    await _budget(session, test_user.id, test_workspace.id, eating.id, "100", this_month)

    joint_view = await _forecast(session, test_user, test_workspace, include_spending=True, account_ids=[joint.id])
    card_view = await _forecast(session, test_user, test_workspace, include_spending=True, account_ids=[card.id])

    first, end = today + timedelta(days=1), _add_months(today, MONTHS)
    # Each carries half of the household's Eating out, so half of its 100 budget.
    for before, after in ((before_joint, joint_view), (before_card, card_view)):
        assert after.summary.end_balance - before.summary.end_balance == pytest.approx(
            _spread_total(280, first, end) - _spread_total(50, first, end), abs=0.05
        )


async def test_api_budget_choices(client, auth_headers, session, test_user, test_workspace):
    from tests.test_forecast import _category

    eating = await _category(session, test_user.id, "Eating out")
    gifts = await _category(session, test_user.id, "Gifts")
    await _budget(session, test_user.id, test_workspace.id, eating.id, "100", app_today().replace(day=1),
                  choose=False)
    group = await _group(session, test_user.id, test_workspace.id, "50", app_today().replace(day=1), gifts.id,
                         choose=False)

    resp = await client.get("/api/forecast/budget-choices", headers=auth_headers)
    assert resp.status_code == 200 and resp.json() == {"category_ids": [], "budget_group_ids": []}
    body = {"category_ids": [str(eating.id)], "budget_group_ids": [str(group.id)]}
    resp = await client.put("/api/forecast/budget-choices", json=body, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == body
    assert (await client.get("/api/forecast/budget-choices", headers=auth_headers)).json() == body
    # A category with no budget, or another workspace's group, is refused.
    for payload, message in [
        ({"category_ids": [str(gifts.id)]}, "Budget not found"),
        ({"budget_group_ids": [str(uuid.uuid4())]}, "Budget group not found"),
    ]:
        resp = await client.put("/api/forecast/budget-choices", json=payload, headers=auth_headers)
        assert resp.status_code == 400 and resp.json()["detail"] == message
    resp = await client.put("/api/forecast/budget-choices", json={}, headers=auth_headers)
    assert resp.json() == {"category_ids": [], "budget_group_ids": []}


async def test_api_has_no_budget_kind(client, auth_headers):
    resp = await client.post("/api/forecast/items", json={
        "kind": "budget", "amount": "450", "frequency": "weekly", "start_date": "2030-01-01",
    }, headers=auth_headers)
    assert resp.status_code == 422
