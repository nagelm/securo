"""f008 on real PostgreSQL: existing accounts are numbered alphabetically per
workspace, so nothing moves until the user reorders, and downgrade drops the
column. Runs the migration's own downgrade()/upgrade() in a per-test schema.
Every name here is made up."""
import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.database import get_async_session
from app.main import app
from app.models.account import Account
from app.models.workspace import Workspace, WorkspaceMember

pytestmark = pytest.mark.asyncio

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "f008_account_position.py"
_SPEC = importlib.util.spec_from_file_location("f008_account_position", _PATH)
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


async def _run(session: AsyncSession, step: str) -> None:
    def go(sync_session):
        connection = sync_session.connection()
        schema = (connection.get_execution_options().get("schema_translate_map") or {}).get(None)
        # The per-test schema is a translate map, which raw SQL and
        # Alembic's ALTERs don't follow.
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, step)()

    await session.run_sync(go)
    await session.commit()


async def _schema(session: AsyncSession) -> str:
    connection = (await session.connection()).sync_connection
    assert connection is not None
    options = connection.get_execution_options()
    return options["schema_translate_map"][None]


async def _columns(session: AsyncSession) -> set[str]:
    schema = await _schema(session)
    rows = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = 'accounts'"
        ),
        {"schema": schema},
    )
    return {r[0] for r in rows}


async def _workspace(session: AsyncSession, user_id: uuid.UUID, name: str) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), name=name, kind="personal", created_by_user_id=user_id)
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=ws.id, user_id=user_id, role="owner"))
    await session.commit()
    return ws


def _account(user_id, ws, name, display_name=None, **over) -> Account:
    return Account(
        id=over.pop("id", uuid.uuid4()), user_id=user_id, workspace_id=ws.id, name=name,
        display_name=display_name, type="checking", balance=Decimal("0"), currency="BRL",
        position=over.pop("position", 9), **over,
    )


async def test_upgrade_numbers_accounts_alphabetically_per_workspace(session, test_user, test_workspace):
    other = await _workspace(session, test_user.id, "Second")
    same = [uuid.UUID(int=2), uuid.UUID(int=1)]
    session.add_all([
        _account(test_user.id, test_workspace, "zulu"),
        # Sorted by the name the user sees, ignoring case.
        _account(test_user.id, test_workspace, "Bank label", display_name="Mike"),
        _account(test_user.id, test_workspace, "alpha"),
        _account(test_user.id, test_workspace, "Bravo", is_closed=True),
        # Same visible name and bank name: the id decides, as in the API.
        _account(test_user.id, test_workspace, "Everyday", id=same[0]),
        _account(test_user.id, test_workspace, "Everyday", id=same[1]),
        _account(test_user.id, other, "Yankee"),
        _account(test_user.id, other, "Xray"),
    ])
    await session.commit()

    await _run(session, "downgrade")
    assert "position" not in await _columns(session)
    await _run(session, "upgrade")
    assert "position" in await _columns(session)

    schema = await _schema(session)
    rows = await session.execute(
        text(f'SELECT workspace_id, id, COALESCE(display_name, name), position FROM "{schema}".accounts')
    )
    by_ws: dict = {}
    for ws_id, acc_id, name, position in rows:
        by_ws.setdefault(ws_id, {})[(name, acc_id)] = position
    mine = sorted(by_ws[test_workspace.id].items(), key=lambda kv: kv[1])
    assert [(name, pos) for (name, _), pos in mine] == [
        ("alpha", 0), ("Bravo", 1), ("Everyday", 2), ("Everyday", 3), ("Mike", 4), ("zulu", 5),
    ]
    assert [acc_id for (name, acc_id), _ in mine if name == "Everyday"] == [same[1], same[0]]
    assert {name: pos for (name, _), pos in by_ws[other.id].items()} == {"Xray": 0, "Yankee": 1}


async def test_after_upgrade_the_api_lists_what_it_listed_before(client, auth_headers, session, test_user, test_workspace):
    for name, display in (("zulu", None), ("Bank label", "Mike"), ("alpha", None), ("Charlie", None)):
        session.add(_account(test_user.id, test_workspace, name, display, position=0))
    await session.commit()
    before = [a["id"] for a in (await client.get("/api/accounts", headers=auth_headers)).json()]

    await _run(session, "downgrade")
    await _run(session, "upgrade")

    after = (await client.get("/api/accounts", headers=auth_headers)).json()
    assert [a["id"] for a in after] == before
    assert [a["position"] for a in after] == [0, 1, 2, 3]
    assert [a["display_name"] or a["name"] for a in after] == ["alpha", "Charlie", "Mike", "zulu"]


async def test_an_empty_table_upgrades_and_downgrades(session):
    await _run(session, "downgrade")
    await _run(session, "upgrade")
    assert "position" in await _columns(session)


async def test_reordering_on_postgresql(client, auth_headers, session, test_user, test_workspace):
    accounts = [_account(test_user.id, test_workspace, n, position=i) for i, n in enumerate("ABCDE")]
    session.add_all(accounts)
    await session.commit()
    ids = {a.name: str(a.id) for a in accounts}

    response = await client.put(
        "/api/accounts/order", headers=auth_headers,
        json={"account_ids": [ids["E"], ids["A"], ids["C"]]},
    )
    assert response.status_code == 204, response.text

    listed = (await client.get("/api/accounts", headers=auth_headers)).json()
    assert [a["name"] for a in listed] == ["E", "B", "A", "D", "C"]
