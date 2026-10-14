"""Fork: the Forecast tests again on real PostgreSQL (the cash-flow
projection and the category estimate run real SQL), plus f010's own
upgrade()/downgrade() in a per-test schema. Every figure is made up."""
import importlib.util
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
from tests.test_forecast import (  # noqa: F401  (collected here on PostgreSQL)
    test_api_crud_and_forecast,
    test_api_is_workspace_scoped,
    test_api_scenario_change_validation,
    test_api_validation,
    test_api_viewer_can_read_but_not_write,
    test_base_line_counts_recurring_and_expected_payments,
    test_cash_flow_report_counts_expected_payments_but_not_scenarios,
    test_disabled_scenarios_are_left_out_unless_asked_for,
    test_expected_payment_on_a_loan_account_is_not_cash,
    test_income_change,
    test_lowest_point_and_its_date,
    test_matched_payment_drops_out_of_the_forecast,
    test_matching_only_looks_at_the_rows_given,
    test_matching_tolerance,
    test_one_bank_row_settles_one_payment,
    test_one_off_and_repeating_scenario_items,
    test_overdue_unmatched_payment_is_still_expected_tomorrow,
    test_recurring_change,
    test_spending_change_amount_between_dates,
    test_spending_change_does_nothing_with_the_estimate_off,
    test_spending_change_percent,
    test_spending_cut_cannot_go_below_nothing,
    test_spending_estimate_feeds_the_base_only_when_switched_on,
    test_spending_estimate_per_category,
)

pytestmark = pytest.mark.asyncio

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "f010_forecast.py"
_SPEC = importlib.util.spec_from_file_location("f010_forecast", _PATH)
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
        # The per-test schema is a translate map, which Alembic's DDL doesn't follow.
        connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            getattr(migration, step)()

    await session.run_sync(go)
    await session.commit()


async def _tables(session: AsyncSession) -> set[str]:
    connection = (await session.connection()).sync_connection
    assert connection is not None
    schema = connection.get_execution_options()["schema_translate_map"][None]
    rows = await session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
        {"schema": schema},
    )
    return {r[0] for r in rows}


async def test_migration_downgrades_and_upgrades(session):
    assert {"forecast_scenarios", "forecast_items"} <= await _tables(session)
    await _run(session, "downgrade")
    assert not {"forecast_scenarios", "forecast_items"} & await _tables(session)
    await _run(session, "upgrade")
    assert {"forecast_scenarios", "forecast_items"} <= await _tables(session)


async def test_migrated_tables_take_the_app_rows(session, test_user, test_workspace):
    """After a real upgrade (not create_all), the ORM can write and cascade."""
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import delete, select

    from app.models.forecast import ForecastItem, ForecastScenario

    await _run(session, "downgrade")
    await _run(session, "upgrade")
    scenario = ForecastScenario(
        user_id=test_user.id, workspace_id=test_workspace.id, name="S", color="#000000",
    )
    session.add(scenario)
    await session.flush()
    session.add(ForecastItem(
        user_id=test_user.id, workspace_id=test_workspace.id, scenario_id=scenario.id, kind="spending_change",
        currency="BRL", change_mode="percent", percent=Decimal("-50"), start_date=date(2030, 1, 1),
    ))
    await session.commit()
    # A core DELETE, so the migration's ON DELETE CASCADE does the work.
    await session.execute(delete(ForecastScenario).where(ForecastScenario.id == scenario.id))
    await session.commit()
    session.expunge_all()
    assert (await session.execute(select(ForecastItem))).scalars().all() == []
