"""f009 on real PostgreSQL: existing categories get the default key their name
carries, one per key in a workspace, and downgrade drops the column. Runs the
migration's own downgrade()/upgrade() in a per-test schema, and the
default-key lookups against the partial unique index. Every name here is
made up."""
import importlib.util
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.database import get_async_session
from app.main import app
from app.services.category_service import (
    DEFAULT_CATEGORIES_I18N,
    create_default_categories,
    default_category_names,
)
from tests.test_category_default_key import (  # noqa: F401  (collected here on PostgreSQL)
    test_a_key_is_unique_per_workspace,
    test_a_renamed_default_is_still_found,
    test_repayment_legs_follow_a_renamed_category,
    test_rule_pack_seeding_sets_the_key_and_skips_a_renamed_default,
    test_seeded_defaults_carry_their_key,
    test_sync_hint_falls_back_once_the_default_is_deleted,
    test_sync_hint_resolves_a_renamed_default,
    test_the_keyed_category_wins_over_a_same_named_one,
    test_the_same_key_in_two_workspaces_and_many_unkeyed_rows_are_fine,
    test_unkeyed_rows_still_match_by_name,
)
from tests.test_loan_repayments_migration import _workspace

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "f009_category_default_key.py"
_SPEC = importlib.util.spec_from_file_location("f009_category_default_key", _PATH)
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
    return connection.get_execution_options()["schema_translate_map"][None]


async def _columns(session: AsyncSession) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = 'categories'"
        ),
        {"schema": await _schema(session)},
    )
    return {r[0] for r in rows}


async def _indexes(session: AsyncSession) -> set[str]:
    rows = await session.execute(
        text("SELECT indexname FROM pg_indexes WHERE schemaname = :schema AND tablename = 'categories'"),
        {"schema": await _schema(session)},
    )
    return {r[0] for r in rows}


async def _keys(session: AsyncSession, workspace_id: uuid.UUID) -> dict:
    schema = await _schema(session)
    rows = await session.execute(
        text(f'SELECT id, name, default_key FROM "{schema}".categories WHERE workspace_id = :ws'),
        {"ws": workspace_id},
    )
    return {(r.name, r.id): r.default_key for r in rows}


async def _insert(session: AsyncSession, user_id, ws_id, name: str, cat_id: uuid.UUID | None = None) -> uuid.UUID:
    """A category as it was before f009: no default_key column."""
    schema = await _schema(session)
    cat_id = cat_id or uuid.uuid4()
    await session.execute(
        text(
            f'INSERT INTO "{schema}".categories '
            "(id, user_id, workspace_id, name, icon, color, is_system, is_hidden, treat_as_transfer, is_ignored) "
            "VALUES (:id, :user_id, :ws, :name, 'tag', '#000000', false, false, false, false)"
        ),
        {"id": cat_id, "user_id": user_id, "ws": ws_id, "name": name},
    )
    await session.commit()
    return cat_id


def test_the_snapshot_matches_the_template_at_this_revision():
    assert {key: set(names) for key, names in migration.NAMES.items()} == {
        key: set(default_category_names(key)) for key in DEFAULT_CATEGORIES_I18N
    }


async def test_upgrade_backfills_keys_by_name_per_workspace(session, test_user, test_workspace):
    other = await _workspace(session, test_user, "Second")
    await create_default_categories(session, test_user.id, "de", workspace_id=other.id)

    await _run(session, "downgrade")
    assert "default_key" not in await _columns(session)
    assert migration.INDEX not in await _indexes(session)

    # A workspace seeded in English: one default renamed, one user-made
    # category, and two categories carrying the Transfers name.
    first = await _insert(session, test_user.id, test_workspace.id, "Transfers", uuid.UUID(int=1))
    second = await _insert(session, test_user.id, test_workspace.id, "Transfers", uuid.UUID(int=2))
    groceries = await _insert(session, test_user.id, test_workspace.id, "Groceries", uuid.UUID(int=3))
    renamed = await _insert(session, test_user.id, test_workspace.id, "Example repayments")
    mine = await _insert(session, test_user.id, test_workspace.id, "Example hobby")
    # Groceries in another language, after the English one by id.
    portuguese = await _insert(session, test_user.id, test_workspace.id, "Mercado", uuid.UUID(int=4))

    await _run(session, "upgrade")
    assert "default_key" in await _columns(session)
    assert migration.INDEX in await _indexes(session)

    keys = await _keys(session, test_workspace.id)
    assert keys == {
        ("Transfers", first): "transfers",
        # Same name again: the first by id has the key, as the old name
        # lookup picked it.
        ("Transfers", second): None,
        ("Groceries", groceries): "groceries",
        # Already renamed: nothing says which default it was.
        ("Example repayments", renamed): None,
        ("Example hobby", mine): None,
        # The English Groceries, first by id, already has the key.
        ("Mercado", portuguese): None,
    }

    # The other workspace, seeded in German, gets every key on its own.
    other_keys = {name: key for (name, _), key in (await _keys(session, other.id)).items()}
    assert other_keys == {data.get("de", data["en"]): key for key, data in DEFAULT_CATEGORIES_I18N.items()}


async def test_the_backfilled_index_rejects_a_second_category_for_a_key(session, test_user, test_workspace):
    await create_default_categories(session, test_user.id, "en", workspace_id=test_workspace.id)
    await _run(session, "downgrade")
    await _run(session, "upgrade")

    schema = await _schema(session)
    with pytest.raises(IntegrityError):
        await session.execute(
            text(
                f'INSERT INTO "{schema}".categories (id, user_id, workspace_id, name, icon, color, is_system, '
                "is_hidden, treat_as_transfer, is_ignored, default_key) VALUES (:id, :user_id, :ws, 'Example', "
                "'tag', '#000000', false, false, false, false, 'transfers')"
            ),
            {"id": uuid.uuid4(), "user_id": test_user.id, "ws": test_workspace.id},
        )
    await session.rollback()


async def test_an_empty_table_upgrades_and_downgrades(session):
    await _run(session, "downgrade")
    await _run(session, "upgrade")
    assert "default_key" in await _columns(session)
    await _run(session, "downgrade")
    assert "default_key" not in await _columns(session)
