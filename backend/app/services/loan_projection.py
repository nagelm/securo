"""Fork: a table loan's repayments projected from today (the Forecast page).

Pure functions, no database. The service (``forecast_service``) reads each
loan's ``loan_details`` (Akahu's shape, see ``loan_details``) and its current
debt, and asks for a base schedule and one per scenario.

The model is the plain table-loan one: each repayment period charges
``rate / periods a year`` on the balance at the start of the period, the
repayment pays that interest first and the rest is principal. A loan is
projected from the debt owed today, never from ``initial_principal``.

Changes a scenario can make, each from a date:

* a new interest rate, with the repayment either recalculated over the
  remaining term (as at a refix) or kept (so the term changes);
* a new repayment amount;
* a one-off extra repayment (lump sum).
"""
import calendar
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Optional

from app.services.loan_details import is_revolving

PERIODS_PER_YEAR = {
    "WEEKLY": 52,
    "FORTNIGHTLY": 26,
    "MONTHLY": 12,
    "QUARTERLY": 4,
    "BIANNUALLY": 2,
    "ANNUALLY": 1,
}
_MONTHS_PER_PERIOD = {"MONTHLY": 1, "QUARTERLY": 3, "BIANNUALLY": 6, "ANNUALLY": 12}
_DAYS_PER_PERIOD = {"WEEKLY": 7, "FORTNIGHTLY": 14}
# A schedule that never pays off (repayment below the interest) stops here.
MAX_YEARS = 100
_CENT = 0.005


def _add_months(d: date, months: int, day: int) -> date:
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def period_date(first: date, frequency: str, k: int) -> date:
    """The ``k``-th repayment date (0 = ``first``); month-based ones keep ``first``'s day."""
    if frequency in _DAYS_PER_PERIOD:
        return first + timedelta(days=_DAYS_PER_PERIOD[frequency] * k)
    return _add_months(first, _MONTHS_PER_PERIOD[frequency] * k, first.day)


def payment_for(principal: float, annual_rate: float, periods_per_year: int, periods: int) -> float:
    """The level repayment that clears ``principal`` in ``periods`` repayments."""
    if periods <= 0:
        return principal
    r = annual_rate / 100 / periods_per_year
    if r == 0:
        return principal / periods
    return principal * r / (1 - (1 + r) ** -periods)


def periods_to_repay(
    principal: float, annual_rate: float, periods_per_year: int, payment: float
) -> Optional[int]:
    """How many repayments of ``payment`` clear ``principal``; None if never."""
    if principal <= 0:
        return 0
    if payment <= 0:
        return None
    r = annual_rate / 100 / periods_per_year
    if r == 0:
        return math.ceil(principal / payment - 1e-9)
    if payment <= principal * r:
        return None
    return math.ceil(-math.log(1 - principal * r / payment) / math.log(1 + r) - 1e-9)


@dataclass(frozen=True)
class LoanTerms:
    debt: float  # owed today, positive
    rate: float  # percent a year
    frequency: str  # a PERIODS_PER_YEAR key
    repayment: float
    first_date: date  # the next repayment after today
    matures_at: Optional[date] = None


@dataclass(frozen=True)
class LoanChange:
    """One scenario change; exactly one of rate, repayment, lump_sum is set."""

    start: date
    rate: Optional[float] = None
    recalculate: bool = True
    repayment: Optional[float] = None
    lump_sum: Optional[float] = None


@dataclass(frozen=True)
class LoanPeriod:
    date: date
    opening: float
    rate: float
    interest: float
    payment: float  # the regular repayment in effect
    repayment: float  # what was paid of it (the last one may be smaller)
    extra: float  # lump sums applied with this repayment
    principal: float  # repayment - interest + extra
    closing: float


@dataclass
class LoanSchedule:
    periods: list[LoanPeriod] = field(default_factory=list)
    # (date, amount) of each lump sum actually paid, on its own date.
    lump_sums: list[tuple[date, float]] = field(default_factory=list)
    payoff_date: Optional[date] = None
    total_interest: float = 0.0

    def cash_out(self) -> dict[date, float]:
        """Money leaving cash for this loan, per day."""
        out: dict[date, float] = {}
        for p in self.periods:
            if p.repayment:
                out[p.date] = out.get(p.date, 0.0) + p.repayment
        for d, amount in self.lump_sums:
            out[d] = out.get(d, 0.0) + amount
        return out


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _positive(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number > 0 and math.isfinite(number) else None


def loan_terms(details: Any, debt: float, today: date) -> Optional[LoanTerms]:
    """The terms a projection needs, or None when ``loan_details`` lacks them.

    Needs a TABLE loan with an interest rate and a repayment amount and
    frequency. A missing or past ``repayment.next_date`` is rolled forward
    by whole periods to the first repayment after today.
    """
    if not isinstance(details, dict) or is_revolving(details):
        return None
    if str(details.get("type") or "").upper() != "TABLE":
        return None
    interest = details.get("interest") if isinstance(details.get("interest"), dict) else {}
    repayment = details.get("repayment") if isinstance(details.get("repayment"), dict) else {}
    rate_raw = interest.get("rate")
    if rate_raw is None or isinstance(rate_raw, bool):
        return None
    try:
        rate = float(rate_raw)
    except (TypeError, ValueError):
        return None
    if not 0 <= rate <= 100:
        return None
    amount = _positive(repayment.get("next_amount"))
    frequency = str(repayment.get("frequency") or "").upper()
    if amount is None or frequency not in PERIODS_PER_YEAR:
        return None
    first = _parse_date(repayment.get("next_date")) or period_date(today, frequency, 1)
    k = 0
    while period_date(first, frequency, k) <= today:
        k += 1
    return LoanTerms(
        debt=debt,
        rate=rate,
        frequency=frequency,
        repayment=amount,
        first_date=period_date(first, frequency, k),
        matures_at=_parse_date(details.get("matures_at")),
    )


def project_loan(
    terms: LoanTerms, changes: Iterable[LoanChange] = (), *, max_years: int = MAX_YEARS
) -> LoanSchedule:
    """Every repayment from ``terms.first_date`` until the loan is paid off.

    Rate and repayment changes apply from the first repayment dated on or
    after their start. A lump sum leaves cash on its own date and reduces
    the balance with the first repayment on or after it, so interest saved
    is never overstated. Recalculating at a new rate keeps the remaining
    term: to ``matures_at`` when known, otherwise the number of repayments
    the current schedule still needs.
    """
    ppy = PERIODS_PER_YEAR[terms.frequency]
    pending = sorted(changes, key=lambda c: c.start)
    schedule = LoanSchedule()
    balance, rate, payment = terms.debt, terms.rate, terms.repayment
    cutoff = _add_months(terms.first_date, 12 * max_years, terms.first_date.day)
    lumps_due: list[LoanChange] = []
    k = 0
    while balance > _CENT:
        d = period_date(terms.first_date, terms.frequency, k)
        if d > cutoff:
            break
        while pending and pending[0].start <= d:
            change = pending.pop(0)
            if change.lump_sum is not None:
                lumps_due.append(change)
                continue
            if change.rate is not None:
                if change.recalculate:
                    remaining = _remaining_periods(terms, k, balance, rate, payment)
                    if remaining:
                        payment = round(payment_for(balance, change.rate, ppy, remaining), 2)
                rate = change.rate
            if change.repayment is not None:
                payment = change.repayment
        interest = round(balance * rate / 100 / ppy, 2)
        due = balance + interest
        paid = round(min(payment, due), 2)
        closing = round(due - paid, 2)
        extra = 0.0
        for lump in lumps_due:
            amount = round(min(lump.lump_sum or 0.0, closing - extra), 2)
            if amount > 0:
                extra += amount
                schedule.lump_sums.append((lump.start, amount))
        lumps_due = []
        closing = round(closing - extra, 2)
        schedule.periods.append(LoanPeriod(
            date=d, opening=round(balance, 2), rate=rate, interest=interest, payment=payment, repayment=paid,
            extra=round(extra, 2), principal=round(paid - interest + extra, 2), closing=max(closing, 0.0),
        ))
        schedule.total_interest += interest
        balance = closing
        k += 1
    if balance <= _CENT and schedule.periods:
        schedule.payoff_date = schedule.periods[-1].date
    schedule.total_interest = round(schedule.total_interest, 2)
    return schedule


def _remaining_periods(terms: LoanTerms, k: int, balance: float, rate: float, payment: float) -> Optional[int]:
    if terms.matures_at is not None and terms.matures_at >= period_date(terms.first_date, terms.frequency, k):
        n = 0
        while period_date(terms.first_date, terms.frequency, k + n) <= terms.matures_at:
            n += 1
        return n
    return periods_to_repay(balance, rate, PERIODS_PER_YEAR[terms.frequency], payment)


def cash_difference(base: LoanSchedule, scenario: LoanSchedule) -> dict[date, float]:
    """Cash a scenario keeps (+) or spends (−) against the base, per day.

    Only the difference: the base's repayments are already in (or out of)
    the cash line however the user records them, so adding them again
    would count them twice.
    """
    before, after = base.cash_out(), scenario.cash_out()
    out: dict[date, float] = {}
    for d in before.keys() | after.keys():
        diff = round(before.get(d, 0.0) - after.get(d, 0.0), 2)
        if diff:
            out[d] = diff
    return out
