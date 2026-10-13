"""Periodic contributions toward a savings goal.

A goal may carry a periodic target ("save 50 every week"). When the goal
tracks an account, what was actually contributed in each period is read
from that account's ledger:

    contributed = transfers in from the user's other accounts
                  - transfers out to the user's other accounts

A row on the linked account counts when it is a transfer: it has a pair
(`transfer_pair_id`, the other leg sits on another of the user's accounts)
or, unpaired, it sits in a `treat_as_transfer` category (money from an
account the user keeps outside Securo). Credits add, debits subtract.
Everything else on the account (interest, tax on interest, fees, a card
purchase straight from the savings account) is not a contribution and is
left out; it still moves the balance, which is the goal's `current_amount`.
Ignored rows and rows in ignored categories are left out, as everywhere.
A row the bank itself types as interest (Akahu `INTEREST`) never counts,
even when a rule has filed it under a transfer category.

Periods follow the calendar: weeks run Monday to Sunday (ISO; Securo has
no week-start setting), fortnights are pairs of such weeks counted from a
fixed Monday, and months are calendar months. "Today" is the workspace's
own date (`app_today`).

Ahead or behind the plan is measured over a window that opens when money
last left the account (`plan_window`):

    since   = date of the latest money out of the account: any debit,
              withdrawal or transfer out alike. Charges the bank posts
              itself (Akahu types INTEREST, TAX, FEE: interest, tax on it,
              fees) do not open a window. An account money never left
              opens it on its first deposit.
    saved   = deposits dated from `since` through today, the `since` day
              included (rows carry no time of day, so a deposit later on
              the day of the withdrawal counts; the withdrawal itself is a
              debit and never does). A deposit is a contribution credit as
              above: a transfer in, never interest.
    planned = whole periods only: the plan steps up once per period.
              * each finished period that starts on or after `since`:
                the full target;
              * the finished period holding `since`, when it started
                before it: what went in from `since`, up to the target
                (a payment made earlier in that period, before the
                withdrawal, never makes it a missed period);
              * the current period, not over yet: what went in so far,
                up to the target.
    ahead   = saved - planned (negative: behind).

So paying on time reads on plan; a period not paid yet reads on plan until
it ends, then behind by its target; paying more than the target reads
ahead by the excess.

All of this is display-only: nothing here writes, and no balance, net
worth, P&L or budget reads it.
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Literal, Optional, cast

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.category import Category
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services.fx_rate_service import convert

#: How many periods the history and the streak look back over, the current
#: one included.
HISTORY_PERIODS = 12

#: How many of those the API returns: the last ten finished periods and the
#: current one.
SHOWN_PERIODS = 11

#: Akahu transaction types (`raw_data.type`) the bank posts itself. Interest
#: is never a deposit; none of them is money taken out of the account.
_INTEREST_TYPES = ("INTEREST",)
_BANK_CHARGE_TYPES = ("INTEREST", "TAX", "FEE")

#: Fortnights are counted from this Monday, so every goal and every caller
#: agrees on where a fortnight starts.
_FORTNIGHT_EPOCH = date(2024, 1, 1)

_CENT = Decimal("0.01")

Period = Literal["weekly", "biweekly", "monthly"]
#: met / over: the target was reached; short: a finished period that fell
#: short; pending: the current period, not reached yet.
PeriodStatus = Literal["met", "over", "short", "pending"]


def period_bounds(period: str, day: date) -> tuple[date, date]:
    """The period (`weekly`, `biweekly`, `monthly`) holding `day`, inclusive."""
    if period == "monthly":
        start = day.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    monday = day - timedelta(days=day.weekday())
    if period == "weekly":
        return monday, monday + timedelta(days=6)
    if period == "biweekly":
        weeks = (monday - _FORTNIGHT_EPOCH).days // 7
        start = monday - timedelta(weeks=weeks % 2)
        return start, start + timedelta(days=13)
    raise ValueError(f"unknown contribution period: {period}")


def recent_periods(period: str, today: date, count: int = HISTORY_PERIODS) -> list[tuple[date, date]]:
    """The last `count` periods, oldest first; the last one holds `today`."""
    bounds = [period_bounds(period, today)]
    while len(bounds) < count:
        bounds.append(period_bounds(period, bounds[-1][0] - timedelta(days=1)))
    return list(reversed(bounds))


@dataclass
class PeriodResult:
    start: date
    end: date
    contributed: Decimal
    target: Decimal
    status: PeriodStatus
    current: bool


@dataclass
class PlanWindow:
    """Saved vs planned since money last left the account."""

    since: date
    saved: Decimal
    planned: Decimal
    ahead_by: Decimal  # saved - planned; negative: behind


@dataclass
class ContributionProgress:
    period: Period
    target: Decimal
    current: PeriodResult
    history: list[PeriodResult]  # oldest first, the current period last
    streak: int
    plan: Optional[PlanWindow] = None


def _status(contributed: Decimal, target: Decimal, current: bool) -> PeriodStatus:
    if abs(contributed - target) < _CENT:
        return "met"
    if contributed > target:
        return "over"
    return "pending" if current else "short"


def _streak(history: list[PeriodResult]) -> int:
    """Consecutive periods that met the target, counting back from the
    latest finished one. The current period adds one once it is met and
    never breaks the streak while it is still under way."""
    streak = 0
    finished = [p for p in history if not p.current]
    for result in reversed(finished):
        if result.status in ("met", "over"):
            streak += 1
        else:
            break
    current = next((p for p in history if p.current), None)
    if current is not None and current.status in ("met", "over"):
        streak += 1
    return streak


def _raw_type():
    """The provider's own transaction type (Akahu `raw_data.type`), upper
    case, or "" for a row without one."""
    return func.upper(func.coalesce(Transaction.raw_data["type"].as_string(), ""))


async def _contribution_rows(
    session: AsyncSession,
    account_ids: list[uuid.UUID],
    start: date,
    end: date,
) -> list[tuple[date, str, Decimal, str]]:
    """(date, type, amount, currency) of every contribution-shaped row on
    the accounts between `start` and `end`, inclusive."""
    rows = (
        await session.execute(
            select(
                Transaction.id,
                Transaction.date,
                Transaction.type,
                Transaction.amount,
                Transaction.currency,
                Transaction.transfer_pair_id,
                Category.treat_as_transfer,
                Category.is_ignored,
                _raw_type().label("raw_type"),
            )
            .outerjoin(Category, Category.id == Transaction.category_id)
            .where(
                Transaction.account_id.in_(account_ids),
                Transaction.date >= start,
                Transaction.date <= end,
                Transaction.is_ignored.is_not(True),
            )
        )
    ).all()

    pair_ids = {r.transfer_pair_id for r in rows if r.transfer_pair_id is not None}
    # A pair whose other leg is on one of the same linked accounts only moves
    # money around inside the goal; it is not a contribution.
    internal_pairs: set[uuid.UUID] = set()
    paired_elsewhere: set[uuid.UUID] = set()
    if pair_ids:
        own_ids = {r.id for r in rows}
        legs = (
            await session.execute(
                select(Transaction.id, Transaction.transfer_pair_id, Transaction.account_id).where(
                    Transaction.transfer_pair_id.in_(pair_ids),
                )
            )
        ).all()
        for leg_id, pair_id, account_id in legs:
            if leg_id in own_ids:
                continue
            if account_id in account_ids:
                internal_pairs.add(pair_id)
            else:
                paired_elsewhere.add(pair_id)

    out: list[tuple[date, str, Decimal, str]] = []
    for r in rows:
        if r.is_ignored or r.raw_type in _INTEREST_TYPES:
            continue
        pair_id = r.transfer_pair_id
        if pair_id is not None and pair_id in internal_pairs:
            continue
        # A pair whose other leg is gone is read like an unpaired row.
        if (pair_id is not None and pair_id in paired_elsewhere) or r.treat_as_transfer:
            out.append((r.date, r.type, Decimal(r.amount), r.currency))
    return out


async def _in_currency(session: AsyncSession, amount: Decimal, from_cur: str, to_cur: str) -> Decimal:
    if from_cur == to_cur:
        return amount
    converted, _ = await convert(session, amount, from_cur, to_cur)
    return converted


def planned_between(
    period: str,
    target: Decimal,
    since: date,
    today: date,
    deposits: Iterable[tuple[date, Decimal]],
) -> Decimal:
    """What the plan asks for from `since` through `today`, in whole
    periods (see the module docstring). `deposits` are (date, amount) in the
    goal's currency; only those from `since` through `today` are read."""
    paid: dict[date, Decimal] = {}
    for day, amount in deposits:
        if since <= day <= today:
            start = period_bounds(period, day)[0]
            paid[start] = paid.get(start, Decimal("0")) + amount
    total = Decimal("0")
    day = since
    while day <= today:
        period_start, period_end = period_bounds(period, day)
        finished = period_end < today
        if finished and period_start >= since:
            total += target
        else:
            total += min(target, paid.get(period_start, Decimal("0")))
        day = period_end + timedelta(days=1)
    return total


async def _window_start(session: AsyncSession, account_ids: list[uuid.UUID], today: date) -> Optional[date]:
    """The date money last left the accounts, else the first deposit into them."""
    other = aliased(Transaction)
    same_pair = and_(
        other.transfer_pair_id == Transaction.transfer_pair_id,
        other.id != Transaction.id,
    )
    # The other leg on another linked account: money moved inside the goal.
    internal = exists().where(same_pair, other.account_id.in_(account_ids))
    paired_elsewhere = exists().where(same_pair, other.account_id.not_in(account_ids))
    counted = (
        Transaction.account_id.in_(account_ids),
        Transaction.date <= today,
        Transaction.is_ignored.is_not(True),
        Category.is_ignored.is_not(True),
        ~internal,
    )

    last_out = (
        await session.execute(
            select(func.max(Transaction.date))
            .outerjoin(Category, Category.id == Transaction.category_id)
            .where(
                *counted,
                Transaction.type == "debit",
                _raw_type().not_in(_BANK_CHARGE_TYPES),
            )
        )
    ).scalar_one_or_none()
    if last_out is not None:
        return last_out

    return (
        await session.execute(
            select(func.min(Transaction.date))
            .outerjoin(Category, Category.id == Transaction.category_id)
            .where(
                *counted,
                Transaction.type == "credit",
                or_(paired_elsewhere, Category.treat_as_transfer.is_(True)),
                _raw_type().not_in(_INTEREST_TYPES),
            )
        )
    ).scalar_one_or_none()


async def plan_window(
    session: AsyncSession,
    *,
    account_ids: list[uuid.UUID],
    period: Period,
    target: Decimal,
    currency: str,
    today: date,
) -> Optional[PlanWindow]:
    """Saved vs planned since money last left the accounts (see the module
    docstring), or None while nothing has gone in or out."""
    since = await _window_start(session, account_ids, today)
    if since is None:
        return None
    deposits: list[tuple[date, Decimal]] = []
    for row_date, row_type, amount, row_currency in await _contribution_rows(session, account_ids, since, today):
        if row_type == "credit":
            deposits.append((row_date, await _in_currency(session, abs(amount), row_currency, currency)))
    saved = sum((amount for _, amount in deposits), Decimal("0")).quantize(_CENT)
    planned = planned_between(period, target, since, today, deposits).quantize(_CENT)
    return PlanWindow(since=since, saved=saved, planned=planned, ahead_by=saved - planned)


async def compute_contribution_progress(
    session: AsyncSession,
    *,
    account_ids: list[uuid.UUID],
    period: Period,
    target: Decimal,
    currency: str,
    today: date,
    count: int = HISTORY_PERIODS,
) -> ContributionProgress:
    """Contributed vs target for the last `count` periods up to `today`."""
    bounds = recent_periods(period, today, count)
    rows = await _contribution_rows(session, account_ids, bounds[0][0], bounds[-1][1])

    totals = [Decimal("0")] * len(bounds)
    for row_date, row_type, amount, row_currency in rows:
        signed = abs(amount) if row_type == "credit" else -abs(amount)
        value = await _in_currency(session, signed, row_currency, currency)
        for i, (start, end) in enumerate(bounds):
            if start <= row_date <= end:
                totals[i] += value
                break

    history = [
        PeriodResult(
            start=start,
            end=end,
            contributed=totals[i].quantize(_CENT),
            target=target,
            status=_status(totals[i], target, current=(i == len(bounds) - 1)),
            current=(i == len(bounds) - 1),
        )
        for i, (start, end) in enumerate(bounds)
    ]
    return ContributionProgress(
        period=period,
        target=target,
        current=history[-1],
        history=history,
        streak=_streak(history),
        plan=await plan_window(
            session, account_ids=account_ids, period=period, target=target, currency=currency, today=today,
        ),
    )


# -- Suggesting a target ------------------------------------------------------

_SUGGESTED_PERIODS = ("weekly", "biweekly", "monthly")

#: How far back the history-based guess looks.
_LOOKBACK_DAYS = 120


@dataclass
class ContributionSuggestion:
    amount: Decimal
    period: Period
    currency: str
    source: Literal["recurring", "history"]
    recurring_transaction_id: Optional[uuid.UUID] = None
    description: Optional[str] = None


def _period_from_gap(days: float) -> Optional[Period]:
    if 5 <= days <= 9:
        return "weekly"
    if 12 <= days <= 16:
        return "biweekly"
    if 26 <= days <= 35:
        return "monthly"
    return None


async def suggest_contribution(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    today: date,
) -> Optional[ContributionSuggestion]:
    """A periodic target for a goal on `account_id`, read from what already
    pays into it.

    1. An active recurring transaction that pays into the account: a credit
       scheduled on the account itself, or the schedule matched to the other
       leg of a recent transfer into it (a weekly automatic payment from the
       everyday account). Only weekly, fortnightly and monthly ones count.
    2. Otherwise the transfers into the account over the last 120 days: the
       most frequent amount, seen at least three times, at a regular gap.
    """
    since = today - timedelta(days=_LOOKBACK_DAYS)
    credits = (
        await session.execute(
            select(Transaction.date, Transaction.amount, Transaction.currency, Transaction.transfer_pair_id)
            .where(
                Transaction.account_id == account_id,
                Transaction.type == "credit",
                Transaction.transfer_pair_id.is_not(None),
                Transaction.is_ignored.is_not(True),
                Transaction.date >= since,
                Transaction.date <= today,
            )
            .order_by(Transaction.date)
        )
    ).all()

    pair_ids = [c.transfer_pair_id for c in credits]
    recurring_filters = [
        and_(RecurringTransaction.account_id == account_id, RecurringTransaction.type == "credit"),
        RecurringTransaction.transfer_account_id == account_id,
    ]
    if pair_ids:
        linked_recurring = (
            select(Transaction.recurring_transaction_id)
            .where(
                Transaction.transfer_pair_id.in_(pair_ids),
                Transaction.account_id != account_id,
                Transaction.recurring_transaction_id.is_not(None),
            )
        )
        recurring_filters.append(RecurringTransaction.id.in_(linked_recurring))
    recurring = (
        await session.execute(
            select(RecurringTransaction)
            .where(
                RecurringTransaction.workspace_id == workspace_id,
                RecurringTransaction.is_active.is_(True),
                RecurringTransaction.frequency.in_(_SUGGESTED_PERIODS),
                or_(*recurring_filters),
            )
            .order_by(RecurringTransaction.next_occurrence, RecurringTransaction.created_at)
        )
    ).scalars().first()
    if recurring is not None:
        return ContributionSuggestion(
            amount=abs(Decimal(recurring.amount)).quantize(_CENT),
            period=cast(Period, recurring.frequency),
            currency=recurring.currency,
            source="recurring",
            recurring_transaction_id=recurring.id,
            description=recurring.description,
        )

    if len(credits) < 3:
        return None
    amounts = Counter(abs(Decimal(c.amount)).quantize(_CENT) for c in credits)
    amount, seen = amounts.most_common(1)[0]
    if seen < 3:
        return None
    dates = [c.date for c in credits if abs(Decimal(c.amount)).quantize(_CENT) == amount]
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    period = _period_from_gap(median(gaps)) if gaps else None
    if period is None:
        return None
    currency = next(c.currency for c in credits if abs(Decimal(c.amount)).quantize(_CENT) == amount)
    return ContributionSuggestion(amount=amount, period=period, currency=currency, source="history")
