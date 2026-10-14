"""Fork: the Forecast chart's history. As much real cash-and-cards balance
before today as the forecast looks ahead, and each loan's balance owed over
the same span. Every figure is made up.

Runs on SQLite here and again on PostgreSQL from
test_forecast_loans_postgres.py.
"""
import calendar
from datetime import timedelta

import pytest

from app.core.app_clock import app_today
from app.services import forecast_service
from app.services.report_service import _add_months
from tests.test_forecast import _account, _tx
from tests.test_forecast_loans import _loan

pytestmark = pytest.mark.asyncio


async def _cash(session, user_id):
    today = app_today()
    account = await _account(session, user_id, opening=0)
    await _tx(session, user_id, account.id, 1000, "credit", today - timedelta(days=400), source="opening_balance")
    await _tx(session, user_id, account.id, 200, "debit", today - timedelta(days=10))
    return account


@pytest.mark.parametrize("months", [3, 12, 60])
@pytest.mark.parametrize("interval", ["weekly", "monthly"])
async def test_history_spans_as_far_back_as_the_forecast_looks_ahead(
    session, test_user, test_workspace, months, interval
):
    today = app_today()
    await _cash(session, test_user.id)

    result = await forecast_service.compute_forecast(
        session, test_workspace.id, test_user.id, months=months, interval=interval, include_spending=False,
    )

    actuals = result.actuals
    start = _add_months(today, -months)
    assert actuals[0].date == start
    assert actuals[-1].date == today == result.base[0].date
    assert actuals[-1].value == result.starting_balance == pytest.approx(800)
    dates = [p.date for p in actuals]
    assert dates == sorted(set(dates))
    inner = dates[1:-1]
    if interval == "weekly":
        assert all((b - a).days == 7 for a, b in zip(dates[:-1], dates[1:-1]))
        assert len(actuals) in (len(range(0, (today - start).days + 1, 7)), len(range(0, (today - start).days + 1, 7)) + 1)
    else:
        assert all(d.day == calendar.monthrange(d.year, d.month)[1] for d in inner)
        assert len(inner) == months
    # Before the opening balance there was nothing; before the debit, 1000.
    by_date = {p.date: p.value for p in actuals}
    for d, value in by_date.items():
        if d < today - timedelta(days=400):
            assert value == 0
        elif d < today - timedelta(days=10):
            assert value == pytest.approx(1000)
        else:
            assert value == pytest.approx(800)


async def test_history_leaves_out_loans_and_investments(session, test_user, test_workspace):
    today = app_today()
    await _cash(session, test_user.id)
    await _loan(session, test_user.id)  # 100,000 owed since 30 days ago
    investment = await _account(session, test_user.id, opening=0, type_="investment")
    await _tx(session, test_user.id, investment.id, 9999, "credit", today - timedelta(days=50))

    result = await forecast_service.compute_forecast(
        session, test_workspace.id, test_user.id, months=3, interval="weekly", include_spending=False,
    )

    assert {round(p.value, 2) for p in result.actuals} <= {1000.0, 800.0}
    assert result.actuals[-1].value == pytest.approx(800)


async def test_each_loan_has_its_balance_history(session, test_user, test_workspace):
    today = app_today()
    await _account(session, test_user.id)
    loan = await _loan(session, test_user.id)  # 100,000 drawn 30 days ago
    await _tx(session, test_user.id, loan.id, 5000, "credit", today - timedelta(days=5))

    result = await forecast_service.compute_forecast(
        session, test_workspace.id, test_user.id, months=3, interval="weekly", include_spending=False,
    )

    history = result.loans[0].actuals
    assert history[0].date == _add_months(today, -3)
    assert history[-1].date == today
    assert history[-1].value == pytest.approx(95_000) == result.loans[0].balance
    for p in history:
        if p.date < today - timedelta(days=30):
            assert p.value == 0
        elif p.date < today - timedelta(days=5):
            assert p.value == pytest.approx(100_000)
    # The projection starts where the history ends.
    assert result.loans[0].base is not None
    assert result.loans[0].base.points[0].balance == pytest.approx(95_000)
