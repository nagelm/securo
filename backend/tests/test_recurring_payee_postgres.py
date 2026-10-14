"""Fork: the recurring payee tests again on real PostgreSQL, plus f012's own
upgrade()/downgrade() (with its backfill) in a per-test schema. Every name and
amount is made up."""
from datetime import date

import pytest
from sqlalchemy import text

from tests import test_forecast_postgres as _pg
from tests.test_recurring_payee import (  # noqa: F401  (collected here on PostgreSQL)
    _payee,
    _recurring,
    _tx,
    manual_account,
    migration,
    test_api_create_read_update_with_payee,
    test_api_rejects_a_payee_of_another_workspace,
    test_backfill,
    test_generated_placeholder_carries_the_payee,
    test_linked_real_row_takes_the_payee_only_when_it_has_none,
    test_payee_merge_and_delete_follow_to_recurring,
)

pytestmark = pytest.mark.asyncio

# The PostgreSQL fixtures of test_forecast_postgres, under the names the tests ask for.
session = _pg.session
client = _pg.client
clean_db = _pg.clean_db


async def _run(session, step: str) -> None:
    def go(sync_session):
        connection = sync_session.connection()
        schema = (connection.get_execution_options().get("schema_translate_map") or {}).get(None)
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, step)()

    await session.run_sync(go)
    await session.commit()


async def _schema(session) -> str:
    connection = (await session.connection()).sync_connection
    assert connection is not None
    return connection.get_execution_options()["schema_translate_map"][None]


async def _has_column(session) -> bool:
    rows = await session.execute(
        text("SELECT 1 FROM information_schema.columns WHERE table_schema = :schema "
             "AND table_name = 'recurring_transactions' AND column_name = 'payee_id'"),
        {"schema": await _schema(session)},
    )
    return rows.first() is not None


async def test_migration_downgrades_and_upgrades_with_backfill(
    session, test_user, test_workspace, manual_account  # noqa: F811
):
    ws = test_workspace.id
    netflix = await _payee(session, test_user, ws, "Netflix")
    acme = await _payee(session, test_user, ws, "Acme")
    linked = await _recurring(session, test_user, ws, manual_account, "Streaming")
    await _tx(session, test_user, manual_account, linked, date(2026, 2, 5), payee_id=netflix.id)
    by_name = await _recurring(session, test_user, ws, manual_account, "Acme insurance")
    none = await _recurring(session, test_user, ws, manual_account, "Rent")
    await session.commit()
    ids = {"linked": linked.id, "by_name": by_name.id, "none": none.id}
    session.expunge_all()

    await _run(session, "downgrade")
    assert not await _has_column(session)

    await _run(session, "upgrade")
    assert await _has_column(session)
    rows = dict(
        (await session.execute(
            text(f'SELECT id, payee_id FROM "{await _schema(session)}".recurring_transactions')
        )).all()
    )
    assert rows[ids["linked"]] == netflix.id
    assert rows[ids["by_name"]] == acme.id
    assert rows[ids["none"]] is None
