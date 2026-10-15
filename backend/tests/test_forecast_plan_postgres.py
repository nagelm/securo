"""Fork: the Forecast plan tests (expected transfers, repeating expected
payments, the account view, budgets) again on real PostgreSQL, plus
f014-f016's own upgrade()/downgrade() in a per-test schema. Every figure is
made up."""
import importlib.util
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.models.forecast import ForecastItem, ForecastItemMatch
from tests import test_forecast_postgres as _pg
from tests.test_forecast import _account, _tx
from tests.test_forecast_plan import (  # noqa: F401  (collected here on PostgreSQL)
    test_a_budget_on_another_account_stays_out_of_an_account_view,
    test_a_budget_replaces_the_spending_estimate_over_its_dates,
    test_a_budget_switches_off_a_spending_change_over_its_dates,
    test_a_category_budget_switches_off_only_that_category,
    test_a_series_date_past_when_it_was_entered_may_match_an_older_row,
    test_account_view_counts_a_loan_change_on_the_account_that_pays_the_loan,
    test_account_view_history_estimate_and_recurring_keep_to_its_accounts,
    test_account_view_keeps_to_the_cash_accounts,
    test_account_view_leaves_out_payments_with_no_account,
    test_account_view_sees_one_leg_of_an_expected_transfer,
    test_api_account_view,
    test_api_budget,
    test_api_expected_transfer,
    test_api_repeating_expected_payment,
    test_budgets_are_not_matched,
    test_budgets_project_with_the_estimate_off_and_cover_the_union_of_their_dates,
    test_each_occurrence_is_matched_on_its_own,
    test_expected_transfer_between_cash_accounts_is_not_counted_twice,
    test_expected_transfer_matches_the_debit_leg_of_a_bank_transfer,
    test_expected_transfer_to_a_loan_leaves_cash,
    test_overdue_occurrences_are_still_expected_tomorrow,
    test_repeating_expected_payment_projects_each_occurrence,
    test_repeating_transfer_between_cash_accounts_leaves_the_line_unchanged,
    test_scenario_transfer_counts_its_legs_in_scope,
)

pytestmark = pytest.mark.asyncio

# The PostgreSQL fixtures of test_forecast_postgres, under the names the tests ask for.
session = _pg.session
client = _pg.client
clean_db = _pg.clean_db


def _load(name: str):
    path = Path(__file__).resolve().parent.parent / "alembic" / "versions" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


F014 = _load("f014_forecast_transfers")
F015 = _load("f015_forecast_item_matches")
F016 = _load("f016_forecast_budgets")


async def _schema_facts(session) -> tuple[set[str], set[str]]:
    """forecast_items' columns and the schema's tables."""
    connection = (await session.connection()).sync_connection
    assert connection is not None
    schema = connection.get_execution_options()["schema_translate_map"][None]
    columns = await session.execute(
        text("SELECT column_name FROM information_schema.columns "
             "WHERE table_schema = :schema AND table_name = 'forecast_items'"),
        {"schema": schema},
    )
    tables = await session.execute(
        text("SELECT table_name FROM information_schema.tables WHERE table_schema = :schema"),
        {"schema": schema},
    )
    return {r[0] for r in columns}, {r[0] for r in tables}


async def test_plan_migrations_downgrade_and_upgrade(session, test_user, test_workspace):
    joint = await _account(session, test_user.id)
    savings = await _account(session, test_user.id)
    common = {"user_id": test_user.id, "workspace_id": test_workspace.id, "currency": "BRL", "type": "debit",
              "amount": Decimal("100")}
    session.add_all([
        ForecastItem(kind="one_off", date=date(2030, 1, 1), account_id=joint.id,
                     transfer_account_id=savings.id, **common),
        ForecastItem(kind="budget", frequency="weekly", start_date=date(2030, 1, 1), **common),
        ForecastItem(kind="one_off", date=date(2030, 1, 1), account_id=joint.id, **common),
    ])
    await session.commit()

    await _pg._run(session, "downgrade", F016)
    await _pg._run(session, "downgrade", F015)
    await _pg._run(session, "downgrade", F014)
    columns, tables = await _schema_facts(session)
    assert "transfer_account_id" not in columns and "forecast_item_matches" not in tables
    # Budgets and transfers can't live under the old schema; the plain payment stays.
    kinds = await session.execute(text("SELECT kind FROM forecast_items"))
    assert [r[0] for r in kinds] == ["one_off"]

    await _pg._run(session, "upgrade", F014)
    await _pg._run(session, "upgrade", F015)
    await _pg._run(session, "upgrade", F016)
    columns, tables = await _schema_facts(session)
    assert "transfer_account_id" in columns and "forecast_item_matches" in tables

    # The ORM writes every new shape after a real upgrade.
    series = ForecastItem(kind="repeating", frequency="weekly", start_date=date(2030, 1, 1), account_id=joint.id,
                          transfer_account_id=savings.id, **common)
    session.add_all([series, ForecastItem(kind="budget", frequency="monthly", start_date=date(2030, 1, 1), **common)])
    await session.flush()
    tx = await _tx(session, test_user.id, joint.id, 100, "debit", date(2030, 1, 1))
    session.add(ForecastItemMatch(item_id=series.id, occurrence_date=date(2030, 1, 1), transaction_id=tx.id))
    await session.commit()
    # Deleting the bank row deletes its match (ON DELETE CASCADE from f015).
    await session.execute(text("DELETE FROM transactions WHERE id = :id"), {"id": tx.id})
    await session.commit()
    session.expunge_all()
    assert (await session.execute(select(func.count(ForecastItemMatch.id)))).scalar_one() == 0
