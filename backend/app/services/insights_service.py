"""Fork: the Insights page, a monthly briefing of patterns in the payment data.

Everything here is deterministic: rules and robust statistics over the
workspace's own transactions, no language model. Two layers:

  * ``load_rows`` reads the rows that count as income or spending, with the
    same rule the reports use (``counts_as_pnl``), plus opening balances and
    pending rows left out;
  * ``analyse`` is pure Python over those rows, so every signal can be tested
    without a database.

Signals favour behaviour over single totals: a month against its usual
month, twelve-month totals that peak or climb, the share of income kept,
price steps on regular payments, new and stopped payees, income changes and
bursts of small charges. Two guards keep them honest. Categories whose spending
is mostly one-off purchases are left out (one big purchase is not a pattern),
and categories paid on a fixed cycle are left out of the month comparison when
the month simply held one more or one fewer payment.
"""
from __future__ import annotations

import calendar
import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.payee import Payee
from app.models.transaction import Transaction
from app.services._query_filters import counts_as_pnl

# A single charge at or above this is a one-off purchase, not everyday spending.
ONE_OFF_THRESHOLD = 1000.0
# A category whose last two years of spending is at least this share one-offs
# is "lumpy" and stays out of the month comparison and trend signals.
LUMPY_SHARE = 0.5
# Months of history read before the briefing month. The rolling twelve-month
# figures need twelve points, each covering twelve months.
HISTORY_MONTHS = 24
USUAL_MONTHS = 6
CARDS_PER_SIDE = 4


@dataclass(frozen=True)
class Row:
    when: date
    amount: float  # always positive, in the workspace's primary currency
    is_debit: bool
    category_id: Optional[str]
    category: Optional[str]
    payee_id: Optional[str]
    payee: Optional[str]
    account_id: Optional[str] = None


@dataclass
class Card:
    kind: str
    direction: str  # "better" | "worse"
    score: float
    params: dict[str, Any] = field(default_factory=dict)
    series: list[float] = field(default_factory=list)
    category_id: Optional[str] = None
    payee_id: Optional[str] = None
    link_from: Optional[str] = None
    link_to: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "direction": self.direction,
            "params": self.params,
            "series": [round(v, 2) for v in self.series],
            "category_id": self.category_id,
            "payee_id": self.payee_id,
            "link_from": self.link_from,
            "link_to": self.link_to,
        }


# ---------------------------------------------------------------------------
# month arithmetic
# ---------------------------------------------------------------------------


def _mi(d: date) -> int:
    return d.year * 12 + d.month - 1


def _month_start(i: int) -> date:
    return date(i // 12, i % 12 + 1, 1)


def _month_end(i: int) -> date:
    s = _month_start(i)
    return date(s.year, s.month, calendar.monthrange(s.year, s.month)[1])


def _ym(i: int) -> str:
    s = _month_start(i)
    return f"{s.year:04d}-{s.month:02d}"


def default_month(today: Optional[date] = None) -> date:
    """The last complete month."""
    today = today or app_today()
    return _month_start(_mi(today) - 1)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


async def load_rows(session: AsyncSession, workspace_id: uuid.UUID, start: date, end: date) -> list[Row]:
    amount = func.coalesce(Transaction.amount_primary, Transaction.amount)
    result = await session.execute(
        select(
            Transaction.date, amount, Transaction.type, Category.id, Category.name, Payee.id, Payee.name,
            Transaction.account_id,
        )
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .outerjoin(Payee, Transaction.payee_id == Payee.id)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.date >= start,
            Transaction.date <= end,
            Transaction.status == "posted",
            Transaction.source != "opening_balance",
            counts_as_pnl(),
        )
    )
    rows = []
    for when, amt, type_, cat_id, cat_name, payee_id, payee_name, account_id in result.all():
        rows.append(Row(
            when=when,
            amount=abs(float(amt or 0)),
            is_debit=type_ == "debit",
            category_id=str(cat_id) if cat_id else None,
            category=cat_name,
            payee_id=str(payee_id) if payee_id else None,
            payee=payee_name,
            account_id=str(account_id),
        ))
    return rows


async def get_briefing(
    session: AsyncSession, workspace_id: uuid.UUID, currency: str, month: Optional[date] = None,
) -> dict[str, Any]:
    month = month or default_month()
    m0 = _mi(month)
    rows = await load_rows(session, workspace_id, _month_start(m0 - HISTORY_MONTHS + 1), _month_end(m0))
    first = await session.scalar(
        select(func.min(Transaction.date)).where(
            Transaction.workspace_id == workspace_id, Transaction.source != "opening_balance",
        )
    )
    briefing = analyse(rows, month)
    briefing["currency"] = currency
    briefing["first_month"] = _ym(_mi(first)) if first else None
    briefing["last_month"] = _ym(_mi(default_month()))
    return briefing


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values)) if values else 0.0


@dataclass
class _Regular:
    payee_id: str
    payee: str
    category_id: Optional[str]
    category: Optional[str]
    cycle: float  # days between charges
    dates: list[date]
    amounts: list[float]


def _regulars(rows: list[Row], end: date, debit: bool) -> list[_Regular]:
    """Payees charged (or paying) on a steady cycle, weekly to monthly.

    Keyed by payee and account: one lender charging interest on several loan
    accounts is several cycles, not one irregular one.
    """
    by_payee: dict[tuple[str, Optional[str]], list[Row]] = defaultdict(list)
    for r in rows:
        if r.is_debit == debit and r.payee_id and r.when <= end:
            by_payee[(r.payee_id, r.account_id)].append(r)
    out = []
    for (payee_id, _account), rs in by_payee.items():
        rs.sort(key=lambda r: r.when)
        # One charge per day: a split payment is still one cycle.
        daily: dict[date, float] = defaultdict(float)
        for r in rs:
            daily[r.when] += r.amount
        dates = sorted(daily)
        if len(dates) < 4:
            continue
        gaps = [(b - a).days for a, b in zip(dates[-9:], dates[-8:])]
        gap = _median(gaps)
        if not 6 <= gap <= 40:
            continue
        spread = statistics.pstdev(gaps) / gap if len(gaps) > 1 else 0
        if spread >= 0.35:
            continue
        out.append(_Regular(
            payee_id=payee_id, payee=rs[-1].payee or "", category_id=rs[-1].category_id,
            category=rs[-1].category, cycle=gap, dates=dates, amounts=[daily[d] for d in dates],
        ))
    return out


def _driver_params(found: list[dict[str, Any]]) -> dict[str, Any]:
    if not found:
        return {}
    return {"driver": found[0]["payee"], "driver_diff": found[0]["diff"]}


def analyse(rows: list[Row], month: date) -> dict[str, Any]:
    m0 = _mi(month)
    first_m = m0 - HISTORY_MONTHS + 1
    month_start, month_end = _month_start(m0), _month_end(m0)
    window = (_month_start(m0 - 11).isoformat(), month_end.isoformat())

    spend: dict[Optional[str], dict[int, float]] = defaultdict(lambda: defaultdict(float))
    visits: dict[Optional[str], dict[int, int]] = defaultdict(lambda: defaultdict(int))
    one_off: dict[Optional[str], float] = defaultdict(float)
    all_debits: dict[Optional[str], float] = defaultdict(float)
    names: dict[Optional[str], str] = {}
    everyday_total: dict[int, float] = defaultdict(float)
    income: dict[int, float] = defaultdict(float)
    month_one_offs: list[Row] = []
    # Everyday spending per category, payee and month, to say who moved a category.
    by_payee: dict[Optional[str], dict[str, dict[int, float]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(float))
    )
    payee_names: dict[str, str] = {}
    for r in rows:
        mi = _mi(r.when)
        if mi < first_m or mi > m0:
            continue
        if not r.is_debit:
            income[mi] += r.amount
            continue
        names[r.category_id] = r.category or ""
        all_debits[r.category_id] += r.amount
        if r.amount >= ONE_OFF_THRESHOLD:
            one_off[r.category_id] += r.amount
            if mi == m0:
                month_one_offs.append(r)
            continue
        spend[r.category_id][mi] += r.amount
        visits[r.category_id][mi] += 1
        everyday_total[mi] += r.amount
        payee_key = r.payee_id or ""
        by_payee[r.category_id][payee_key][mi] += r.amount
        payee_names[payee_key] = r.payee or ""

    lumpy = {c for c, total in all_debits.items() if total > 0 and one_off[c] / total >= LUMPY_SHARE}
    usual_range = range(m0 - USUAL_MONTHS, m0)

    def baseline(c: Optional[str]) -> list[int]:
        """The months a category's usual month is measured over.

        Normally the six before the briefing month. When the category clearly
        changed level inside those six (every later month well above, or well
        below, every earlier one), only the months since the change count: a
        subscription that started in April makes April onwards the usual, not
        a median that still mixes in the months before it.
        """
        months = list(usual_range)
        values = [spend[c][m] for m in months]
        # At least three months on the new level, and a real move: the new
        # level is at least $100 a month and at least double (or half) the old.
        for k in range(1, len(months) - 2):
            before, after = values[:k], values[k:]
            up = min(after) > 1.5 * max(before) and _median(after) >= max(100, 2 * _median(before))
            down = max(after) * 1.5 < min(before) and _median(before) >= max(100, 2 * _median(after))
            if up or down:
                return months[k:]
        return months

    def usual(c: Optional[str]) -> float:
        return _median([spend[c][m] for m in baseline(c)])

    def drivers(c: Optional[str], diff: float) -> list[dict[str, Any]]:
        """The payees behind most of a category's difference, biggest first.

        Only for a difference big enough to matter, only named payees, and
        never one whose own move is far bigger than the category's (that is a
        payee offset by others, not the story of the month).
        """
        if abs(diff) < max(50.0, 0.2 * usual(c)):
            return []
        months = baseline(c)
        moves = []
        for p, by_m in by_payee[c].items():
            if not p:
                continue
            delta = by_m[m0] - _median([by_m[m] for m in months])
            if delta * diff > 0 and abs(delta) >= 20 and abs(delta) <= 1.5 * abs(diff):
                moves.append((p, delta))
        moves.sort(key=lambda x: -abs(x[1]))
        out = []
        covered = 0.0
        for p, delta in moves[:2]:
            out.append({"payee_id": p or None, "payee": payee_names.get(p) or "", "diff": round(delta, 2)})
            covered += delta
            if abs(covered) >= 0.5 * abs(diff):
                break
        return out if abs(covered) >= 0.5 * abs(diff) else []

    # --- fixed-cycle categories: one more or one fewer payment is not a change
    regulars = _regulars(rows, month_end, debit=True)
    cadence_note: dict[Optional[str], str] = {}
    by_cat_regular: dict[Optional[str], list[_Regular]] = defaultdict(list)
    for reg in regulars:
        recent = reg.amounts[-8:]
        mean = sum(recent) / len(recent)
        # Bills and instalments, not a shop visited every week: the amount
        # barely moves from one charge to the next.
        if mean > 0 and statistics.pstdev(recent) / mean < 0.3:
            by_cat_regular[reg.category_id].append(reg)

    def charges(regs: list[_Regular], m: int) -> tuple[int, float]:
        hits = [a for reg in regs for d, a in zip(reg.dates, reg.amounts) if _mi(d) == m]
        return len(hits), sum(hits)

    for c, regs in by_cat_regular.items():
        cat_usual_total = sum(spend[c][m] for m in usual_range)
        if cat_usual_total <= 0:
            continue
        reg_usual_total = sum(charges(regs, m)[1] for m in usual_range)
        if reg_usual_total / cat_usual_total < 0.7:
            continue
        now_count, now_total = charges(regs, m0)
        usual_count = _median([charges(regs, m)[0] for m in usual_range])
        if now_count == usual_count or usual_count == 0 or now_count == 0:
            continue
        per_usual = reg_usual_total / sum(charges(regs, m)[0] for m in usual_range)
        if per_usual and abs((now_total / now_count) / per_usual - 1) <= 0.1:
            cadence_note[c] = "more_payments" if now_count > usual_count else "fewer_payments"

    # --- the month against its usual month, by category
    compared: list[tuple[Optional[str], float, float]] = []  # (category, month, usual)
    for c in set(spend) | set(names):
        if c in lumpy or c in cadence_note:
            continue
        now, u = round(spend[c][m0], 2), round(usual(c), 2)
        if max(now, u) >= 50:
            compared.append((c, now, u))
    compared.sort(key=lambda x: x[1] - x[2])
    categories = []
    for c, now, u in compared:
        months = baseline(c)
        categories.append({
            "category_id": c, "name": names.get(c) or "", "amount": now, "usual": u, "diff": round(now - u, 2),
            "usual_since": _ym(months[0]) if len(months) < USUAL_MONTHS else None,
            "drivers": drivers(c, now - u),
        })
    excluded = (
        [{"category_id": c, "name": names.get(c) or "", "reason": cadence_note[c]} for c in cadence_note]
        + [{"category_id": c, "name": names.get(c) or "", "reason": "one_off"}
           for c in lumpy if spend[c][m0] or one_off[c]]
    )

    cards: list[Card] = []
    series_range = range(m0 - 11, m0 + 1)

    for c, now, u in compared:
        diff = round(now - u, 2)
        if abs(diff) < max(50.0, 0.2 * u):
            continue
        quiet = 0
        for m in range(m0 - 1, m0 - 12, -1):
            if diff > 0 and spend[c][m] < 0.6 * u or diff < 0 and spend[c][m] > 1.4 * u:
                quiet += 1
            else:
                break
        cards.append(Card(
            kind="category_month", direction="worse" if diff > 0 else "better", score=abs(diff),
            params={
                "category": names.get(c) or "", "amount": now, "usual": u, "diff": diff,
                "visits": visits[c][m0], "usual_visits": _median([visits[c][m] for m in usual_range]),
                "previous": round(spend[c][m0 - 1], 2), "streak": quiet,
                "usual_since": _ym(baseline(c)[0]) if len(baseline(c)) < USUAL_MONTHS else "",
                **_driver_params(drivers(c, diff)),
            },
            series=[spend[c][m] for m in series_range], category_id=c,
            link_from=month_start.isoformat(), link_to=month_end.isoformat(),
        ))

    # --- twelve-month totals: peaks passed and steady climbs
    for c in set(spend):
        if c in lumpy:
            continue
        ttm = [sum(spend[c][k] for k in range(m - 11, m + 1)) for m in series_range]
        then = sum(spend[c][k] for k in range(first_m, first_m + 12))
        now = ttm[-1]
        peak = max(ttm[:-3]) if len(ttm) > 3 else 0
        if peak >= 1000 and now <= 0.8 * peak:
            peak_m = m0 - 11 + ttm.index(peak)
            cards.append(Card(
                kind="trend_cooling", direction="better", score=(peak - now) / 12,
                params={"category": names.get(c) or "", "total": round(now, 2), "peak": round(peak, 2),
                        "peak_month": _ym(peak_m)},
                series=ttm, category_id=c, link_from=window[0], link_to=window[1],
            ))
        elif then >= 500 and now >= 1.4 * then and now - then >= 500:
            cards.append(Card(
                kind="trend_rising", direction="worse", score=(now - then) / 12,
                params={"category": names.get(c) or "", "total": round(now, 2), "before": round(then, 2),
                        "change_pct": round(100 * (now / then - 1))},
                series=ttm, category_id=c, link_from=window[0], link_to=window[1],
            ))

    # --- share of income kept after everyday spending, over twelve months
    rates: list[Optional[float]] = []
    for m in series_range:
        inc = sum(income[k] for k in range(m - 11, m + 1))
        out = sum(everyday_total[k] for k in range(m - 11, m + 1))
        rates.append(round(100 * (inc - out) / inc, 1) if inc > 0 else None)
    savings_rate = rates[-1]
    savings_rate_before = rates[0]
    if savings_rate is not None and savings_rate_before is not None:
        change = savings_rate - savings_rate_before
        if abs(change) >= 8:
            inc12 = sum(income[k] for k in range(m0 - 11, m0 + 1))
            cards.append(Card(
                kind="savings_rate", direction="better" if change > 0 else "worse",
                score=abs(change) / 100 * inc12 / 12 + 10_000,  # leads its column
                params={"rate": savings_rate, "before": savings_rate_before, "change": round(change, 1)},
                series=[r if r is not None else 0.0 for r in rates],
                link_from=window[0], link_to=window[1],
            ))

    # --- price steps on regular payments
    for reg in regulars:
        if reg.category_id in lumpy or len(reg.amounts) < 4:
            continue
        last = reg.amounts[-1]
        start = len(reg.amounts) - 1
        while start > 0 and abs(reg.amounts[start - 1] / last - 1) <= 0.05:
            start -= 1
        # The new amount has to repeat before it counts as a new price; a
        # single odd charge (a part period, a final bill) is not a step.
        if start == 0 or start > len(reg.amounts) - 2:
            continue
        stepped_on = reg.dates[start]
        if not (month_end - timedelta(days=92) < stepped_on <= month_end):
            continue
        before = _median(reg.amounts[max(0, start - 3):start])
        if before <= 0 or abs(last / before - 1) < 0.05 or abs(last - before) < 1:
            continue
        per_month = 30.4 / reg.cycle
        cards.append(Card(
            kind="price_up" if last > before else "price_down",
            direction="worse" if last > before else "better",
            score=abs(last - before) * per_month,
            params={"payee": reg.payee, "before": round(before, 2), "after": round(last, 2),
                    "since": stepped_on.isoformat(), "cycle_days": round(reg.cycle)},
            series=reg.amounts[-12:], payee_id=reg.payee_id,
            link_from=_month_start(m0 - 11).isoformat(), link_to=month_end.isoformat(),
        ))

    # --- regular payments that stopped recently
    for reg in regulars:
        silent = (month_end - reg.dates[-1]).days
        # A holiday pause is not a stop: at least 45 days of silence.
        if reg.category_id in lumpy or silent <= max(2.5 * reg.cycle, 45) or silent > 120:
            continue
        typical = _median(reg.amounts[-6:])
        cards.append(Card(
            kind="payee_stopped", direction="better", score=typical * 30.4 / reg.cycle,
            params={"payee": reg.payee, "category": reg.category or "", "amount": round(typical, 2), "last": reg.dates[-1].isoformat(),
                    "cycle_days": round(reg.cycle)},
            series=reg.amounts[-12:], payee_id=reg.payee_id,
        ))

    payee_month: dict[str, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    payee_small: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    payee_info: dict[str, Row] = {}
    for r in rows:
        if not r.is_debit or not r.payee_id or r.amount >= ONE_OFF_THRESHOLD:
            continue
        mi = _mi(r.when)
        payee_month[r.payee_id][mi] += r.amount
        if r.amount < 20:
            payee_small[r.payee_id][mi] += 1
        payee_info[r.payee_id] = r

    # --- a payee that has suddenly become a real cost
    for p, by_m in payee_month.items():
        info = payee_info[p]
        if info.category_id in lumpy:
            continue
        recent = by_m[m0] + by_m[m0 - 1]
        earlier = sum(by_m[m] for m in range(m0 - 13, m0 - 1))
        if by_m[m0] >= 150 and earlier < 0.1 * recent:
            cards.append(Card(
                kind="payee_new", direction="worse", score=by_m[m0],
                params={"payee": info.payee or "", "category": info.category or "", "amount": round(by_m[m0], 2)},
                series=[by_m[m] for m in series_range], payee_id=p, category_id=info.category_id,
                link_from=month_start.isoformat(), link_to=month_end.isoformat(),
            ))

    # --- bursts of small charges (in-app purchases and the like)
    for p, by_m in payee_small.items():
        count = by_m[m0]
        before = sum(by_m[m] for m in usual_range) / USUAL_MONTHS
        if count >= 10 and count >= 3 * max(before, 1):
            info = payee_info[p]
            cards.append(Card(
                kind="small_burst", direction="worse", score=payee_month[p][m0] + 100,
                params={"payee": info.payee or "", "count": count, "usual_count": round(before, 1),
                        "amount": round(payee_month[p][m0], 2)},
                series=[float(by_m[m]) for m in series_range], payee_id=p,
                link_from=month_start.isoformat(), link_to=month_end.isoformat(),
            ))

    # --- income from a regular payer changed
    for reg in _regulars(rows, month_end, debit=False):
        if len(reg.amounts) < 6 or (month_end - reg.dates[-1]).days > 2 * reg.cycle:
            continue
        now = _median(reg.amounts[-3:])
        before = _median(reg.amounts[-9:-3])
        if before <= 0 or abs(now / before - 1) < 0.05:
            continue
        cards.append(Card(
            kind="income_change", direction="better" if now > before else "worse",
            score=abs(now - before) * 30.4 / reg.cycle,
            params={"payee": reg.payee, "before": round(before, 2), "after": round(now, 2),
                    "change_pct": round(100 * (now / before - 1)), "cycle_days": round(reg.cycle)},
            series=reg.amounts[-12:], payee_id=reg.payee_id,
        ))

    # One card per subject: the strongest card about a category or a payee
    # wins, so a burst of charges is not also told as a new payee and as a
    # category over its usual month.
    kept: list[Card] = []
    used: set[tuple[str, str]] = set()
    for card in sorted(cards, key=lambda c: -c.score):
        keys: set[tuple[str, str]] = set()
        if card.category_id:
            keys.add(("c", card.category_id))
        if card.payee_id:
            keys.add(("p", card.payee_id))
        if keys & used:
            continue
        used |= keys
        kept.append(card)

    def side(direction: str) -> list[dict[str, Any]]:
        return [c.as_dict() for c in kept if c.direction == direction][:CARDS_PER_SIDE]

    usual_total = _median([everyday_total[m] for m in usual_range])
    return {
        "month": _ym(m0),
        "everyday_spend": round(everyday_total[m0], 2),
        "usual_spend": round(usual_total, 2),
        "income": round(income[m0], 2),
        "savings_rate": savings_rate,
        "savings_rate_before": savings_rate_before,
        "one_off_count": len(month_one_offs),
        "one_off_total": round(sum(r.amount for r in month_one_offs), 2),
        "one_off_threshold": ONE_OFF_THRESHOLD,
        "categories": categories,
        "excluded": excluded,
        "better": side("better"),
        "worse": side("worse"),
    }
