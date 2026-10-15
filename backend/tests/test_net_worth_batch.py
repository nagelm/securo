"""The net worth report gives the same answer as the one-snapshot-per-point
code it replaced, with a number of queries that does not grow with the
number of points.

``_reference_net_worth_at`` and ``_reference_net_worth_report`` are verbatim
copies of ``report_service._net_worth_at`` and the trend loop of
``get_net_worth_report`` as they stood before the batched version (one
balance query per account, one value query per asset and one FX lookup per
item, for every point). Every figure here is made up.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

import pytest
from sqlalchemy import desc, event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.account import Account
from app.models.asset import Asset
from app.models.asset_group import AssetGroup
from app.models.asset_transaction import AssetTransaction
from app.models.asset_value import AssetValue
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.report import (
    ReportBreakdown,
    ReportCompositionItem,
    ReportDataPoint,
    ReportMeta,
    ReportResponse,
    ReportSummary,
)
from app.services.account_service import get_account_name
from app.services.asset_service import counted_in_net_worth
from app.services.dashboard_service import _account_balance_at, _get_open_accounts
from app.services.fx_rate_service import convert
from app.services.report_service import (
    _ACCOUNT_TYPE_COLORS,
    _ASSET_TYPE_COLORS,
    _date_points,
    _format_date_label,
    _net_worth_at,
    _report_start_date,
    get_net_worth_report,
)
from tests.conftest import engine

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Reference: the per-point implementation the batched one replaced.
# ---------------------------------------------------------------------------


async def _reference_net_worth_at(
    session: AsyncSession, workspace_id: uuid.UUID, cutoff: date,
    primary_currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
    asset_group_ids: Optional[list[uuid.UUID]] = None,
    accounts: Optional[list[Account]] = None,
) -> ReportDataPoint:
    if accounts is None:
        accounts = await _get_open_accounts(session, workspace_id, account_ids)

    accounts_total = 0.0
    assets_total = 0.0
    liabilities_total = 0.0
    composition: list[ReportCompositionItem] = []

    for account in accounts:
        bal = await _account_balance_at(session, account, cutoff)
        converted, _ = await convert(
            session, Decimal(str(abs(bal))), account.currency, primary_currency, cutoff
        )
        converted_val = float(converted)
        is_debt_account = account.type in ("credit_card", "loan")
        if is_debt_account or bal < 0:
            liabilities_total += converted_val
            if converted_val > 0 or is_debt_account:
                composition.append(ReportCompositionItem(
                    key=str(account.id),
                    label=get_account_name(account),
                    value=round(converted_val, 2),
                    color=_ACCOUNT_TYPE_COLORS.get(account.type, "#6B7280"),
                    group="liabilities",
                ))
        else:
            is_investment = account.type == "investment"
            if is_investment:
                assets_total += converted_val
            else:
                accounts_total += converted_val
            if converted_val > 0:
                composition.append(ReportCompositionItem(
                    key=str(account.id),
                    label=get_account_name(account),
                    value=round(converted_val, 2),
                    color=_ACCOUNT_TYPE_COLORS.get(account.type, "#6B7280"),
                    group="assets" if is_investment else "accounts",
                ))

    filtered = account_ids is not None
    asset_stmt = select(Asset).where(
        Asset.workspace_id == workspace_id,
        Asset.is_archived == False,  # noqa: E712
        Asset.sell_date.is_(None),
    )
    if filtered:
        asset_stmt = asset_stmt.where(Asset.group_id.in_(asset_group_ids or []))
    asset_stmt = asset_stmt.where(counted_in_net_worth())
    asset_result = await session.execute(asset_stmt)
    for asset in asset_result.scalars().all():
        val_result = await session.execute(
            select(AssetValue.amount)
            .where(AssetValue.asset_id == asset.id, AssetValue.date <= cutoff)
            .order_by(desc(AssetValue.date), desc(AssetValue.id))
            .limit(1)
        )
        val = val_result.scalar_one_or_none()
        if val is not None:
            amount = float(val)
        elif asset.purchase_price is not None and (
            asset.purchase_date is None or asset.purchase_date <= cutoff
        ):
            amount = float(asset.purchase_price)
        else:
            amount = 0.0
        if amount > 0:
            converted, _ = await convert(
                session, Decimal(str(amount)), asset.currency, primary_currency, cutoff
            )
            converted_val = round(float(converted), 2)
            assets_total += converted_val
            composition.append(ReportCompositionItem(
                key=str(asset.id),
                label=asset.name,
                value=converted_val,
                color=_ASSET_TYPE_COLORS.get(asset.type, "#6B7280"),
                group="assets",
            ))

    net_worth = accounts_total + assets_total - liabilities_total

    return ReportDataPoint(
        date=cutoff.isoformat(),
        value=round(net_worth, 2),
        breakdowns={
            "accounts": round(accounts_total, 2),
            "assets": round(assets_total, 2),
            "liabilities": round(liabilities_total, 2),
        },
        composition=composition,
    )


async def _reference_net_worth_report(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 12,
    interval: str = "monthly",
    currency: str = "USD",
    account_ids: Optional[list[uuid.UUID]] = None,
    asset_group_ids: Optional[list[uuid.UUID]] = None,
    period: str | None = None,
    financial_year_start_month: int = 1,
    start_date: date | None = None,
    end_date: date | None = None,
) -> ReportResponse:
    if asset_group_ids is not None and account_ids is None:
        account_ids = []
    real_today = app_today()
    axis_end = end_date or real_today
    start = _report_start_date(
        axis_end, months, period, financial_year_start_month=financial_year_start_month,
        start_date=start_date,
    )
    user = await session.get(User, user_id)
    primary_currency = user.primary_currency if user else get_settings().default_currency
    points = _date_points(start, axis_end, interval)
    accounts = await _get_open_accounts(session, workspace_id, account_ids)

    trend: list[ReportDataPoint] = []
    for point in points:
        dp = await _reference_net_worth_at(
            session, workspace_id, point, primary_currency, account_ids, asset_group_ids,
            accounts=accounts,
        )
        dp.date = _format_date_label(point, interval)
        dp.change = round(dp.value - trend[-1].value, 2) if trend else None
        trend.append(dp)

    current = trend[-1] if trend else ReportDataPoint(
        date="", value=0, breakdowns={"accounts": 0, "assets": 0, "liabilities": 0}
    )
    if trend and points[0] == start:
        baseline = trend[0]
    else:
        baseline = await _reference_net_worth_at(
            session, workspace_id, start, primary_currency, account_ids, asset_group_ids,
            accounts=accounts,
        )
    previous = baseline if trend else current

    change_amount = current.value - previous.value
    change_percent = (
        (change_amount / abs(previous.value) * 100)
        if previous.value != 0
        else None
    )

    summary = ReportSummary(
        primary_value=current.value,
        change_amount=round(change_amount, 2),
        change_percent=round(change_percent, 2) if change_percent is not None else None,
        breakdowns=[
            ReportBreakdown(key="accounts", label="Accounts",
                            value=current.breakdowns.get("accounts", 0), color="#6366F1"),
            ReportBreakdown(key="assets", label="Assets",
                            value=current.breakdowns.get("assets", 0), color="#F59E0B"),
            ReportBreakdown(key="liabilities", label="Liabilities",
                            value=current.breakdowns.get("liabilities", 0), color="#F43F5E"),
        ],
    )
    meta = ReportMeta(
        type="net_worth",
        series_keys=["accounts", "assets", "liabilities"],
        currency=primary_currency,
        interval=interval,
    )
    composition = trend[-1].composition if trend else []
    return ReportResponse(summary=summary, trend=trend, meta=meta, composition=composition)


# ---------------------------------------------------------------------------
# Fixture: a varied made-up workspace.
# ---------------------------------------------------------------------------


def _months_ago(today: date, months: int, day: int = 10) -> date:
    y, m = today.year, today.month - months
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(day, 28))


async def _build_workspace(session: AsyncSession, user: User, workspace_id: uuid.UUID) -> dict:
    today = app_today()
    uid = user.id

    def _account(name, type_, currency, *, connection=None, balance="0", closed=False,
                 external_id=None):
        acct = Account(
            id=uuid.uuid4(), user_id=uid, workspace_id=workspace_id, name=name, type=type_,
            currency=currency, balance=Decimal(balance), is_closed=closed,
            connection_id=connection.id if connection else None, external_id=external_id,
        )
        session.add(acct)
        return acct

    rows: list[Transaction] = []

    def _tx(acct, amount, kind, when, *, currency=None, amount_primary=None, status="posted",
            ignored=False, category=None, source="manual"):
        rows.append(Transaction(
            id=uuid.uuid4(), user_id=uid, workspace_id=workspace_id, account_id=acct.id,
            description=f"Row {len(rows)}", amount=Decimal(str(amount)),
            currency=currency or acct.currency, date=when, effective_date=when, type=kind,
            source=source, status=status, is_ignored=ignored,
            amount_primary=Decimal(str(amount_primary)) if amount_primary is not None else None,
            category_id=category.id if category else None,
            created_at=datetime.now(timezone.utc),
        ))

    conn = BankConnection(
        id=uuid.uuid4(), user_id=uid, workspace_id=workspace_id, provider="test",
        external_id="nw-batch-conn", institution_name="Test Bank", credentials={},
        status="active", last_sync_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    session.add(conn)
    ignored_cat = Category(id=uuid.uuid4(), user_id=uid, workspace_id=workspace_id,
                           name="Ignored stuff", is_ignored=True)
    normal_cat = Category(id=uuid.uuid4(), user_id=uid, workspace_id=workspace_id,
                          name="Groceries")
    session.add_all([ignored_cat, normal_cat])
    await session.flush()

    checking = _account("Everyday", "checking", "BRL")
    savings_usd = _account("Dollar savings", "savings", "USD")
    manual_card = _account("Manual card", "credit_card", "BRL")
    overdraft = _account("Overdraft", "checking", "EUR")
    closed = _account("Old account", "checking", "BRL", closed=True,
                      connection=conn, external_id="ext-closed")
    bank_checking = _account("Bank cheque", "checking", "EUR", connection=conn,
                             balance="4321.09", external_id="ext-chq")
    bank_card = _account("Bank card", "credit_card", "BRL", connection=conn,
                         balance="1500.25", external_id="ext-card")
    loan = _account("Home loan", "loan", "BRL", connection=conn, balance="-250000.00",
                    external_id="ext-loan")
    repaid_loan = _account("Repaid loan", "loan", "BRL", connection=conn, balance="0",
                           external_id="ext-loan2")
    investment = _account("Fund account", "investment", "BRL", connection=conn,
                          balance="20000", external_id="ext-inv")
    plain_investment = _account("Broker cash", "investment", "USD", connection=conn,
                                balance="900", external_id="ext-broker")
    await session.flush()

    # Ten-plus years of activity, starting with an opening balance.
    start = _months_ago(today, 130)
    _tx(checking, "5000.00", "credit", start, source="opening_balance")
    _tx(savings_usd, "1000.00", "credit", start, source="opening_balance")
    for i in range(130, -1, -1):
        d = _months_ago(today, i)
        _tx(checking, "3210.55", "credit", d)
        _tx(checking, f"{1000 + i * 7}.13", "debit", d + timedelta(days=3), category=normal_cat)
        _tx(manual_card, f"{200 + i}.40", "debit", d + timedelta(days=1))
        _tx(manual_card, f"{150 + i}.40", "credit", d + timedelta(days=12))
        if i % 3 == 0:
            _tx(savings_usd, "250.75", "credit", d)
        if i < 60:
            _tx(bank_checking, f"{800 + i}.10", "credit", d)
            _tx(bank_checking, f"{700 + i}.45", "debit", d + timedelta(days=2))
            _tx(bank_card, f"{300 + i}.99", "debit", d + timedelta(days=4))
            _tx(bank_card, f"{280 + i}.11", "credit", d + timedelta(days=9))
            _tx(loan, "1234.56", "credit", d + timedelta(days=5))
            _tx(loan, "456.78", "debit", d + timedelta(days=5))
            _tx(plain_investment, "50.00", "credit", d)
        if i < 30:
            _tx(overdraft, f"{40 + i}.20", "debit", d)
        _tx(closed, "99.99", "credit", d)
    # A foreign row in an account: its amount_primary counts.
    _tx(checking, "100.00", "debit", _months_ago(today, 40), currency="USD",
        amount_primary="512.34")
    _tx(checking, "77.00", "credit", _months_ago(today, 20), currency="USD")  # no amount_primary
    # Rows the balance never counts.
    _tx(checking, "99999.00", "credit", _months_ago(today, 50), ignored=True)
    _tx(checking, "88888.00", "credit", _months_ago(today, 30), category=ignored_cat)
    _tx(bank_checking, "7777.00", "debit", _months_ago(today, 10), ignored=True)
    _tx(bank_checking, "6666.00", "debit", _months_ago(today, 8), category=ignored_cat)
    # Pending rows, recent and older, and rows dated after today.
    _tx(checking, "321.00", "debit", today - timedelta(days=2), status="pending")
    _tx(bank_checking, "55.55", "debit", today - timedelta(days=1), status="pending")
    _tx(bank_checking, "44.44", "debit", today - timedelta(days=40), status="pending")
    _tx(bank_card, "66.66", "debit", today, status="pending")
    _tx(checking, "1000.00", "credit", today + timedelta(days=5))
    _tx(bank_checking, "2000.00", "credit", today + timedelta(days=3))
    # Rows on today itself.
    _tx(checking, "12.34", "debit", today)
    _tx(bank_checking, "43.21", "credit", today)
    session.add_all(rows)

    # FX: rates only from about eight years back (older dates use the closest
    # rate after), monthly, with today's rate for EUR but not USD's quote BRL.
    fx_rows = []
    for i in range(96, -1, -1):
        d = _months_ago(today, i, day=15)
        if d == today:
            continue
        fx_rows.append(FxRate(base_currency="USD", quote_currency="BRL", date=d,
                              rate=Decimal(f"{4 + (i % 17) / 10:.4f}"), source="test"))
        fx_rows.append(FxRate(base_currency="USD", quote_currency="EUR", date=d,
                              rate=Decimal(f"0.{80 + i % 15}12"), source="test"))
    fx_rows.append(FxRate(base_currency="USD", quote_currency="EUR", date=today,
                          rate=Decimal("0.9123"), source="test"))
    session.add_all(fx_rows)

    # Assets.
    wallet = AssetGroup(id=uuid.uuid4(), user_id=uid, workspace_id=workspace_id, name="Wallet")
    other_wallet = AssetGroup(id=uuid.uuid4(), user_id=uid, workspace_id=workspace_id,
                              name="Other wallet")
    session.add_all([wallet, other_wallet])
    await session.flush()

    def _asset(name, type_, currency, **kw):
        a = Asset(id=uuid.uuid4(), user_id=uid, workspace_id=workspace_id, name=name,
                  type=type_, currency=currency, **kw)
        session.add(a)
        return a

    house = _asset("House", "real_estate", "BRL", purchase_price=Decimal("600000"),
                   purchase_date=_months_ago(today, 80), group_id=wallet.id)
    car = _asset("Car", "vehicle", "EUR", purchase_price=Decimal("30000"),
                 purchase_date=_months_ago(today, 36), group_id=other_wallet.id)
    _asset("Painting", "valuable", "USD", purchase_price=Decimal("1500"))
    grower = _asset("Bond", "investment", "BRL", valuation_method="growth_rule",
                    purchase_price=Decimal("10000"), purchase_date=_months_ago(today, 48),
                    growth_type="percentage", growth_rate=Decimal("5"),
                    growth_frequency="yearly", growth_start_date=_months_ago(today, 48),
                    group_id=wallet.id)
    _asset("Sold boat", "vehicle", "BRL", purchase_price=Decimal("9000"),
           purchase_date=_months_ago(today, 100), sell_date=_months_ago(today, 20),
           sell_price=Decimal("7000"))
    _asset("Archived ring", "valuable", "BRL", purchase_price=Decimal("800"), is_archived=True)
    zeroed = _asset("Written off", "other", "BRL", purchase_price=Decimal("400"),
                    purchase_date=_months_ago(today, 70))
    future = _asset("Next year", "other", "BRL", purchase_price=Decimal("123"),
                    purchase_date=today + timedelta(days=200))
    # Holdings an open account carries (left out), and one of a closed account (counted).
    holding = _asset("Fund units", "investment", "USD", valuation_method="market_price",
                     connection_id=conn.id, account_external_id="ext-inv", source="sync",
                     purchase_price=Decimal("5000"), purchase_date=_months_ago(today, 30))
    holding2 = _asset("Fund units B", "investment", "BRL", connection_id=conn.id,
                      account_external_id="ext-inv", source="sync")
    orphan = _asset("Old fund units", "investment", "BRL", connection_id=conn.id,
                    account_external_id="ext-closed", source="sync",
                    purchase_price=Decimal("2500"), purchase_date=_months_ago(today, 90))
    await session.flush()

    values = []
    for i in range(80, -1, -2):
        d = _months_ago(today, i, day=20)
        values.append(AssetValue(asset_id=house.id, workspace_id=workspace_id,
                                 amount=Decimal(600000 + (80 - i) * 2500), date=d))
    # Two values on one day: the later-sorting id wins.
    dup_day = _months_ago(today, 12, day=5)
    values.append(AssetValue(id=uuid.UUID(int=1), asset_id=house.id,
                             workspace_id=workspace_id, amount=Decimal("700001"), date=dup_day))
    values.append(AssetValue(id=uuid.UUID(int=2), asset_id=house.id,
                             workspace_id=workspace_id, amount=Decimal("700002"), date=dup_day))
    for i in range(30, -1, -6):
        values.append(AssetValue(asset_id=car.id, workspace_id=workspace_id,
                                 amount=Decimal(28000 - (30 - i) * 300),
                                 date=_months_ago(today, i, day=2)))
    for i in range(48, -1, -12):
        values.append(AssetValue(asset_id=grower.id, workspace_id=workspace_id,
                                 amount=Decimal(10000 + (48 - i) * 41), source="rule",
                                 date=_months_ago(today, i, day=1)))
    values.append(AssetValue(asset_id=zeroed.id, workspace_id=workspace_id,
                             amount=Decimal("0"), date=_months_ago(today, 24)))
    values.append(AssetValue(asset_id=future.id, workspace_id=workspace_id,
                             amount=Decimal("123"), date=today + timedelta(days=200)))
    for i in range(30, -1, -1):
        d = _months_ago(today, i, day=25)
        values.append(AssetValue(asset_id=holding.id, workspace_id=workspace_id,
                                 amount=Decimal("0"), price=Decimal(f"{10 + i % 7}.5"), date=d))
        values.append(AssetValue(asset_id=holding2.id, workspace_id=workspace_id,
                                 amount=Decimal(f"{3000 + i * 11}"), date=d))
    for i in range(90, 20, -10):
        values.append(AssetValue(asset_id=orphan.id, workspace_id=workspace_id,
                                 amount=Decimal(2500 + i), date=_months_ago(today, i)))
    session.add_all(values)
    session.add_all([
        AssetTransaction(asset_id=holding.id, workspace_id=workspace_id, kind="buy",
                         quantity=Decimal("400"), price=Decimal("12.5"),
                         date=_months_ago(today, 30, day=3)),
        AssetTransaction(asset_id=holding.id, workspace_id=workspace_id, kind="buy",
                         quantity=Decimal("100"), price=Decimal("13"),
                         date=_months_ago(today, 14, day=3)),
        AssetTransaction(asset_id=holding.id, workspace_id=workspace_id, kind="sell",
                         quantity=Decimal("50"), price=Decimal("14"),
                         date=_months_ago(today, 6, day=3)),
    ])
    await session.commit()
    return {
        "collection_accounts": [checking.id, bank_card.id, investment.id, loan.id],
        "collection_wallets": [wallet.id],
        "all_accounts": [checking, savings_usd, manual_card, overdraft, bank_checking,
                         bank_card, loan, repaid_loan, investment, plain_investment],
    }


_RANGES = [
    pytest.param(dict(months=12, interval="monthly"), id="12-monthly"),
    pytest.param(dict(days=168, interval="weekly"), id="24-weekly"),
    pytest.param(dict(months=120, interval="monthly"), id="120-monthly"),
    pytest.param(dict(months=24, interval="yearly"), id="yearly"),
    pytest.param(dict(days=10, interval="daily"), id="daily"),
    pytest.param(dict(start_offset=400, end_offset=-60, interval="weekly"), id="past-range"),
    pytest.param(dict(start_offset=200, end_offset=21, interval="weekly"), id="into-future"),
]


def _range_kwargs(spec: dict) -> dict:
    today = app_today()
    kw: dict = {"interval": spec["interval"]}
    if "months" in spec:
        kw["months"] = spec["months"]
    if "days" in spec:
        kw["start_date"] = today - timedelta(days=spec["days"] - 1)
    if "start_offset" in spec:
        kw["start_date"] = today - timedelta(days=spec["start_offset"])
        kw["end_date"] = today - timedelta(days=spec["end_offset"])
    return kw


@pytest.fixture(params=[True, False], ids=["fx-fetch-history", "fx-stored-only"])
def fx_history_mode(request, monkeypatch):
    monkeypatch.setattr(get_settings(), "fx_fetch_history_on_demand", request.param)
    return request.param


@pytest.mark.parametrize("spec", _RANGES)
@pytest.mark.parametrize("scope", ["all", "collection", "wallet-only"])
async def test_report_matches_the_per_point_reference(
    session: AsyncSession, test_user, test_workspace, fx_history_mode, spec, scope,
):
    data = await _build_workspace(session, test_user, test_workspace.id)
    kw = _range_kwargs(spec)
    if scope == "collection":
        kw["account_ids"] = data["collection_accounts"]
        kw["asset_group_ids"] = data["collection_wallets"]
    elif scope == "wallet-only":
        kw["asset_group_ids"] = data["collection_wallets"]

    expected = await _reference_net_worth_report(
        session, test_workspace.id, test_user.id, **kw)
    actual = await get_net_worth_report(session, test_workspace.id, test_user.id, **kw)

    assert len(actual.trend) > 1
    assert actual.model_dump() == expected.model_dump()


async def test_single_snapshot_matches_the_reference(
    session: AsyncSession, test_user, test_workspace, fx_history_mode,
):
    data = await _build_workspace(session, test_user, test_workspace.id)
    today = app_today()
    for cutoff in [today, today - timedelta(days=1), _months_ago(today, 70),
                   _months_ago(today, 125), today + timedelta(days=10)]:
        expected = await _reference_net_worth_at(session, test_workspace.id, cutoff, "BRL")
        actual = await _net_worth_at(session, test_workspace.id, cutoff, "BRL")
        assert actual.model_dump() == expected.model_dump()
        expected = await _reference_net_worth_at(
            session, test_workspace.id, cutoff, "BRL",
            data["collection_accounts"], data["collection_wallets"])
        actual = await _net_worth_at(
            session, test_workspace.id, cutoff, "BRL",
            data["collection_accounts"], data["collection_wallets"])
        assert actual.model_dump() == expected.model_dump()


class _QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, *args, **kwargs):
        self.count += 1


async def _count_queries(coro_factory) -> int:
    counter = _QueryCounter()
    event.listen(engine.sync_engine, "before_cursor_execute", counter)
    try:
        await coro_factory()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", counter)
    return counter.count


async def test_query_count_does_not_grow_with_points(
    session: AsyncSession, test_user, test_workspace, monkeypatch,
):
    monkeypatch.setattr(get_settings(), "fx_fetch_history_on_demand", False)
    await _build_workspace(session, test_user, test_workspace.id)
    ws, uid = test_workspace.id, test_user.id
    today = app_today()

    def report(**kw):
        return lambda: get_net_worth_report(session, ws, uid, **kw)

    q12 = await _count_queries(report(months=12, interval="monthly"))
    q120 = await _count_queries(report(months=120, interval="monthly"))
    q520 = await _count_queries(report(
        start_date=today - timedelta(weeks=519), interval="weekly"))
    reference12 = await _count_queries(
        lambda: _reference_net_worth_report(session, ws, uid, months=12, interval="monthly"))

    assert q120 <= 2 * q12, (q12, q120)
    assert q520 <= 2 * q12, (q12, q520)
    assert q12 < reference12 / 5, (q12, reference12)
