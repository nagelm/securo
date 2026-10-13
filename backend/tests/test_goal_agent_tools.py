"""The agent goal tools speak the goal API's own fields: list_goals reports
target date, progress, periodic target and linked account; the create-goal
proposal is exactly a POST /api/goals body. Every name and amount is made up."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import mcp_server.tools  # noqa: F401  (registers the tools)
from app.models.account import Account
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.schemas.goal import GoalCreate
from app.services import goal_service
from mcp_server.auth import CallContext
from mcp_server.registry import REGISTRY

TODAY = date(2026, 3, 18)  # a Wednesday


async def _savings(session: AsyncSession, user, name: str = "Fund", currency: str = "NZD") -> Account:
    acc = Account(id=uuid.uuid4(), user_id=user.id, name=name, type="savings",
                  balance=Decimal("0"), currency=currency)
    session.add(acc)
    await session.commit()
    return acc


@pytest.mark.asyncio
async def test_list_goals_reports_the_real_fields(session: AsyncSession, test_user, test_workspace, monkeypatch):
    monkeypatch.setattr(goal_service, "app_today", lambda: TODAY)
    everyday = await _savings(session, test_user, "Everyday", "BRL")
    fund = await _savings(session, test_user, "Holiday fund", "BRL")
    pair = uuid.uuid4()
    for acc, kind in ((everyday, "debit"), (fund, "credit")):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=acc.id, description="Save",
            amount=Decimal("30"), currency="BRL", date=date(2026, 3, 17), type=kind,
            source="manual", transfer_pair_id=pair, created_at=datetime.now(timezone.utc),
        ))
    session.add_all([
        Goal(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Car",
             target_amount=Decimal("1000"), current_amount=Decimal("250"), currency="BRL",
             target_date=date(2027, 1, 31), position=0),
        Goal(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Holiday",
             target_amount=None, contribution_amount=Decimal("50"), contribution_period="weekly",
             tracking_type="account", account_id=fund.id, currency="BRL", position=1),
    ])
    await session.commit()

    r = await REGISTRY["list_goals"].handler(session=session, ctx=CallContext(user_id=test_user.id))
    car, holiday = r["items"]
    for item in (car, holiday):
        assert not {"deadline", "category_id"} & set(item)

    assert car["target_date"] == "2027-01-31"
    assert (car["progress_pct"], car["progress_basis"]) == (25.0, "total")
    assert car["account_id"] is None and car["contribution_period"] is None

    assert holiday["target_amount"] is None
    assert (holiday["progress_pct"], holiday["progress_basis"]) == (60.0, "period")
    assert holiday["account_id"] == str(fund.id)
    assert holiday["account_name"] == "Holiday fund"
    assert holiday["tracking_type"] == "account"
    assert (holiday["contribution_amount"], holiday["contribution_period"]) == (50.0, "weekly")
    assert (holiday["period_contributed"], holiday["period_status"]) == (30.0, "pending")


@pytest.mark.asyncio
async def test_proposal_links_an_account_and_applies_as_proposed(session: AsyncSession, test_user):
    fund = await _savings(session, test_user)
    handler = REGISTRY["propose_create_goal"].handler
    ctx = CallContext(user_id=test_user.id, external=True)

    preview = await handler(
        session=session, ctx=CallContext(user_id=test_user.id),
        name="Pet fund", contribution_amount=15, contribution_period="biweekly", account_id=str(fund.id),
    )
    proposed = preview["proposed"]
    assert proposed["tracking_type"] == "account"
    assert proposed["account_id"] == str(fund.id)
    assert proposed["currency"] == "NZD"
    assert proposed["target_amount"] is None
    assert preview["account_name"] == "Fund"
    assert set(proposed) <= set(GoalCreate.model_fields)

    applied = await handler(
        session=session, ctx=ctx,
        name="Pet fund", contribution_amount=15, contribution_period="biweekly", account_id=str(fund.id),
        target_date="2026-12-01", apply=True,
    )
    assert applied["applied"] is True
    row = (await session.execute(select(Goal).where(Goal.id == uuid.UUID(applied["id"])))).scalar_one()
    assert row.account_id == fund.id
    assert row.tracking_type == "account"
    assert row.contribution_period == "biweekly"
    assert row.contribution_amount == Decimal("15.00")
    assert row.target_date == date(2026, 12, 1)


@pytest.mark.asyncio
async def test_proposal_keeps_what_is_already_saved_on_a_manual_goal(session: AsyncSession, test_user):
    r = await REGISTRY["propose_create_goal"].handler(
        session=session, ctx=CallContext(user_id=test_user.id, external=True),
        name="Laptop", target_amount=2000, current_amount=300, apply=True,
    )
    row = (await session.execute(select(Goal).where(Goal.id == uuid.UUID(r["id"])))).scalar_one()
    assert row.tracking_type == "manual"
    assert row.current_amount == Decimal("300.00")


@pytest.mark.asyncio
async def test_proposal_refuses_an_unknown_account(session: AsyncSession, test_user):
    r = await REGISTRY["propose_create_goal"].handler(
        session=session, ctx=CallContext(user_id=test_user.id),
        name="X", target_amount=10, account_id=str(uuid.uuid4()),
    )
    assert r["error"] == "account not found"
