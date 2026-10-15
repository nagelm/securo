"""Fork: the Forecast page's loans: each loan's projection and the
``loan_change`` scenario kind (new rate, new repayment, lump sum). Every
figure is made up.

Runs on SQLite here and again on PostgreSQL from
test_forecast_loans_postgres.py.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.recurring_transaction import RecurringTransaction
from app.services.loan_projection import loan_terms, project_loan
from app.services.report_service import _add_months, get_cash_flow_report
from tests.test_forecast import MONTHS, _account, _category, _forecast, _scenario, _tx

pytestmark = pytest.mark.asyncio

DEBT = 100_000.0
REPAYMENT = 352.40


def _details(**overrides) -> dict:
    today = app_today()
    details = {
        "type": "TABLE",
        "interest": {"rate": 5.15, "type": "FIXED"},
        "repayment": {
            "frequency": "WEEKLY",
            "next_date": (today + timedelta(days=3)).isoformat(),
            "next_amount": REPAYMENT,
        },
    }
    details.update(overrides)
    return details


async def _loan(session: AsyncSession, user_id, debt: float = DEBT, details: dict | None = None,
                name: str = "Home loan") -> Account:
    loan = await _account(session, user_id, opening=0, type_="loan")
    loan.name = name
    loan.loan_details = _details() if details is None else details
    await session.commit()
    await _tx(session, user_id, loan.id, debt, "debit", app_today() - timedelta(days=30), source="opening_balance")
    return loan


def _base_schedule(debt: float = DEBT, details: dict | None = None):
    terms = loan_terms(_details() if details is None else details, debt, app_today())
    assert terms is not None
    return terms, project_loan(terms)


def _in_horizon(dates) -> list[date]:
    today, end = app_today(), _add_months(app_today(), MONTHS)
    return [d for d in dates if today < d <= end]


# ---------------------------------------------------------------------------
# the base projection
# ---------------------------------------------------------------------------


async def test_each_loan_gets_its_base_projection(session, test_user, test_workspace):
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)

    result = await _forecast(session, test_user, test_workspace)

    assert len(result.loans) == 1
    forecast = result.loans[0]
    _, base = _base_schedule()
    assert forecast.account_id == loan.id
    assert forecast.name == "Home loan"
    assert forecast.balance == pytest.approx(DEBT)
    assert (forecast.rate, forecast.frequency, forecast.missing_details) == (5.15, "WEEKLY", False)
    assert forecast.base is not None
    assert forecast.base.payoff_date == base.payoff_date
    assert forecast.base.total_interest == pytest.approx(base.total_interest)
    assert forecast.base.repayment == REPAYMENT
    points = forecast.base.points
    assert points[0].date == app_today() and points[0].balance == pytest.approx(DEBT)
    in_horizon = [p for p in base.periods if p.date <= _add_months(app_today(), MONTHS)]
    assert points[-1].balance == pytest.approx(in_horizon[-1].closing)
    assert sum(p.interest for p in points) == pytest.approx(sum(p.interest for p in in_horizon), abs=0.01)
    assert sum(p.interest + p.principal for p in points) == pytest.approx(REPAYMENT * len(in_horizon), abs=0.01)
    # The loan's own balance never enters the cash line; with no recurring
    # schedule paying it, its repayments do (owner 2026-10-14).
    assert result.summary.end_balance == pytest.approx(1000 - REPAYMENT * len(in_horizon), abs=0.01)
    assert forecast.scenarios == []


async def test_loans_without_enough_details_are_listed_without_a_projection(session, test_user, test_workspace):
    await _account(session, test_user.id)
    revolving = await _loan(session, test_user.id, debt=5_000, details={"type": "REVOLVING"}, name="Revolving")
    no_rate = await _loan(session, test_user.id, details=_details(interest={}), name="No rate")
    await _loan(session, test_user.id, debt=0, name="Paid off")  # owes nothing: left out
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": revolving.id, "change_mode": "lump_sum",
        "amount": Decimal("1000"), "start_date": app_today() + timedelta(days=5),
    })

    result = await _forecast(session, test_user, test_workspace)

    by_id = {f.account_id: f for f in result.loans}
    assert set(by_id) == {revolving.id, no_rate.id}
    for forecast in by_id.values():
        assert forecast.missing_details is True
        assert forecast.base is None and forecast.scenarios == []
    assert by_id[revolving.id].balance == pytest.approx(5_000)
    # A change on a loan that can't be projected changes nothing.
    assert result.scenarios[0].difference == 0


# ---------------------------------------------------------------------------
# repayments in the base cash line
# ---------------------------------------------------------------------------


async def test_a_loan_no_schedule_pays_takes_its_repayments_from_cash(session, test_user, test_workspace):
    from app.services.category_service import find_default_category
    from app.services.forecast_service import loan_repayment_projections

    today = app_today()
    checking = await _account(session, test_user.id)
    await _loan(session, test_user.id)
    repayments = await _category(session, test_user.id, "Loan repayments", default_key="loan_repayments",
                                 treat_as_transfer=True)

    report = await get_cash_flow_report(session, test_workspace.id, test_user.id, months=MONTHS, interval="daily")

    _, base = _base_schedule()
    dates = _in_horizon(p.date for p in base.periods)
    assert report.summary.primary_value == pytest.approx(1000 - REPAYMENT * len(dates), abs=0.01)
    rows = await loan_repayment_projections(session, test_workspace.id, today, _add_months(today, MONTHS))
    assert [r["date"] for r in rows] == dates
    assert {r["type"] for r in rows} == {"debit"}
    assert await find_default_category(session, test_workspace.id, "loan_repayments") == repayments.id
    assert {r["category_id"] for r in rows} == {repayments.id}
    # A report narrowed to particular accounts leaves them out, like invoices.
    narrowed = await get_cash_flow_report(
        session, test_workspace.id, test_user.id, months=MONTHS, interval="daily", account_ids=[checking.id],
    )
    assert narrowed.summary.primary_value == pytest.approx(1000)


async def test_a_plain_recurring_transfer_into_the_loan_counts_as_its_schedule(session, test_user, test_workspace):
    today = app_today()
    checking = await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    first = today + timedelta(days=3)
    session.add(RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=checking.id, transfer_account_id=loan.id,
        description="To mortgage", amount=Decimal("400"), currency="BRL", type="debit",
        frequency="weekly", start_date=first, next_occurrence=first, is_active=True,
    ))
    await session.commit()
    from app.services.forecast_service import loan_repayment_projections

    assert await loan_repayment_projections(session, test_workspace.id, today, _add_months(today, MONTHS)) == []


async def test_a_planned_repeating_transfer_into_the_loan_replaces_its_repayments(
    session, test_user, test_workspace,
):
    """Fork: over the dates a plan's repeating transfer into the loan covers,
    the loan card's own repayments are not projected as well."""
    from app.models.forecast import ForecastItem
    from app.services.forecast_service import loan_repayment_projections

    today = app_today()
    checking = await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    last = today + timedelta(days=60)
    session.add(ForecastItem(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, kind="repeating",
        description="Plan: loan", amount=Decimal("400"), currency="BRL", type="debit",
        frequency="weekly", start_date=today + timedelta(days=1), end_date=last,
        account_id=checking.id, transfer_account_id=loan.id,
    ))
    await session.commit()

    rows = await loan_repayment_projections(session, test_workspace.id, today, _add_months(today, MONTHS))
    _, base = _base_schedule()
    assert [r["date"] for r in rows] == [d for d in _in_horizon(p.date for p in base.periods) if d > last]


async def test_a_loan_without_details_adds_no_repayments(session, test_user, test_workspace):
    await _account(session, test_user.id)
    await _loan(session, test_user.id, details={"type": "REVOLVING"})
    await _loan(session, test_user.id, details=_details(interest={}), name="No rate")

    result = await _forecast(session, test_user, test_workspace)

    assert result.summary.end_balance == pytest.approx(1000)


async def test_repayments_stop_at_payoff(session, test_user, test_workspace):
    await _account(session, test_user.id)
    await _loan(session, test_user.id, debt=3_000)

    result = await _forecast(session, test_user, test_workspace)

    _, base = _base_schedule(debt=3_000)
    assert base.payoff_date is not None and base.payoff_date <= _add_months(app_today(), MONTHS)
    paid = sum(p.repayment for p in base.periods)
    assert paid == pytest.approx(3_000 + base.total_interest, abs=0.01)
    assert result.summary.end_balance == pytest.approx(1000 - paid, abs=0.01)


# ---------------------------------------------------------------------------
# loan_change, one mode at a time
# ---------------------------------------------------------------------------


async def test_paying_more_a_week_pays_off_earlier(session, test_user, test_workspace):
    today = app_today()
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": loan.id, "change_mode": "amount",
        "amount": Decimal("600"), "start_date": today,
    }, name="Repay more")

    result = await _forecast(session, test_user, test_workspace)

    _, base = _base_schedule()
    line = result.loans[0].scenarios[0]
    assert line.name == "Repay more"
    assert line.repayment == 600
    assert line.payoff_date is not None and base.payoff_date is not None
    assert line.payoff_date < base.payoff_date
    assert line.payoff_change_days == (line.payoff_date - base.payoff_date).days < 0
    assert line.interest_saved == pytest.approx(base.total_interest - line.total_interest)
    assert line.interest_saved > 0
    # Cash: only the extra 600 − 352.40 a week leaves, on the loan's repayment dates.
    weeks = len(_in_horizon(p.date for p in base.periods))
    assert result.scenarios[0].difference == pytest.approx(-(600 - REPAYMENT) * weeks, abs=0.01)


async def test_a_scheduled_loan_payment_is_not_counted_twice(session, test_user, test_workspace):
    """The base cash line holds the recurring loan payment; a scenario that
    repays more only adds the extra, and the base line is unchanged."""
    today = app_today()
    checking = await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    first = today + timedelta(days=3)
    session.add(RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=checking.id, transfer_account_id=loan.id,
        kind="loan_payment", description="Mortgage", amount=Decimal(str(REPAYMENT)), currency="BRL",
        type="debit", frequency="weekly", start_date=first, next_occurrence=first, is_active=True,
    ))
    await session.commit()
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": loan.id, "change_mode": "amount",
        "amount": Decimal("600"), "start_date": today,
    })

    result = await _forecast(session, test_user, test_workspace)

    _, base = _base_schedule()
    weeks = len(_in_horizon(p.date for p in base.periods))
    assert result.summary.end_balance == pytest.approx(1000 - REPAYMENT * weeks, abs=0.01)
    assert result.scenarios[0].difference == pytest.approx(-(600 - REPAYMENT) * weeks, abs=0.01)
    assert result.scenarios[0].summary.end_balance == pytest.approx(1000 - 600 * weeks, abs=0.01)


async def test_lump_sum(session, test_user, test_workspace):
    today = app_today()
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    on = today + timedelta(days=10)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": loan.id, "change_mode": "lump_sum",
        "amount": Decimal("10000"), "start_date": on,
    })

    result = await _forecast(session, test_user, test_workspace)

    _, base = _base_schedule()
    line = result.loans[0].scenarios[0]
    assert line.payoff_date < base.payoff_date
    assert line.interest_saved > 0
    assert line.repayment == REPAYMENT  # the repayment is kept; the term shortens
    assert result.scenarios[0].difference == pytest.approx(-10_000)
    after = next(p for p in line.points if p.date >= on + timedelta(days=7))
    base_after = next(p for p in result.loans[0].base.points if p.date == after.date)
    assert base_after.balance - after.balance > 10_000  # less owed, and less interest on it


async def test_a_lump_sum_that_clears_the_loan_frees_the_repayments(session, test_user, test_workspace):
    """Base and scenario both pay off inside the horizon: the cash kept is
    exactly the interest saved."""
    today = app_today()
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id, debt=3_000)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": loan.id, "change_mode": "lump_sum",
        "amount": Decimal("1500"), "start_date": today - timedelta(days=2),  # past: lands tomorrow
    })

    result = await _forecast(session, test_user, test_workspace)

    line = result.loans[0].scenarios[0]
    assert line.payoff_date < result.loans[0].base.payoff_date <= _add_months(today, MONTHS)
    assert result.scenarios[0].difference == pytest.approx(line.interest_saved, abs=0.01)
    assert line.points[-1].balance == 0


@pytest.mark.parametrize("recalculate", [True, False])
async def test_rate_change(session, test_user, test_workspace, recalculate):
    today = app_today()
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    start = today + timedelta(days=20)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": loan.id, "change_mode": "rate",
        "percent": Decimal("6.50"), "recalculate_repayment": recalculate, "start_date": start,
    })

    result = await _forecast(session, test_user, test_workspace)

    _, base = _base_schedule()
    line = result.loans[0].scenarios[0]
    assert line.interest_saved < 0  # a higher rate costs interest
    if recalculate:
        # A refix: same term, a higher repayment from the change on.
        assert line.repayment > REPAYMENT
        assert abs(line.payoff_change_days) <= 7
        repaid_after = len(_in_horizon(p.date for p in base.periods if p.date >= start))
        assert result.scenarios[0].difference == pytest.approx(-(line.repayment - REPAYMENT) * repaid_after, abs=0.01)
    else:
        # Same repayment, a longer term, and no change to cash in the horizon.
        assert line.repayment == REPAYMENT
        assert line.payoff_change_days > 0
        assert result.scenarios[0].difference == 0


async def test_rate_change_with_a_chosen_repayment(session, test_user, test_workspace):
    """Fork: a new rate and a repayment the user picks, from the same date."""
    today = app_today()
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    start = today + timedelta(days=20)
    chosen = REPAYMENT + 150
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": loan.id, "change_mode": "rate",
        "percent": Decimal("6.50"), "amount": Decimal(str(chosen)), "start_date": start,
    })

    result = await _forecast(session, test_user, test_workspace)

    _, base = _base_schedule()
    line = result.loans[0].scenarios[0]
    assert line.repayment == pytest.approx(chosen)
    repaid_after = len(_in_horizon(p.date for p in base.periods if p.date >= start))
    assert result.scenarios[0].difference == pytest.approx(-150 * repaid_after, abs=0.01)
    # Paying more than the base repayment, even at the higher rate, ends it sooner.
    assert line.payoff_change_days < 0


async def test_a_rate_change_repayment_must_be_positive():
    from app.services.forecast_service import _validate_item

    item = {
        "kind": "loan_change", "scenario_id": uuid.uuid4(), "account_id": uuid.uuid4(),
        "change_mode": "rate", "percent": Decimal("6.50"), "start_date": app_today(),
    }
    _validate_item({**item, "amount": None})
    _validate_item({**item, "amount": Decimal("900")})
    with pytest.raises(ValueError, match="Repayment must be greater than zero"):
        _validate_item({**item, "amount": Decimal("0")})


async def test_the_whole_term_runs_to_the_last_payoff(session, test_user, test_workspace):
    """Fork: every line of a loan gets the balance over the whole term, to the
    latest payoff, so a lump sum shows how much sooner it ends."""
    today = app_today()
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": loan.id, "change_mode": "lump_sum",
        "amount": Decimal("20000"), "start_date": today + timedelta(days=5),
    })

    result = await _forecast(session, test_user, test_workspace)

    base, line = result.loans[0].base, result.loans[0].scenarios[0]
    assert base is not None and base.payoff_date is not None and line.payoff_date is not None
    # Both span the same months, past the forecast horizon, to the base payoff.
    assert [p.date for p in base.term_points] == [p.date for p in line.term_points]
    assert base.term_points[-1].date >= base.payoff_date > _add_months(today, MONTHS)
    assert base.term_points[-1].balance == 0
    # The scenario reaches zero sooner and stays there.
    first_zero = next(p.date for p in line.term_points if p.balance == 0)
    assert first_zero < base.payoff_date
    assert all(p.balance == 0 for p in line.term_points if p.date >= first_zero)


async def test_changes_to_one_loan_leave_the_others_alone(session, test_user, test_workspace):
    today = app_today()
    await _account(session, test_user.id)
    first = await _loan(session, test_user.id, name="A")
    await _loan(session, test_user.id, debt=50_000, name="B")
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "loan_change", "account_id": first.id, "change_mode": "amount",
        "amount": Decimal("500"), "start_date": today,
    })

    result = await _forecast(session, test_user, test_workspace)

    by_name = {f.name: f for f in result.loans}
    assert len(by_name["A"].scenarios) == 1
    assert by_name["B"].scenarios == []


# ---------------------------------------------------------------------------
# API: validation and scoping
# ---------------------------------------------------------------------------


async def _api_scenario(client, auth_headers) -> str:
    resp = await client.post("/api/forecast/scenarios", json={"name": "Refix", "color": "#10B981"},
                             headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_api_loan_change_round_trip(client, auth_headers, session, test_user, test_workspace):
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)
    scenario_id = await _api_scenario(client, auth_headers)

    resp = await client.post("/api/forecast/items", json={
        "scenario_id": scenario_id, "kind": "loan_change", "account_id": str(loan.id),
        "change_mode": "rate", "percent": "6.25", "start_date": str(app_today() + timedelta(days=30)),
    }, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    item = resp.json()
    assert item["recalculate_repayment"] is True  # a refix by default
    assert item["currency"] == loan.currency

    resp = await client.patch(f"/api/forecast/items/{item['id']}", json={"recalculate_repayment": False},
                              headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["recalculate_repayment"] is False

    resp = await client.get("/api/forecast", params={"months": 3, "include_spending": "false"},
                            headers=auth_headers)
    assert resp.status_code == 200, resp.text
    loans = resp.json()["loans"]
    assert [entry["account_id"] for entry in loans] == [str(loan.id)]
    assert loans[0]["scenarios"][0]["scenario_id"] == scenario_id
    assert loans[0]["scenarios"][0]["payoff_change_days"] > 0


@pytest.mark.parametrize("payload,message", [
    ({"change_mode": "rate"}, "Interest rate must be between 0 and 100"),
    ({"change_mode": "rate", "percent": "120"}, "Interest rate must be between 0 and 100"),
    ({"change_mode": "amount"}, "Amount must be greater than zero"),
    ({"change_mode": "lump_sum", "amount": "0"}, "Amount must be greater than zero"),
    ({"change_mode": "percent", "percent": "5"}, "Change must be rate, amount or lump_sum"),
    ({"change_mode": "rate", "percent": "5", "start_date": None}, "Start date is required"),
    ({"change_mode": "rate", "percent": "5", "account_id": None}, "Loan is required"),
])
async def test_api_loan_change_validation(client, auth_headers, session, test_user, payload, message):
    loan = await _loan(session, test_user.id)
    scenario_id = await _api_scenario(client, auth_headers)
    body = {"scenario_id": scenario_id, "kind": "loan_change", "account_id": str(loan.id),
            "start_date": "2030-01-01", **payload}

    resp = await client.post("/api/forecast/items", json=body, headers=auth_headers)

    assert resp.status_code == 400
    assert resp.json()["detail"] == message


async def test_api_loan_change_needs_an_open_loan_in_the_workspace(
    client, auth_headers, session, test_user, test_workspace
):
    from app.models.user import User
    from app.models.workspace import Workspace, WorkspaceMember

    checking = await _account(session, test_user.id)
    closed = await _loan(session, test_user.id, name="Closed")
    closed.is_closed = True
    other_user = User(id=uuid.uuid4(), email="loans-other@example.com", hashed_password="x", is_active=True,
                      is_superuser=False, is_verified=True)
    session.add(other_user)
    await session.flush()
    other_ws = Workspace(id=uuid.uuid4(), name="Other", kind="personal", created_by_user_id=other_user.id,
                         default_currency="BRL", locale="pt-BR")
    session.add(other_ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other_ws.id, user_id=other_user.id, role="owner"))
    theirs = Account(id=uuid.uuid4(), user_id=other_user.id, workspace_id=other_ws.id, name="Their loan",
                     type="loan", balance=Decimal("0"), currency="BRL", loan_details=_details())
    session.add(theirs)
    await session.commit()
    scenario_id = await _api_scenario(client, auth_headers)

    async def post(account_id) -> str:
        resp = await client.post("/api/forecast/items", json={
            "scenario_id": scenario_id, "kind": "loan_change", "account_id": str(account_id),
            "change_mode": "lump_sum", "amount": "100", "start_date": "2030-01-01",
        }, headers=auth_headers)
        assert resp.status_code == 400
        return resp.json()["detail"]

    assert await post(checking.id) == "The account is not a loan"
    assert await post(closed.id) == "Loan account is closed"
    assert await post(theirs.id) == "Account not found"
    # Nor can an existing change be moved onto a non-loan.
    loan = await _loan(session, test_user.id, name="Mine")
    created = await client.post("/api/forecast/items", json={
        "scenario_id": scenario_id, "kind": "loan_change", "account_id": str(loan.id),
        "change_mode": "lump_sum", "amount": "100", "start_date": "2030-01-01",
    }, headers=auth_headers)
    assert created.status_code == 201, created.text
    resp = await client.patch(f"/api/forecast/items/{created.json()['id']}",
                              json={"account_id": str(checking.id)}, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "The account is not a loan"
    # Other workspaces' loans never appear in this workspace's forecast.
    forecast = (await client.get("/api/forecast", params={"months": 3}, headers=auth_headers)).json()
    assert {entry["account_id"] for entry in forecast["loans"]} == {str(loan.id)}


async def test_api_loan_change_needs_a_scenario(client, auth_headers, session, test_user):
    loan = await _loan(session, test_user.id)
    resp = await client.post("/api/forecast/items", json={
        "kind": "loan_change", "account_id": str(loan.id), "change_mode": "lump_sum", "amount": "100",
        "start_date": "2030-01-01",
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "An expected payment is a one-off or repeating payment, or a budget"
