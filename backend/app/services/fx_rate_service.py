import bisect
import logging
import uuid
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.fx_rate import FxRate
from app.models.user import User
from app.providers.openexchangerates import OpenExchangeRatesProvider

logger = logging.getLogger(__name__)

_provider = OpenExchangeRatesProvider()


async def sync_rates(
    session: AsyncSession, target_date: Optional[date] = None
) -> int:
    """Fetch rates from the provider for the given date and upsert into fx_rates.

    Only saves rates for currencies in `supported_currencies`.
    Idempotent — existing rates for the same date are updated.
    Returns the number of rates synced.
    """
    requested_target = target_date or app_today()
    # Providers cannot return a historical rate for a date that has not
    # happened yet. Treat a future request as a request for today's latest
    # published rate and store it under today's date.
    target = min(requested_target, app_today())
    supported = set(get_settings().supported_currencies.split(","))

    if target == app_today():
        rates = await _provider.fetch_latest()
    else:
        rates = await _provider.fetch_historical(target)

    count = 0
    for currency_code, rate in rates.items():
        if currency_code not in supported:
            continue
        stmt = pg_insert(FxRate).values(
            base_currency="USD",
            quote_currency=currency_code,
            date=target,
            rate=rate,
            source=_provider.name,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_fx_rate_base_quote_date",
            set_={"rate": rate, "source": _provider.name},
        )
        await session.execute(stmt)
        count += 1

    await session.commit()
    logger.info("Synced %d FX rates for %s", count, target)
    return count


async def _resolve_rate(
    session: AsyncSession,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
) -> Optional[Decimal]:
    """Resolve the true FX rate, or None when no rate can be found.

    Uses cross-rate through USD: rate = usd_to_target / usd_to_source.
    Priority: exact date → on-demand fetch → closest available.
    Returns None (not a fake 1:1) when no rate is available, so callers that
    persist a conversion can honestly leave it NULL instead of storing a wrong
    amount (issue #353). Pass ``allow_fetch=False`` to skip the on-demand
    provider call and rely only on already-stored rates.
    """
    if from_currency == to_currency:
        return Decimal("1")

    requested_target = target_date or app_today()
    # A future transaction must use the latest real rate, never query a
    # not-yet-existing historical snapshot. Looking up against today also
    # lets the closest-rate fallback select the last cached business day.
    target = min(requested_target, app_today())

    # Step 1: Try exact date
    usd_to_source = await _get_exact_date_rate(session, from_currency, target)
    usd_to_target = await _get_exact_date_rate(session, to_currency, target)

    # Step 2: If missing, fetch from provider for exact date
    fetch_allowed = allow_fetch and (
        target == app_today() or get_settings().fx_fetch_history_on_demand
    )
    if fetch_allowed and (usd_to_source is None or usd_to_target is None):
        try:
            synced = await sync_rates(session, target)
            if synced > 0:
                logger.info("On-demand sync fetched %d rates for %s", synced, target)
                if usd_to_source is None:
                    usd_to_source = await _get_exact_date_rate(session, from_currency, target)
                if usd_to_target is None:
                    usd_to_target = await _get_exact_date_rate(session, to_currency, target)
        except Exception:
            logger.warning("On-demand FX rate sync failed for %s", target, exc_info=True)

    # Step 3: Fall back to closest available date
    if usd_to_source is None:
        usd_to_source = await _get_closest_rate(session, from_currency, target)
    if usd_to_target is None:
        usd_to_target = await _get_closest_rate(session, to_currency, target)

    if from_currency == "USD":
        usd_to_source = Decimal("1")
    if to_currency == "USD":
        usd_to_target = Decimal("1")

    if usd_to_source is None or usd_to_target is None or usd_to_source == 0:
        return None

    return usd_to_target / usd_to_source


async def get_rate(
    session: AsyncSession,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
) -> Decimal:
    """Get FX rate from from_currency to to_currency.

    Uses cross-rate through USD. When no rate can be resolved, returns a 1:1
    fallback so live reads (balances, dashboards) still render a number. This
    fallback is NOT meant to be persisted — persisting paths use
    :func:`_resolve_rate` directly and leave the value NULL instead (issue #353).
    """
    rate = await _resolve_rate(
        session, from_currency, to_currency, target_date, allow_fetch=allow_fetch
    )
    if rate is None:
        logger.warning(
            "No FX rate found for %s -> %s on %s, returning 1",
            from_currency, to_currency, target_date or app_today(),
        )
        return Decimal("1")
    return rate


async def _get_exact_date_rate(session: AsyncSession, currency: str, target: date) -> Optional[Decimal]:
    """Get the rate for an exact date."""
    if currency == "USD":
        return Decimal("1")
    result = await session.scalar(
        select(FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date == target,
        )
    )
    return result


async def _get_closest_rate(session: AsyncSession, currency: str, target: date) -> Optional[Decimal]:
    """Get the closest available rate to a target date (preferring before, then after)."""
    if currency == "USD":
        return Decimal("1")
    # Try closest before or on target date
    result = await session.scalar(
        select(FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date <= target,
        )
        .order_by(desc(FxRate.date))
        .limit(1)
    )
    if result is not None:
        return result
    # Try closest after target date
    from sqlalchemy import asc
    result = await session.scalar(
        select(FxRate.rate)
        .where(
            FxRate.base_currency == "USD",
            FxRate.quote_currency == currency,
            FxRate.date > target,
        )
        .order_by(asc(FxRate.date))
        .limit(1)
    )
    return result


async def convert(
    session: AsyncSession,
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    target_date: Optional[date] = None,
    *,
    allow_fetch: bool = True,
) -> tuple[Decimal, Decimal]:
    """Convert an amount from one currency to another.

    Returns (converted_amount, rate_used). Uses the 1:1 fallback from
    :func:`get_rate` when no rate is available, so this is for live reads, not
    for persisting a stored conversion.
    """
    if from_currency == to_currency:
        return amount, Decimal("1")

    rate = await get_rate(
        session, from_currency, to_currency, target_date, allow_fetch=allow_fetch
    )
    converted = amount * rate
    return converted.quantize(Decimal("0.01")), rate


class PreloadedRates:
    """``convert`` for many dates from one load of the stored rates.

    Fork: a multi-point report converts every item at every point; asking the
    database for each (currency, date) made the net worth report's queries
    grow with its points. This loads the stored USD rates of the currencies
    asked for once and resolves each rate in memory the way
    :func:`_resolve_rate` does (exact date, then the closest before, then the
    closest after; USD is 1; no rate means the 1:1 fallback of
    :func:`get_rate`), so every result equals ``convert``'s. Where
    ``_resolve_rate`` would call the provider (an exact rate missing for today,
    or for a past date with ``fx_fetch_history_on_demand``), it calls the real
    :func:`get_rate` and then re-reads that date's stored rates, since the
    provider call may have stored new ones.

    For the length of one request: rates stored by anyone else meanwhile are
    not seen.
    """

    def __init__(self, session: AsyncSession, currencies: Optional[set[str]] = None):
        self._session = session
        self._wanted: set[str] = {c for c in (currencies or set()) if c and c != "USD"}
        self._loaded: Optional[set[str]] = None
        self._dates: dict[str, list[date]] = {}
        self._rates: dict[str, list[Decimal]] = {}
        self._memo: dict[tuple[str, str, date], Decimal] = {}

    async def _ensure(self, *currencies: str) -> None:
        missing = {c for c in currencies if c and c != "USD"} - (self._loaded or set())
        if self._loaded is not None and not missing:
            return
        self._wanted |= missing
        rows = await self._session.execute(
            select(FxRate.quote_currency, FxRate.date, FxRate.rate)
            .where(FxRate.base_currency == "USD", FxRate.quote_currency.in_(self._wanted))
            .order_by(FxRate.quote_currency, FxRate.date)
        )
        self._dates = {c: [] for c in self._wanted}
        self._rates = {c: [] for c in self._wanted}
        for quote, day, rate in rows.all():
            self._dates[quote].append(day)
            self._rates[quote].append(rate)
        self._loaded = set(self._wanted)

    async def _reload_date(self, day: date) -> None:
        rows = await self._session.execute(
            select(FxRate.quote_currency, FxRate.rate).where(
                FxRate.base_currency == "USD",
                FxRate.quote_currency.in_(self._wanted),
                FxRate.date == day,
            )
        )
        for quote, rate in rows.all():
            dates, rates = self._dates[quote], self._rates[quote]
            i = bisect.bisect_left(dates, day)
            if i < len(dates) and dates[i] == day:
                rates[i] = rate
            else:
                dates.insert(i, day)
                rates.insert(i, rate)

    def _exact(self, currency: str, day: date) -> Optional[Decimal]:
        if currency == "USD":
            return Decimal("1")
        dates = self._dates[currency]
        i = bisect.bisect_left(dates, day)
        if i < len(dates) and dates[i] == day:
            return self._rates[currency][i]
        return None

    def _closest(self, currency: str, day: date) -> Optional[Decimal]:
        if currency == "USD":
            return Decimal("1")
        dates = self._dates[currency]
        i = bisect.bisect_right(dates, day)
        if i > 0:
            return self._rates[currency][i - 1]
        if i < len(dates):
            return self._rates[currency][i]
        return None

    async def get_rate(
        self, from_currency: str, to_currency: str, target_date: Optional[date] = None
    ) -> Decimal:
        """What :func:`get_rate` returns for these arguments."""
        if from_currency == to_currency:
            return Decimal("1")
        today = app_today()
        target = min(target_date or today, today)
        key = (from_currency, to_currency, target)
        if key in self._memo:
            return self._memo[key]
        await self._ensure(from_currency, to_currency)

        usd_to_source = self._exact(from_currency, target)
        usd_to_target = self._exact(to_currency, target)
        fetch_allowed = target == today or get_settings().fx_fetch_history_on_demand
        if fetch_allowed and (usd_to_source is None or usd_to_target is None):
            rate = await get_rate(self._session, from_currency, to_currency, target_date)
            # The provider call may have stored rates for that date, which
            # can change every answer that resolves through it.
            await self._reload_date(target)
            self._memo.clear()
            return rate

        if usd_to_source is None:
            usd_to_source = self._closest(from_currency, target)
        if usd_to_target is None:
            usd_to_target = self._closest(to_currency, target)
        if from_currency == "USD":
            usd_to_source = Decimal("1")
        if to_currency == "USD":
            usd_to_target = Decimal("1")

        if usd_to_source is None or usd_to_target is None or usd_to_source == 0:
            logger.warning(
                "No FX rate found for %s -> %s on %s, returning 1",
                from_currency, to_currency, target_date or today,
            )
            rate = Decimal("1")
        else:
            rate = usd_to_target / usd_to_source
        self._memo[key] = rate
        return rate

    async def convert(
        self,
        amount: Decimal,
        from_currency: str,
        to_currency: str,
        target_date: Optional[date] = None,
    ) -> tuple[Decimal, Decimal]:
        """What :func:`convert` returns for these arguments."""
        if from_currency == to_currency:
            return amount, Decimal("1")
        rate = await self.get_rate(from_currency, to_currency, target_date)
        converted = amount * rate
        return converted.quantize(Decimal("0.01")), rate


async def stamp_primary_amount(
    session: AsyncSession,
    user_id: uuid.UUID,
    obj,
    amount_field: str = "amount",
    primary_field: str = "amount_primary",
    rate_field: str = "fx_rate_used",
    date_field: str = "date",
    currency_field: str = "currency",
    *,
    allow_fetch: bool = True,
) -> None:
    """Set obj's primary amount and fx_rate_used based on user's primary currency.

    Works for Transaction, RecurringTransaction, etc.

    When the object is in a foreign currency and no real FX rate is available,
    the fields are left untouched instead of persisting a fake 1:1 conversion
    (issue #353). A brand-new object therefore stays NULL (honest "not converted",
    reads fall back to the native amount via ``COALESCE(amount_primary, amount)``),
    while an already-stamped row keeps its current value — so re-stamping never
    pushes a visible transaction into limbo. The row heals on a later pass once a
    rate for its date lands. Pass ``allow_fetch=False`` to avoid the on-demand
    provider call (used by the healer to stay frugal).
    """
    user = await session.get(User, user_id)
    if not user:
        return

    primary_currency = user.primary_currency
    obj_currency = getattr(obj, currency_field, get_settings().default_currency)
    obj_amount = getattr(obj, amount_field, None)

    if obj_amount is None:
        return

    amount_dec = Decimal(str(obj_amount))

    # Genuine same-currency 1:1 — always safe to persist.
    if obj_currency == primary_currency:
        setattr(obj, primary_field, amount_dec.quantize(Decimal("0.01")))
        if hasattr(obj, rate_field):
            setattr(obj, rate_field, Decimal("1"))
        return

    obj_date = getattr(obj, date_field, None)
    rate = await _resolve_rate(
        session, obj_currency, primary_currency, obj_date, allow_fetch=allow_fetch
    )

    if rate is None:
        # No real rate available yet. Leave the fields untouched: a brand-new
        # object stays NULL (honest "not converted"), an existing row keeps its
        # current value. Either way we never persist a fake 1:1, and never push a
        # visible transaction into limbo. It heals on a later pass once a rate lands.
        return

    setattr(obj, primary_field, (amount_dec * rate).quantize(Decimal("0.01")))
    if hasattr(obj, rate_field):
        setattr(obj, rate_field, rate)
