"""Goal contribution targets on real PostgreSQL: goals stay display-only
for every report, contributions read the same, and migration f007 goes up
and down. Every name and amount here is made up."""
import importlib.util
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.core.database import get_async_session
from app.main import app
from app.models.account import Account
from app.models.goal import Goal
from app.models.transaction import Transaction
from app.services.goal_contributions import compute_contribution_progress

pytestmark = pytest.mark.asyncio

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "f007_goal_contribution_targets.py"
_SPEC = importlib.util.spec_from_file_location("f007_goal_contribution_targets", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


@pytest_asyncio.fixture
async def session(postgres_sessions):
    async with postgres_sessions() as session:
        yield session
        await session.rollback()


@pytest.fixture
def clean_db(postgres_sessions):
    """The reused user fixtures start in a fresh per-test PostgreSQL schema."""


@pytest_asyncio.fixture
async def client(postgres_sessions, monkeypatch):
    async def pg_session():
        async with postgres_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_async_session, pg_session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def _account(session, user, name: str, type_: str = "savings") -> Account:
    acc = Account(id=uuid.uuid4(), user_id=user.id, name=name, type=type_, balance=Decimal("0"), currency="BRL")
    session.add(acc)
    await session.commit()
    return acc


def _tx(user, account, amount: str, day: date, type_: str, **kw) -> Transaction:
    return Transaction(
        id=uuid.uuid4(), user_id=user.id, account_id=account.id, description=f"{type_} {amount}",
        amount=Decimal(amount), currency="BRL", date=day, type=type_, source="manual",
        created_at=datetime.now(timezone.utc), **kw,
    )


async def _transfer(session, user, src, dst, amount: str, day: date) -> None:
    pair = uuid.uuid4()
    session.add(_tx(user, src, amount, day, "debit", transfer_pair_id=pair))
    session.add(_tx(user, dst, amount, day, "credit", transfer_pair_id=pair))
    await session.commit()


async def test_goals_never_change_any_report(client, auth_headers, session, test_user):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Fund")
    today = date.today()
    await _transfer(session, test_user, everyday, fund, "50.00", today)
    session.add(_tx(test_user, everyday, "500.00", today, "credit"))
    session.add(_tx(test_user, everyday, "80.00", today, "debit"))
    session.add(_tx(test_user, fund, "1.25", today, "credit"))
    await session.commit()

    month = today.replace(day=1).isoformat()
    endpoints = [
        ("/api/accounts", {}),
        ("/api/dashboard/summary", {"month": month}),
        ("/api/reports/net-worth", {}),
        ("/api/reports/income-expenses", {}),
        ("/api/reports/cash-flow", {}),
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
    fund_goal = next(g for g in goals if g["name"] == "Fund")
    assert float(fund_goal["contribution_progress"]["current"]["contributed"]) == 50.0
    assert await snapshot() == before


async def test_contributions_on_postgres(session, test_user):
    everyday = await _account(session, test_user, "Everyday", "checking")
    fund = await _account(session, test_user, "Fund")
    await _transfer(session, test_user, everyday, fund, "40.00", date(2026, 3, 3))
    await _transfer(session, test_user, fund, everyday, "15.00", date(2026, 3, 20))
    session.add(_tx(test_user, fund, "2.00", date(2026, 3, 31), "credit"))  # interest
    await session.commit()
    progress = await compute_contribution_progress(
        session, account_ids=[fund.id], period="monthly", target=Decimal("25"),
        currency="BRL", today=date(2026, 4, 2), count=2,
    )
    assert progress.history[0].contributed == Decimal("25.00")
    assert progress.history[0].status == "met"
    assert progress.current.status == "pending"
    assert progress.streak == 1


async def _run(session, step: str) -> None:
    def go(sync_session):
        connection = sync_session.connection()
        schema = (connection.get_execution_options().get("schema_translate_map") or {}).get(None)
        if schema:
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, step)()

    await session.run_sync(go)
    await session.commit()


async def _columns(session) -> dict[str, str]:
    def go(sync_session):
        connection = sync_session.connection()
        schema = connection.get_execution_options()["schema_translate_map"][None]
        rows = connection.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = :s AND table_name = 'goals'"
            ),
            {"s": schema},
        )
        return {name: nullable for name, nullable in rows}

    return await session.run_sync(go)


async def _sql(session, statement: str, params: dict | None = None):
    """Raw SQL in the test's own schema (a translate map doesn't reach text())."""
    schema = (await session.connection()).sync_connection.get_execution_options()["schema_translate_map"][None]
    await session.execute(text(f'SET LOCAL search_path TO "{schema}"'))
    return await session.execute(text(statement), params or {})


async def test_f007_downgrade_and_upgrade(session, test_user, test_workspace):
    ids = {"u": test_user.id, "w": test_workspace.id}
    ongoing = Goal(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Ongoing",
        target_amount=None, contribution_amount=Decimal("20"), contribution_period="weekly",
    )
    total = Goal(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Total",
        target_amount=Decimal("500"),
    )
    session.add_all([ongoing, total])
    await session.commit()

    await _run(session, "downgrade")
    cols = await _columns(session)
    assert "contribution_amount" not in cols
    assert "contribution_period" not in cols
    assert cols["target_amount"] == "NO"
    rows = dict((await _sql(session, "SELECT name, target_amount FROM goals")).all())
    assert rows == {"Ongoing": Decimal("0.00"), "Total": Decimal("500.00")}

    await _run(session, "upgrade")
    cols = await _columns(session)
    assert cols["contribution_amount"] == "YES"
    assert cols["contribution_period"] == "YES"
    assert cols["target_amount"] == "YES"

    # The checks are back: a goal with no target at all is refused.
    with pytest.raises(IntegrityError):
        await _sql(
            session,
            (
                "INSERT INTO goals (id, user_id, workspace_id, name, current_amount, initial_amount, "
                "currency, tracking_type, status, position) VALUES "
                "(:id, :u, :w, 'None', 0, 0, 'BRL', 'manual', 'active', 0)"
            ),
            {"id": uuid.uuid4(), **ids},
        )
    await session.rollback()
    with pytest.raises(IntegrityError):
        await _sql(
            session,
            (
                "INSERT INTO goals (id, user_id, workspace_id, name, target_amount, contribution_amount, "
                "current_amount, initial_amount, currency, tracking_type, status, position) VALUES "
                "(:id, :u, :w, 'Half', 10, 5, 0, 0, 'BRL', 'manual', 'active', 0)"
            ),
            {"id": uuid.uuid4(), **ids},
        )
    await session.rollback()
