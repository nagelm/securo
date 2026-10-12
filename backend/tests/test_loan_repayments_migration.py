"""f005: existing workspaces get Loan repayments; nothing else changes.

Runs the migration's own upgrade()/downgrade() against the test database.
Every name and amount is made up.
"""

import importlib.util
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.category_group import CategoryGroup
from app.models.transaction import Transaction
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember
from app.services.category_service import DEFAULT_CATEGORIES_I18N, create_default_categories, find_default_category
from app.services.loan_repayment_category import LOAN_REPAYMENTS_KEY

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "f005_loan_repayments_category.py"
_SPEC = importlib.util.spec_from_file_location("f005_loan_repayments_category", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


async def _run(session: AsyncSession, step: str) -> None:
    def go(sync_session):
        connection = sync_session.connection()
        schema = (connection.get_execution_options().get("schema_translate_map") or {}).get(None)
        if schema and connection.dialect.name == "postgresql":
            # The PostgreSQL tests isolate each test in a schema through a
            # translate map, which plain `sa.table()` clauses don't follow.
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, step)()

    await session.run_sync(go)
    await session.commit()


async def _workspace(session: AsyncSession, user: User, name: str) -> Workspace:
    ws = Workspace(id=uuid.uuid4(), name=name, created_by_user_id=user.id)
    session.add(ws)
    await session.flush()
    session.add(WorkspaceMember(id=uuid.uuid4(), workspace_id=ws.id, user_id=user.id, role="owner"))
    await session.commit()
    return ws


async def _seeded_before_f005(session: AsyncSession, user: User, ws: Workspace, lang: str) -> None:
    """Seed the defaults as they were before Loan repayments existed."""
    await create_default_categories(session, user.id, lang, workspace_id=ws.id)
    cat_id = await find_default_category(session, ws.id, LOAN_REPAYMENTS_KEY)
    await session.delete(await session.get(Category, cat_id))
    await session.commit()


async def _in(session: AsyncSession, ws: Workspace) -> list[Category]:
    names = set(migration.NAMES.values())
    return list(
        (
            await session.execute(
                select(Category).where(Category.workspace_id == ws.id, Category.name.in_(names))
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )


def test_snapshot_matches_the_template():
    template = DEFAULT_CATEGORIES_I18N[LOAN_REPAYMENTS_KEY]
    assert migration.NAMES == {lang: template[lang] for lang in migration.NAMES}
    assert len(migration.NAMES) == len(template) - 3  # icon, color, treat_as_transfer
    assert (migration.ICON, migration.COLOR) == (template["icon"], template["color"])
    for lang, markers in migration.LANGUAGE_MARKERS.items():
        for key, name in zip(("transfers", "housing", "groceries", "other"), markers):
            assert DEFAULT_CATEGORIES_I18N[key][lang] == name


@pytest.mark.asyncio
@pytest.mark.parametrize("lang", ["en", "pt-BR", "pt-PT", "de", "fr", "nl", "sk"])
async def test_upgrade_adds_it_in_the_workspace_language_beside_transfers(
    session: AsyncSession, test_user: User, test_workspace: Workspace, lang
):
    await _seeded_before_f005(session, test_user, test_workspace, lang)
    transfers = await session.get(Category, await find_default_category(session, test_workspace.id, "transfers"))

    await _run(session, "upgrade")

    (cat,) = await _in(session, test_workspace)
    assert transfers is not None
    assert cat.id == migration.category_id_for(test_workspace.id)
    assert cat.name == DEFAULT_CATEGORIES_I18N[LOAN_REPAYMENTS_KEY][lang]
    assert (cat.treat_as_transfer, cat.is_system, cat.is_ignored, cat.is_hidden) == (True, True, False, False)
    assert cat.group_id is not None and cat.group_id == transfers.group_id
    assert cat.user_id == test_user.id
    assert (cat.icon, cat.color) == (migration.ICON, migration.COLOR)


@pytest.mark.asyncio
async def test_upgrade_is_idempotent_and_leaves_transactions_alone(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    await _seeded_before_f005(session, test_user, test_workspace, "en")
    transfers_id = await find_default_category(session, test_workspace.id, "transfers")
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Test loan",
        type="loan", balance=Decimal("-1000"), currency="NZD",
    )
    session.add(account)
    rows = [
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
            description="Example repayment", amount=Decimal("100"), currency="NZD", date=app_today(),
            type="credit", source="sync", status="posted", category_id=category_id,
            created_at=datetime.now(timezone.utc),
        )
        for category_id in (None, transfers_id)
    ]
    session.add_all(rows)
    await session.commit()

    await _run(session, "upgrade")
    await _run(session, "upgrade")

    assert len(await _in(session, test_workspace)) == 1
    after = {
        tx.id: tx.category_id
        for tx in (
            await session.execute(select(Transaction).execution_options(populate_existing=True))
        ).scalars()
    }
    assert after == {rows[0].id: None, rows[1].id: transfers_id}


@pytest.mark.asyncio
async def test_upgrade_skips_unseeded_workspaces_and_ones_that_have_it(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    empty = await _workspace(session, test_user, "Never seeded")
    # Seeded after the template gained the category: already has it.
    await create_default_categories(session, test_user.id, "en", workspace_id=test_workspace.id)
    existing = await _in(session, test_workspace)
    # One the user made by hand, under a translated name.
    own = await _workspace(session, test_user, "Own category")
    await _seeded_before_f005(session, test_user, own, "en")
    session.add(Category(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=own.id, name="Kredittilgungen",
        icon="tag", color="#000000",
    ))
    await session.commit()

    await _run(session, "upgrade")

    count = (
        await session.execute(select(func.count()).select_from(Category).where(Category.workspace_id == empty.id))
    ).scalar_one()
    assert count == 0
    assert [c.id for c in await _in(session, test_workspace)] == [c.id for c in existing]
    assert len(await _in(session, own)) == 1


@pytest.mark.asyncio
async def test_upgrade_falls_back_to_the_other_group_and_the_owner(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    await _seeded_before_f005(session, test_user, test_workspace, "en")
    transfers = await session.get(Category, await find_default_category(session, test_workspace.id, "transfers"))
    await session.delete(transfers)
    await session.commit()
    other_group = (
        await session.execute(
            select(CategoryGroup).where(CategoryGroup.workspace_id == test_workspace.id, CategoryGroup.name == "Other")
        )
    ).scalar_one()

    await _run(session, "upgrade")

    (cat,) = await _in(session, test_workspace)
    assert (cat.name, cat.group_id, cat.user_id) == ("Loan repayments", other_group.id, test_user.id)


@pytest.mark.asyncio
async def test_downgrade_removes_only_unused_categories_it_made(
    session: AsyncSession, test_user: User, test_workspace: Workspace
):
    await _seeded_before_f005(session, test_user, test_workspace, "en")
    used_ws = await _workspace(session, test_user, "Used")
    await _seeded_before_f005(session, test_user, used_ws, "en")
    seeded_ws = await _workspace(session, test_user, "Seeded with it")
    await create_default_categories(session, test_user.id, "en", workspace_id=seeded_ws.id)

    await _run(session, "upgrade")
    account = Account(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=used_ws.id, name="Test loan",
        type="loan", balance=Decimal("-1000"), currency="NZD",
    )
    session.add(account)
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=used_ws.id, account_id=account.id,
        description="Example repayment", amount=Decimal("100"), currency="NZD", date=app_today(),
        type="credit", source="sync", status="posted", category_id=migration.category_id_for(used_ws.id),
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    await _run(session, "downgrade")

    assert await _in(session, test_workspace) == []
    assert [c.id for c in await _in(session, used_ws)] == [migration.category_id_for(used_ws.id)]
    assert len(await _in(session, seeded_ws)) == 1
