"""Fork: the Forecast page. Expected payments, scenarios, the everyday
spending estimate and the API around them. Every figure is made up.

Runs on SQLite here and again on PostgreSQL from test_forecast_postgres.py.
"""
import calendar
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.forecast import ForecastItem, ForecastScenario
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services import forecast_service
from app.services.recurring_transaction_service import get_occurrences_in_range
from app.services.report_service import _add_months, get_cash_flow_report

pytestmark = pytest.mark.asyncio

MONTHS = 3


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


async def _account(session: AsyncSession, user_id, opening: float = 1000, type_: str = "checking") -> Account:
    account = Account(
        id=uuid.uuid4(), user_id=user_id, name=f"Acc {type_}", type=type_,
        balance=Decimal(str(opening)), currency="BRL", is_closed=False,
    )
    session.add(account)
    await session.flush()
    if opening:
        await _tx(session, user_id, account.id, opening, "credit", app_today(), source="opening_balance")
    await session.commit()
    return account


async def _tx(
    session: AsyncSession, user_id, account_id, amount: float, type_: str, when: date,
    *, source: str = "manual", category_id=None, status: str = "posted",
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id, description=f"{type_} {amount}",
        amount=Decimal(str(amount)), date=when, type=type_, source=source, currency="BRL",
        category_id=category_id, status=status, created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.commit()
    return tx


async def _recurring(
    session: AsyncSession, user_id, account_id, amount: float, type_: str, start: date,
    *, category_id=None, frequency: str = "monthly",
) -> RecurringTransaction:
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=user_id, account_id=account_id, category_id=category_id,
        description=f"Recurring {type_} {amount}", amount=Decimal(str(amount)), currency="BRL",
        type=type_, frequency=frequency, start_date=start, is_active=True, next_occurrence=start,
    )
    session.add(rec)
    await session.commit()
    return rec


async def _category(session: AsyncSession, user_id, name: str, **flags) -> Category:
    cat = Category(id=uuid.uuid4(), user_id=user_id, name=name, icon="tag", color="#123456", **flags)
    session.add(cat)
    await session.commit()
    return cat


async def _scenario(session: AsyncSession, user_id, workspace_id, *items: dict, name="What if") -> ForecastScenario:
    scenario = ForecastScenario(
        id=uuid.uuid4(), user_id=user_id, workspace_id=workspace_id, name=name, color="#F59E0B",
        is_enabled=True, position=0,
    )
    session.add(scenario)
    await session.flush()
    for values in items:
        session.add(ForecastItem(
            user_id=user_id, workspace_id=workspace_id, scenario_id=scenario.id, currency="BRL", **values,
        ))
    await session.commit()
    return scenario


async def _expected(session: AsyncSession, user_id, workspace_id, **values) -> ForecastItem:
    item = ForecastItem(
        user_id=user_id, workspace_id=workspace_id, kind="one_off", currency="BRL", **values,
    )
    session.add(item)
    await session.commit()
    return item


async def _forecast(session, user, workspace, **kwargs):
    kwargs.setdefault("include_spending", False)
    return await forecast_service.compute_forecast(
        session, workspace.id, user.id, months=kwargs.pop("months", MONTHS), interval="weekly", **kwargs
    )


def _count(start: date, frequency: str = "monthly", stop: date | None = None) -> int:
    today = app_today()
    return len(get_occurrences_in_range(
        start=start, frequency=frequency, end_date=None,
        range_start=today + timedelta(days=1),
        range_end=(stop or _add_months(today, MONTHS)) + timedelta(days=1),
    ))


def _spread_total(monthly: float, start: date, end: date) -> float:
    total, d = 0.0, start
    while d <= end:
        total += monthly / calendar.monthrange(d.year, d.month)[1]
        d += timedelta(days=1)
    return total


# ---------------------------------------------------------------------------
# base line
# ---------------------------------------------------------------------------


async def test_base_line_counts_recurring_and_expected_payments(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    await _recurring(session, test_user.id, account.id, 500, "credit", today + timedelta(days=1))
    await _expected(
        session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
        date=today + timedelta(days=10), account_id=account.id, description="Tax bill",
    )

    result = await _forecast(session, test_user, test_workspace)

    assert result.starting_balance == 1000
    assert result.base[0].date == today
    assert result.base[-1].date == _add_months(today, MONTHS)
    expected_end = 1000 + 500 * _count(today + timedelta(days=1)) - 200
    assert result.summary.end_balance == pytest.approx(expected_end)
    assert result.scenarios == []


async def test_lowest_point_and_its_date(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    dip = today + timedelta(days=20)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("900"), type="debit",
                    date=dip, account_id=account.id)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("1500"), type="credit",
                    date=dip + timedelta(days=5), account_id=account.id)

    result = await _forecast(session, test_user, test_workspace)

    assert result.summary.lowest_balance == pytest.approx(100)
    assert result.summary.lowest_date == dip
    assert result.summary.end_balance == pytest.approx(1600)


async def test_expected_payment_on_a_loan_account_is_not_cash(session, test_user, test_workspace):
    today = app_today()
    await _account(session, test_user.id)
    loan = await _account(session, test_user.id, opening=0, type_="loan")
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
                    date=today + timedelta(days=3), account_id=loan.id)

    result = await _forecast(session, test_user, test_workspace)

    assert result.summary.end_balance == pytest.approx(1000)


async def test_cash_flow_report_counts_expected_payments_but_not_scenarios(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
                    date=today + timedelta(days=10), account_id=account.id)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "one_off", "amount": Decimal("5000"), "type": "credit", "date": today + timedelta(days=5),
    })

    report = await get_cash_flow_report(session, test_workspace.id, test_user.id, months=MONTHS, interval="daily")

    assert report.summary.primary_value == pytest.approx(800)
    expenses = next(b for b in report.summary.breakdowns if b.key == "projectedExpenses")
    assert expenses.value == pytest.approx(200)


async def test_overdue_unmatched_payment_is_still_expected_tomorrow(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
                    date=today - timedelta(days=30), account_id=account.id)

    result = await _forecast(session, test_user, test_workspace)

    assert result.summary.end_balance == pytest.approx(800)


# ---------------------------------------------------------------------------
# scenarios, one kind at a time
# ---------------------------------------------------------------------------


async def test_one_off_and_repeating_scenario_items(session, test_user, test_workspace):
    today = app_today()
    await _account(session, test_user.id)
    first = today + timedelta(days=1)
    await _scenario(
        session, test_user.id, test_workspace.id,
        {"kind": "one_off", "amount": Decimal("300"), "type": "credit", "date": today + timedelta(days=4)},
        {"kind": "repeating", "amount": Decimal("50"), "type": "debit", "frequency": "weekly",
         "start_date": first, "end_date": first + timedelta(days=28)},
    )

    result = await _forecast(session, test_user, test_workspace)

    assert len(result.scenarios) == 1
    line = result.scenarios[0]
    assert line.difference == pytest.approx(300 - 5 * 50)
    assert line.summary.end_balance == pytest.approx(1000 + 300 - 250)
    assert [p.date for p in line.points] == [p.date for p in result.base]


async def test_disabled_scenarios_are_left_out_unless_asked_for(session, test_user, test_workspace):
    today = app_today()
    await _account(session, test_user.id)
    scenario = await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "one_off", "amount": Decimal("100"), "type": "debit", "date": today + timedelta(days=2),
    })
    scenario.is_enabled = False
    await session.commit()

    assert (await _forecast(session, test_user, test_workspace)).scenarios == []
    asked = await _forecast(session, test_user, test_workspace, scenario_ids=[scenario.id])
    assert [s.id for s in asked.scenarios] == [scenario.id]
    assert asked.scenarios[0].difference == pytest.approx(-100)


@pytest.mark.parametrize("mode", ["stop", "pause", "amount"])
async def test_recurring_change(session, test_user, test_workspace, mode):
    today = app_today()
    account = await _account(session, test_user.id)
    start = today + timedelta(days=1)
    rec = await _recurring(session, test_user.id, account.id, 500, "debit", start)
    occurrences = get_occurrences_in_range(
        start=start, frequency="monthly", end_date=None, range_start=start,
        range_end=_add_months(today, MONTHS) + timedelta(days=1),
    )
    assert len(occurrences) >= 3
    change = {"kind": "recurring_change", "recurring_transaction_id": rec.id, "change_mode": mode}
    if mode == "stop":
        change["start_date"] = occurrences[1]  # the first payment still happens
        expected = 500 * (len(occurrences) - 1)
    elif mode == "pause":
        change["start_date"] = occurrences[1] - timedelta(days=1)
        change["end_date"] = occurrences[1] + timedelta(days=1)
        expected = 500
    else:
        change["start_date"] = start
        change["amount"] = Decimal("700")
        expected = -200 * len(occurrences)
    await _scenario(session, test_user.id, test_workspace.id, change)

    result = await _forecast(session, test_user, test_workspace)

    assert result.scenarios[0].difference == pytest.approx(expected)


@pytest.mark.parametrize("mode,expected_per_payment", [("percent", 50.0), ("amount", -100.0)])
async def test_income_change(session, test_user, test_workspace, mode, expected_per_payment):
    today = app_today()
    account = await _account(session, test_user.id)
    start = today + timedelta(days=1)
    await _recurring(session, test_user.id, account.id, 500, "credit", start)
    await _recurring(session, test_user.id, account.id, 80, "debit", start)  # not income: untouched
    change = {"kind": "income_change", "change_mode": mode, "start_date": start}
    if mode == "percent":
        change["percent"] = Decimal("10")
    else:
        change["amount"] = Decimal("-100")
    await _scenario(session, test_user.id, test_workspace.id, change)

    result = await _forecast(session, test_user, test_workspace)

    assert result.scenarios[0].difference == pytest.approx(expected_per_payment * _count(start))


# ---------------------------------------------------------------------------
# everyday spending estimate and spending_change
# ---------------------------------------------------------------------------


async def _spending_history(session, user_id, account_id):
    """Eating out: 300 on the 1st of each of the last three full months and a
    60 refund, so 280 a month. Rent: covered by a recurring payment. Savings:
    a transfer-like category. Neither of those two is everyday spending."""
    today = app_today()
    eating = await _category(session, user_id, "Eating out")
    rent = await _category(session, user_id, "Rent")
    savings = await _category(session, user_id, "Savings", treat_as_transfer=True)
    this_month = today.replace(day=1)
    for back in (1, 2, 3):
        month = _add_months(this_month, -back)
        await _tx(session, user_id, account_id, 300, "debit", month, category_id=eating.id)
        await _tx(session, user_id, account_id, 1200, "debit", month, category_id=rent.id)
        await _tx(session, user_id, account_id, 400, "debit", month, category_id=savings.id)
    await _tx(session, user_id, account_id, 60, "credit", _add_months(this_month, -2) + timedelta(days=1),
              category_id=eating.id)
    # This month is not a full month yet and is left out.
    await _tx(session, user_id, account_id, 999, "debit", this_month, category_id=eating.id)
    await _recurring(session, user_id, account_id, 1200, "debit", _add_months(this_month, 1), category_id=rent.id)
    return eating


async def test_spending_estimate_per_category(session, test_user, test_workspace):
    account = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, account.id)

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    assert [(e.category_id, e.monthly_amount) for e in result.spending_estimates] == [(eating.id, 280.0)]


async def test_spending_estimate_feeds_the_base_only_when_switched_on(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    await _spending_history(session, test_user.id, account.id)

    off = await _forecast(session, test_user, test_workspace, include_spending=False)
    on = await _forecast(session, test_user, test_workspace, include_spending=True)

    end = _add_months(today, MONTHS)
    assert off.summary.end_balance - on.summary.end_balance == pytest.approx(
        _spread_total(280, today + timedelta(days=1), end), abs=0.05
    )
    # The estimate is listed either way, for the scenario editor.
    assert len(off.spending_estimates) == 1


async def test_spending_change_percent(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, account.id)
    start = today + timedelta(days=1)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "spending_change", "category_id": eating.id, "change_mode": "percent",
        "percent": Decimal("-50"), "start_date": start,
    })

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    expected = _spread_total(140, start, _add_months(today, MONTHS))
    assert result.scenarios[0].difference == pytest.approx(expected, abs=0.05)


async def test_spending_change_amount_between_dates(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, account.id)
    start, stop = today + timedelta(days=10), today + timedelta(days=40)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "spending_change", "category_id": eating.id, "change_mode": "amount",
        "amount": Decimal("-100"), "start_date": start, "end_date": stop,
    })

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    assert result.scenarios[0].difference == pytest.approx(_spread_total(100, start, stop), abs=0.05)


async def test_spending_cut_cannot_go_below_nothing(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, account.id)
    start = today + timedelta(days=1)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "spending_change", "category_id": eating.id, "change_mode": "amount",
        "amount": Decimal("-1000"), "start_date": start,
    })

    result = await _forecast(session, test_user, test_workspace, include_spending=True)

    expected = _spread_total(280, start, _add_months(today, MONTHS))
    assert result.scenarios[0].difference == pytest.approx(expected, abs=0.05)


async def test_spending_change_does_nothing_with_the_estimate_off(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    eating = await _spending_history(session, test_user.id, account.id)
    await _scenario(session, test_user.id, test_workspace.id, {
        "kind": "spending_change", "category_id": eating.id, "change_mode": "percent",
        "percent": Decimal("-50"), "start_date": today + timedelta(days=1),
    })

    result = await _forecast(session, test_user, test_workspace, include_spending=False)

    assert result.scenarios[0].difference == 0


# ---------------------------------------------------------------------------
# matching expected payments
# ---------------------------------------------------------------------------


async def test_matching_tolerance(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    item = await _expected(session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
                           date=today - timedelta(days=2), account_id=account.id)
    # Outside the tolerance: 10% off, 12 days off, the other direction, still pending.
    await _tx(session, test_user.id, account.id, 220, "debit", today - timedelta(days=1))
    await _tx(session, test_user.id, account.id, 200, "debit", today - timedelta(days=14))
    await _tx(session, test_user.id, account.id, 200, "credit", today - timedelta(days=1))
    await _tx(session, test_user.id, account.id, 200, "debit", today, status="pending")

    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 0

    real = await _tx(session, test_user.id, account.id, 205, "debit", today - timedelta(days=1))
    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 1
    await session.refresh(item)
    assert item.matched_transaction_id == real.id


async def test_matched_payment_drops_out_of_the_forecast(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    item = await _expected(session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
                           date=today + timedelta(days=3), account_id=account.id)
    # The bill came early; the bank row is already in the balance.
    real = await _tx(session, test_user.id, account.id, 198, "debit", today)

    result = await _forecast(session, test_user, test_workspace)

    # Owner 2026-10-14: viewing never writes; the match is saved by sync/import.
    await session.refresh(item)
    assert item.matched_transaction_id is None
    assert real.id is not None
    assert result.starting_balance == pytest.approx(802)
    assert result.summary.end_balance == pytest.approx(802)


async def test_matching_only_looks_at_the_rows_given(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    await _expected(session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
                    date=today, account_id=account.id)
    real = await _tx(session, test_user.id, account.id, 200, "debit", today)

    assert await forecast_service.match_expected_payments(session, test_workspace.id, []) == 0
    assert await forecast_service.match_expected_payments(session, test_workspace.id, [real]) == 1


async def test_one_bank_row_settles_one_payment(session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)
    for _ in range(2):
        await _expected(session, test_user.id, test_workspace.id, amount=Decimal("200"), type="debit",
                        date=today, account_id=account.id)
    await _tx(session, test_user.id, account.id, 200, "debit", today)

    assert await forecast_service.match_expected_payments(session, test_workspace.id) == 1


# ---------------------------------------------------------------------------
# API: CRUD, validation, workspace scope, write gate
# ---------------------------------------------------------------------------


async def test_api_crud_and_forecast(client, auth_headers, session, test_user, test_workspace):
    today = app_today()
    account = await _account(session, test_user.id)

    resp = await client.post("/api/forecast/scenarios", json={"name": "New car", "color": "#EF4444"},
                             headers=auth_headers)
    assert resp.status_code == 201, resp.text
    scenario = resp.json()
    assert scenario["position"] == 0 and scenario["is_enabled"] is True

    resp = await client.post("/api/forecast/items", json={
        "kind": "one_off", "amount": "250.00", "type": "debit", "date": str(today + timedelta(days=5)),
        "account_id": str(account.id), "description": "Holiday deposit",
    }, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    expected = resp.json()
    assert expected["currency"] == "BRL" and expected["scenario_id"] is None

    resp = await client.post("/api/forecast/items", json={
        "scenario_id": scenario["id"], "kind": "repeating", "amount": "100", "type": "debit",
        "frequency": "monthly", "start_date": str(today + timedelta(days=1)),
    }, headers=auth_headers)
    assert resp.status_code == 201, resp.text

    resp = await client.get("/api/forecast", params={"months": 3, "interval": "monthly",
                                                     "include_spending": "false"}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["summary"]["end_balance"] == pytest.approx(750)
    assert [s["name"] for s in body["scenarios"]] == ["New car"]
    assert body["scenarios"][0]["difference"] == pytest.approx(-100 * _count(today + timedelta(days=1)))

    resp = await client.patch(f"/api/forecast/items/{expected['id']}", json={"amount": "300"},
                              headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["amount"] == "300.00"

    resp = await client.patch(f"/api/forecast/scenarios/{scenario['id']}", json={"is_enabled": False},
                              headers=auth_headers)
    assert resp.status_code == 200 and resp.json()["is_enabled"] is False

    resp = await client.get("/api/forecast/items", params={"expected": "true"}, headers=auth_headers)
    assert [i["id"] for i in resp.json()] == [expected["id"]]
    resp = await client.get("/api/forecast/items", params={"scenario_id": scenario["id"]}, headers=auth_headers)
    assert len(resp.json()) == 1

    assert (await client.delete(f"/api/forecast/scenarios/{scenario['id']}", headers=auth_headers)).status_code == 204
    resp = await client.get("/api/forecast/items", headers=auth_headers)
    assert [i["id"] for i in resp.json()] == [expected["id"]]  # the scenario's change went with it
    assert (await client.delete(f"/api/forecast/items/{expected['id']}", headers=auth_headers)).status_code == 204
    assert (await client.get("/api/forecast/items", headers=auth_headers)).json() == []


@pytest.mark.parametrize("payload,message", [
    ({"kind": "repeating", "amount": "1", "type": "debit", "frequency": "monthly", "start_date": "2030-01-01"},
     "An expected payment is a one-off payment"),
    ({"kind": "one_off", "amount": "0", "type": "debit", "date": "2030-01-01"}, "Amount must be greater than zero"),
    ({"kind": "one_off", "amount": "5", "date": "2030-01-01"}, "Type is required"),
])
async def test_api_validation(client, auth_headers, test_user, payload, message):
    resp = await client.post("/api/forecast/items", json=payload, headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"] == message


async def test_api_scenario_change_validation(client, auth_headers, test_user):
    scenario = (await client.post("/api/forecast/scenarios", json={"name": "S", "color": "#000000"},
                                  headers=auth_headers)).json()
    resp = await client.post("/api/forecast/items", json={
        "scenario_id": scenario["id"], "kind": "spending_change", "change_mode": "percent", "percent": "-50",
        "start_date": "2030-01-01",
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "Category is required"
    resp = await client.post("/api/forecast/items", json={
        "scenario_id": scenario["id"], "kind": "recurring_change", "change_mode": "pause",
        "recurring_transaction_id": str(uuid.uuid4()), "start_date": "2030-01-01",
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "Recurring transaction not found"


async def test_api_is_workspace_scoped(client, auth_headers, session, test_user, test_workspace):
    from app.models.user import User
    from app.models.workspace import Workspace, WorkspaceMember

    other_user = User(id=uuid.uuid4(), email="other@example.com", hashed_password="x", is_active=True,
                      is_superuser=False, is_verified=True)
    session.add(other_user)
    await session.flush()
    other_ws = Workspace(id=uuid.uuid4(), name="Other", kind="personal", created_by_user_id=other_user.id,
                         default_currency="BRL", locale="pt-BR")
    session.add(other_ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=other_ws.id, user_id=other_user.id, role="owner"))
    other_account = Account(id=uuid.uuid4(), user_id=other_user.id, workspace_id=other_ws.id, name="Theirs",
                            type="checking", balance=Decimal("0"), currency="BRL")
    session.add(other_account)
    await session.commit()
    theirs = await _expected(session, other_user.id, other_ws.id, amount=Decimal("10"), type="debit",
                             date=app_today())
    their_scenario = await _scenario(session, other_user.id, other_ws.id, name="Theirs")

    assert (await client.get("/api/forecast/items", headers=auth_headers)).json() == []
    assert (await client.get("/api/forecast/scenarios", headers=auth_headers)).json() == []
    resp = await client.patch(f"/api/forecast/items/{theirs.id}", json={"amount": "1"}, headers=auth_headers)
    assert resp.status_code == 404
    resp = await client.delete(f"/api/forecast/scenarios/{their_scenario.id}", headers=auth_headers)
    assert resp.status_code == 404
    resp = await client.post("/api/forecast/items", json={
        "kind": "one_off", "amount": "5", "type": "debit", "date": "2030-01-01",
        "account_id": str(other_account.id),
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "Account not found"
    resp = await client.post("/api/forecast/items", json={
        "scenario_id": str(their_scenario.id), "kind": "one_off", "amount": "5", "type": "debit",
        "date": "2030-01-01",
    }, headers=auth_headers)
    assert resp.status_code == 400 and resp.json()["detail"] == "Scenario not found"


async def test_api_viewer_can_read_but_not_write(client, viewer_auth_headers):
    assert (await client.get("/api/forecast/scenarios", headers=viewer_auth_headers)).status_code == 200
    resp = await client.post("/api/forecast/scenarios", json={"name": "S", "color": "#000000"},
                             headers=viewer_auth_headers)
    assert resp.status_code == 403



async def test_creating_a_payment_whose_bank_row_has_landed_matches_it(session, test_user, test_workspace):
    from app.schemas.forecast import ForecastItemCreate

    today = app_today()
    account = await _account(session, test_user.id)
    real = await _tx(session, test_user.id, account.id, 200, "debit", today - timedelta(days=1))
    item = await forecast_service.create_item(
        session, test_workspace.id, test_user.id,
        ForecastItemCreate(kind="one_off", amount=Decimal("200"), type="debit",
                           date=today, account_id=account.id, description="Rates"),
    )
    assert item.matched_transaction_id == real.id
