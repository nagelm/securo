"""Fork: the Forecast loan and history tests again on real PostgreSQL (the balances and
the cash-flow projection run real SQL), plus f011's own upgrade()/downgrade()
in a per-test schema. Every figure is made up."""
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, insert, select, text

from app.models.forecast import ForecastItem, ForecastScenario
from tests.test_forecast_loans import (  # noqa: F401  (collected here on PostgreSQL)
    test_a_loan_no_schedule_pays_takes_its_repayments_from_cash,
    test_a_loan_without_details_adds_no_repayments,
    test_a_lump_sum_that_clears_the_loan_frees_the_repayments,
    test_a_plain_recurring_transfer_into_the_loan_counts_as_its_schedule,
    test_a_scheduled_loan_payment_is_not_counted_twice,
    test_api_loan_change_needs_a_scenario,
    test_api_loan_change_needs_an_open_loan_in_the_workspace,
    test_api_loan_change_round_trip,
    test_api_loan_change_validation,
    test_changes_to_one_loan_leave_the_others_alone,
    test_each_loan_gets_its_base_projection,
    test_loans_without_enough_details_are_listed_without_a_projection,
    test_lump_sum,
    test_paying_more_a_week_pays_off_earlier,
    test_rate_change,
    test_repayments_stop_at_payoff,
)
from tests import test_forecast_postgres as _pg
from tests.test_forecast_history import (  # noqa: F401  (collected here on PostgreSQL)
    test_each_loan_has_its_balance_history,
    test_history_leaves_out_loans_and_investments,
    test_history_spans_as_far_back_as_the_forecast_looks_ahead,
)

pytestmark = pytest.mark.asyncio

# The PostgreSQL fixtures of test_forecast_postgres, under the names the tests ask for.
session = _pg.session
client = _pg.client
clean_db = _pg.clean_db

_PATH = Path(__file__).resolve().parent.parent / "alembic" / "versions" / "f011_forecast_loan_changes.py"
_SPEC = importlib.util.spec_from_file_location("f011_forecast_loan_changes", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
migration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(migration)


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


async def _columns(session) -> set[str]:
    connection = (await session.connection()).sync_connection
    assert connection is not None
    schema = connection.get_execution_options()["schema_translate_map"][None]
    rows = await session.execute(
        text("SELECT column_name FROM information_schema.columns "
             "WHERE table_schema = :schema AND table_name = 'forecast_items'"),
        {"schema": schema},
    )
    return {r[0] for r in rows}


async def _loan_change(session, user, workspace, scenario_id) -> None:
    session.add(ForecastItem(
        user_id=user.id, workspace_id=workspace.id, scenario_id=scenario_id, kind="loan_change",
        currency="BRL", change_mode="rate", percent=Decimal("6.25"), recalculate_repayment=False,
        start_date=date(2030, 1, 1),
    ))
    await session.commit()


async def test_migration_downgrades_and_upgrades(session, test_user, test_workspace):
    scenario = ForecastScenario(user_id=test_user.id, workspace_id=test_workspace.id, name="S", color="#000000")
    session.add(scenario)
    await session.commit()
    await _loan_change(session, test_user, test_workspace, scenario.id)

    await _run(session, "downgrade")
    assert "recalculate_repayment" not in await _columns(session)
    count = await session.execute(select(func.count(ForecastItem.id)))
    assert count.scalar_one() == 0  # loan changes can't live under the old constraints

    await _run(session, "upgrade")
    assert "recalculate_repayment" in await _columns(session)
    await _loan_change(session, test_user, test_workspace, scenario.id)
    session.expunge_all()
    item = (await session.execute(select(ForecastItem))).scalar_one()
    assert (item.kind, item.change_mode, item.recalculate_repayment) == ("loan_change", "rate", False)


async def test_old_constraints_refuse_loan_changes_after_downgrade(session, test_user, test_workspace):
    from sqlalchemy.exc import IntegrityError

    scenario = ForecastScenario(user_id=test_user.id, workspace_id=test_workspace.id, name="S", color="#000000")
    session.add(scenario)
    await session.commit()
    await _run(session, "downgrade")

    # A core insert names only these columns, so the dropped one isn't asked for.
    with pytest.raises(IntegrityError):
        await session.execute(insert(ForecastItem).values(
            user_id=test_user.id, workspace_id=test_workspace.id, scenario_id=scenario.id,
            kind="loan_change", currency="BRL", description="",
        ))
    await session.rollback()

