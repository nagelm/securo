"""Periodic contribution targets on goals: period maths, contributions per
period, on-track status and streak, ongoing goals without a total, bulk
creation, the suggested target, and that goals stay display-only.

Every name and amount here is made up.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.category import Category
from app.models.goal import Goal
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.services import goal_service
from app.services.goal_contributions import (
    compute_contribution_progress,
    period_bounds,
    recent_periods,
    suggest_contribution,
)

# A Wednesday. Its week runs Mon 2026-03-16 .. Sun 2026-03-22.
TODAY = date(2026, 3, 18)


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(goal_service, "app_today", lambda: TODAY)
    return TODAY


# ---------------------------------------------------------------------------
# Period maths
# ---------------------------------------------------------------------------


def test_weekly_period_runs_monday_to_sunday():
    assert period_bounds("weekly", TODAY) == (date(2026, 3, 16), date(2026, 3, 22))
    assert period_bounds("weekly", date(2026, 3, 22)) == (date(2026, 3, 16), date(2026, 3, 22))
    assert period_bounds("weekly", date(2026, 3, 23)) == (date(2026, 3, 23), date(2026, 3, 29))


def test_fortnight_is_two_weeks_from_a_fixed_monday():
    start, end = period_bounds("biweekly", TODAY)
    assert start.weekday() == 0
    assert (end - start).days == 13
    assert start <= TODAY <= end
    # Every day of the fortnight maps to the same fortnight.
    for offset in range(14):
        assert period_bounds("biweekly", start + timedelta(days=offset)) == (start, end)
    assert period_bounds("biweekly", end + timedelta(days=1))[0] == end + timedelta(days=1)


def test_monthly_period_is_the_calendar_month():
    assert period_bounds("monthly", TODAY) == (date(2026, 3, 1), date(2026, 3, 31))
    assert period_bounds("monthly", date(2024, 2, 10)) == (date(2024, 2, 1), date(2024, 2, 29))
    assert period_bounds("monthly", date(2026, 12, 31)) == (date(2026, 12, 1), date(2026, 12, 31))


def test_unknown_period_is_rejected():
    with pytest.raises(ValueError):
        period_bounds("yearly", TODAY)


def test_recent_periods_are_contiguous_and_end_with_today():
    bounds = recent_periods("monthly", TODAY, 12)
    assert len(bounds) == 12
    assert bounds[-1] == (date(2026, 3, 1), date(2026, 3, 31))
    assert bounds[0] == (date(2025, 4, 1), date(2025, 4, 30))
    for (_, end), (start, _) in zip(bounds, bounds[1:]):
        assert start == end + timedelta(days=1)


# ---------------------------------------------------------------------------
# Ledger helpers
# ---------------------------------------------------------------------------


async def _account(session: AsyncSession, user: User, name: str, type_: str = "savings") -> Account:
    acc = Account(
        id=uuid.uuid4(), user_id=user.id, name=name, type=type_,
        balance=Decimal("0"), currency="BRL",
    )
    session.add(acc)
    await session.commit()
    await session.refresh(acc)
    return acc


async def _category(session: AsyncSession, user: User, name: str, transfer: bool) -> Category:
    cat = Category(id=uuid.uuid4(), user_id=user.id, name=name, treat_as_transfer=transfer)
    session.add(cat)
    await session.commit()
    return cat


def _tx(user: User, account: Account, amount: str, day: date, type_: str, **kw) -> Transaction:
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id,
        description=kw.pop("description", f"{type_} {amount}"),
        amount=Decimal(amount), currency="BRL", date=day, type=type_,
        source="manual", created_at=datetime.now(timezone.utc), **kw,
    )


async def _transfer(
    session: AsyncSession, user: User, src: Account, dst: Account, amount: str, day: date,
    category: Category | None = None, **debit_kw,
) -> uuid.UUID:
    pair = uuid.uuid4()
    cat_id = category.id if category else None
    session.add(_tx(user, src, amount, day, "debit", transfer_pair_id=pair, category_id=cat_id, **debit_kw))
    session.add(_tx(user, dst, amount, day, "credit", transfer_pair_id=pair, category_id=cat_id))
    await session.commit()
    return pair


# ---------------------------------------------------------------------------
# Contributions per period
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contributions_count_transfers_in_and_out_only(session: AsyncSession, test_user: User):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Fund")
    transfers = await _category(session, test_user, "Transfers", transfer=True)
    interest_cat = await _category(session, test_user, "Interest", transfer=False)

    this_week = date(2026, 3, 16)
    last_week = date(2026, 3, 10)
    # Last week: 50 in, 20 back out to the everyday account.
    await _transfer(session, test_user, everyday, fund, "50.00", last_week, transfers)
    await _transfer(session, test_user, fund, everyday, "20.00", last_week + timedelta(days=1), transfers)
    # This week: 50 in, plus interest and tax on it, which are not contributions.
    await _transfer(session, test_user, everyday, fund, "50.00", this_week, transfers)
    session.add(_tx(test_user, fund, "3.10", this_week, "credit", category_id=interest_cat.id))
    session.add(_tx(test_user, fund, "0.87", this_week, "debit"))
    # An unpaired credit filed as a transfer: from an account kept outside Securo.
    session.add(_tx(test_user, fund, "15.00", this_week, "credit", category_id=transfers.id))
    # Ignored rows never count.
    session.add(_tx(test_user, fund, "999.00", this_week, "credit", category_id=transfers.id, is_ignored=True))
    await session.commit()

    progress = await compute_contribution_progress(
        session, account_ids=[fund.id], period="weekly", target=Decimal("50"),
        currency="BRL", today=TODAY, count=4,
    )
    assert [p.start for p in progress.history] == [
        date(2026, 2, 23), date(2026, 3, 2), date(2026, 3, 9), date(2026, 3, 16),
    ]
    assert progress.history[-2].contributed == Decimal("30.00")
    assert progress.history[-2].status == "short"
    assert progress.current.contributed == Decimal("65.00")
    assert progress.current.status == "over"
    assert progress.current.current is True


@pytest.mark.asyncio
async def test_transfer_between_two_linked_accounts_is_not_a_contribution(
    session: AsyncSession, test_user: User
):
    a = await _account(session, test_user, "Fund A")
    b = await _account(session, test_user, "Fund B")
    await _transfer(session, test_user, a, b, "40.00", TODAY)
    progress = await compute_contribution_progress(
        session, account_ids=[a.id, b.id], period="weekly", target=Decimal("10"),
        currency="BRL", today=TODAY, count=1,
    )
    assert progress.current.contributed == Decimal("0.00")
    assert progress.current.status == "pending"


@pytest.mark.asyncio
async def test_streak_counts_back_from_the_latest_finished_period(session: AsyncSession, test_user: User):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Fund")
    # Weeks starting 02-16 (miss), 02-23, 03-02, 03-09 (all met); current not met yet.
    for monday in (date(2026, 2, 23), date(2026, 3, 2), date(2026, 3, 9)):
        await _transfer(session, test_user, everyday, fund, "25.00", monday + timedelta(days=3))
    progress = await compute_contribution_progress(
        session, account_ids=[fund.id], period="weekly", target=Decimal("25"),
        currency="BRL", today=TODAY, count=6,
    )
    assert [p.status for p in progress.history] == ["short", "short", "met", "met", "met", "pending"]
    assert progress.streak == 3

    # Meeting the current week extends the streak.
    await _transfer(session, test_user, everyday, fund, "25.00", TODAY)
    progress = await compute_contribution_progress(
        session, account_ids=[fund.id], period="weekly", target=Decimal("25"),
        currency="BRL", today=TODAY, count=6,
    )
    assert progress.current.status == "met"
    assert progress.streak == 4


# ---------------------------------------------------------------------------
# API: periodic targets and ongoing goals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ongoing_goal_has_no_total_and_reports_period_progress(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User, fixed_today
):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Holiday fund")
    await _transfer(session, test_user, everyday, fund, "30.00", date(2026, 3, 17))

    resp = await client.post(
        "/api/goals",
        json={
            "name": "Holiday",
            "currency": "BRL",
            "tracking_type": "account",
            "account_id": str(fund.id),
            "contribution_amount": "50.00",
            "contribution_period": "weekly",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["target_amount"] is None
    assert float(data["contribution_amount"]) == 50.0
    assert data["contribution_period"] == "weekly"
    assert data["percentage"] == 0
    assert data["monthly_contribution"] is None
    assert data["on_track"] is None
    progress = data["contribution_progress"]
    assert progress["period"] == "weekly"
    assert len(progress["history"]) == 12
    assert progress["current"]["start"] == "2026-03-16"
    assert float(progress["current"]["contributed"]) == 30.0
    assert progress["current"]["status"] == "pending"
    assert progress["streak"] == 0

    summary = (await client.get("/api/goals/summary", headers=auth_headers)).json()
    assert summary[0]["target_amount"] is None
    assert float(summary[0]["period_contributed"]) == 30.0
    assert summary[0]["period_status"] == "pending"
    assert summary[0]["contribution_streak"] == 0


@pytest.mark.asyncio
async def test_goal_with_total_and_periodic_target_keeps_both(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User, fixed_today
):
    fund = await _account(session, test_user, "Car fund")
    resp = await client.post(
        "/api/goals",
        json={
            "name": "Car",
            "target_amount": "6000.00",
            "currency": "BRL",
            "target_date": "2027-03-01",
            "tracking_type": "account",
            "account_id": str(fund.id),
            "contribution_amount": "200.00",
            "contribution_period": "monthly",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert float(data["target_amount"]) == 6000.0
    assert data["monthly_contribution"] is not None
    assert data["contribution_progress"]["current"]["start"] == "2026-03-01"


@pytest.mark.asyncio
async def test_manual_goal_with_periodic_target_has_no_progress(
    client: AsyncClient, auth_headers: dict, fixed_today
):
    resp = await client.post(
        "/api/goals",
        json={"name": "Pet", "currency": "BRL", "contribution_amount": "10", "contribution_period": "biweekly"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["contribution_progress"] is None
    assert resp.json()["contribution_period"] == "biweekly"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"name": "Nothing to aim at"},
        {"name": "Half", "contribution_amount": "10"},
        {"name": "Half", "target_amount": "100", "contribution_period": "weekly"},
        {"name": "Bad period", "contribution_amount": "10", "contribution_period": "yearly"},
        {"name": "Zero", "contribution_amount": "0", "contribution_period": "weekly"},
    ],
)
async def test_invalid_targets_are_rejected(client: AsyncClient, auth_headers: dict, body: dict):
    resp = await client.post("/api/goals", json=body, headers=auth_headers)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_update_cannot_leave_a_goal_without_any_target(client: AsyncClient, auth_headers: dict):
    created = await client.post(
        "/api/goals", json={"name": "Total only", "target_amount": "100"}, headers=auth_headers,
    )
    goal_id = created.json()["id"]
    resp = await client.patch(f"/api/goals/{goal_id}", json={"target_amount": None}, headers=auth_headers)
    assert resp.status_code == 400

    # Adding a periodic target first makes the total optional.
    resp = await client.patch(
        f"/api/goals/{goal_id}",
        json={"target_amount": None, "contribution_amount": "5", "contribution_period": "weekly"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["target_amount"] is None

    # Removing the periodic target again needs a total back.
    resp = await client.patch(
        f"/api/goals/{goal_id}",
        json={"contribution_amount": None, "contribution_period": None},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    resp = await client.get(f"/api/goals/{goal_id}", headers=auth_headers)
    assert resp.json()["contribution_period"] == "weekly"


# ---------------------------------------------------------------------------
# Quick setup: several goals at once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_create_makes_one_goal_per_account(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User, fixed_today
):
    a = await _account(session, test_user, "Fund A")
    b = await _account(session, test_user, "Fund B")
    resp = await client.post(
        "/api/goals/bulk",
        json={"goals": [
            {"name": "Fund A", "tracking_type": "account", "account_id": str(a.id),
             "contribution_amount": "20", "contribution_period": "weekly", "currency": "BRL"},
            {"name": "Fund B", "tracking_type": "account", "account_id": str(b.id),
             "contribution_amount": "80", "contribution_period": "monthly", "currency": "BRL"},
        ]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    assert [g["name"] for g in resp.json()] == ["Fund A", "Fund B"]
    assert {g["account_id"] for g in resp.json()} == {str(a.id), str(b.id)}


@pytest.mark.asyncio
async def test_bulk_create_is_all_or_nothing(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    a = await _account(session, test_user, "Fund A")
    resp = await client.post(
        "/api/goals/bulk",
        json={"goals": [
            {"name": "Good", "tracking_type": "account", "account_id": str(a.id),
             "contribution_amount": "20", "contribution_period": "weekly"},
            {"name": "Bad", "tracking_type": "account", "account_id": str(uuid.uuid4()),
             "contribution_amount": "20", "contribution_period": "weekly"},
        ]},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    count = (await session.execute(select(func.count()).select_from(Goal))).scalar_one()
    assert count == 0


@pytest.mark.asyncio
async def test_bulk_create_needs_write_access(client: AsyncClient, viewer_auth_headers: dict):
    resp = await client.post(
        "/api/goals/bulk",
        json={"goals": [{"name": "X", "target_amount": "1"}]},
        headers=viewer_auth_headers,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Suggested target
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suggestion_from_a_schedule_paying_the_other_leg(
    session: AsyncSession, test_user: User, test_workspace
):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Fund")
    rec = RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=everyday.id, description="To fund",
        amount=Decimal("35.00"), currency="BRL", type="debit", frequency="weekly",
        start_date=date(2026, 1, 1), next_occurrence=date(2026, 3, 19), is_active=True,
    )
    session.add(rec)
    await session.commit()
    await _transfer(session, test_user, everyday, fund, "35.00", date(2026, 3, 12), recurring_transaction_id=rec.id)

    s = await suggest_contribution(session, workspace_id=test_workspace.id, account_id=fund.id, today=TODAY)
    assert s is not None
    assert (s.amount, s.period, s.source) == (Decimal("35.00"), "weekly", "recurring")
    assert s.recurring_transaction_id == rec.id


@pytest.mark.asyncio
async def test_suggestion_from_a_credit_scheduled_on_the_account(
    session: AsyncSession, test_user: User, test_workspace
):
    fund = await _account(session, test_user, "Fund")
    session.add(RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, account_id=fund.id, description="Top-up",
        amount=Decimal("120.00"), currency="BRL", type="credit", frequency="monthly",
        start_date=date(2026, 1, 5), next_occurrence=date(2026, 4, 5), is_active=True,
    ))
    await session.commit()
    s = await suggest_contribution(session, workspace_id=test_workspace.id, account_id=fund.id, today=TODAY)
    assert s is not None
    assert (s.amount, s.period, s.source) == (Decimal("120.00"), "monthly", "recurring")


@pytest.mark.asyncio
async def test_suggestion_from_regular_transfers(session: AsyncSession, test_user: User, test_workspace):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Fund")
    for weeks_ago in range(1, 6):
        await _transfer(session, test_user, everyday, fund, "15.00", TODAY - timedelta(weeks=weeks_ago))
    await _transfer(session, test_user, everyday, fund, "400.00", TODAY - timedelta(days=2))

    s = await suggest_contribution(session, workspace_id=test_workspace.id, account_id=fund.id, today=TODAY)
    assert s is not None
    assert (s.amount, s.period, s.source) == (Decimal("15.00"), "weekly", "history")


@pytest.mark.asyncio
async def test_no_suggestion_without_a_regular_pattern(session: AsyncSession, test_user: User, test_workspace):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Fund")
    await _transfer(session, test_user, everyday, fund, "15.00", TODAY - timedelta(days=3))
    await _transfer(session, test_user, everyday, fund, "15.00", TODAY - timedelta(days=60))
    s = await suggest_contribution(session, workspace_id=test_workspace.id, account_id=fund.id, today=TODAY)
    assert s is None


@pytest.mark.asyncio
async def test_suggestion_endpoint(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User, fixed_today
):
    fund = await _account(session, test_user, "Fund")
    resp = await client.get(
        "/api/goals/contribution-suggestion", params={"account_id": str(fund.id)}, headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json() is None

    resp = await client.get(
        "/api/goals/contribution-suggestion", params={"account_id": str(uuid.uuid4())}, headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Display-only (the report endpoints need PostgreSQL: see
# test_goal_contributions_postgres.py)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goals_never_change_balances_reports_or_budgets(
    client: AsyncClient, auth_headers: dict, session: AsyncSession, test_user: User
):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Fund")
    today = date.today()
    await _transfer(session, test_user, everyday, fund, "50.00", today)
    session.add(_tx(test_user, everyday, "500.00", today, "credit", description="Pay"))
    session.add(_tx(test_user, fund, "1.25", today, "credit", description="Interest"))
    await session.commit()

    month = today.replace(day=1).isoformat()
    endpoints = [
        ("/api/accounts", {}),
        ("/api/dashboard/summary", {"month": month}),
        ("/api/budgets/comparison", {"month": month}),
    ]

    async def snapshot():
        out = {}
        for path, params in endpoints:
            resp = await client.get(path, params=params, headers=auth_headers)
            assert resp.status_code == 200, (path, resp.text)
            out[path] = resp.json()
        return out

    before = await snapshot()
    resp = await client.post(
        "/api/goals/bulk",
        json={"goals": [
            {"name": "Fund", "tracking_type": "account", "account_id": str(fund.id),
             "target_amount": "1000", "contribution_amount": "50", "contribution_period": "weekly"},
            {"name": "Sinking", "tracking_type": "account", "account_id": str(everyday.id),
             "contribution_amount": "10", "contribution_period": "monthly"},
        ]},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    goals = (await client.get("/api/goals", headers=auth_headers)).json()
    assert len(goals) == 2
    assert await snapshot() == before
