"""Fork: the Forecast page.

The base line is the cash-flow report's forward projection (cash and cards:
recurring payments, pending and future rows, invoices owed, expected
payments), optionally plus an everyday spending estimate per category. Each
scenario is the base line plus the changes it holds, so the projection
engine stays in one place (`report_service.get_cash_flow_report`) and a
scenario only ever computes its own difference.

A scenario can also change a table loan's rate or repayments
(``loan_change``). Each open loan with enough ``loan_details`` gets a base
repayment schedule and one per scenario that changes it (``loan_projection``);
the cash line takes only the difference in repayments from the base. The
base repayments are in the cash line once: from a recurring schedule paying
into the loan when there is one, otherwise from the loan's base schedule
(``loan_repayment_projections``, which the cash-flow report adds).

Expected payments are one-off items with no scenario. When the real bank row
arrives (same account and direction, amount within 5%, date within 10 days)
the item is matched to it and drops out: from the sync and import paths
after new rows land, and again whenever a forecast is computed, so a missed
hook only delays the match.

Fork: an expected payment can also repeat (``repeating``, with a frequency
and a start and end date). Each occurrence projects and is matched like a
one-off, on its own (``ForecastItemMatch``); a matched one stops projecting.

Fork: a budget (kind ``budget``, no scenario) is the plan for everyday
spending: ``amount`` per weekly, fortnightly or monthly period, from
``start_date`` to ``end_date``. It projects as debits on that schedule and
switches the history-based spending estimate off over its dates, for every
category or, with ``category_id``, for that one. Budgets are not matched.

Fork: an expected payment with ``transfer_account_id`` is a transfer between
own accounts, projected as a debit on ``account_id`` and a credit on the
to-account on the same date. Only the legs on accounts in scope count, so in
the all-accounts line the two cancel (and are left out); it is matched by
the debit leg of a bank transfer pair between the same two accounts.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Iterable, Optional, cast

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.forecast import ForecastItem, ForecastItemMatch, ForecastScenario
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.forecast import (
    ForecastItemCreate,
    ForecastItemUpdate,
    ForecastLineSummary,
    ForecastPoint,
    ForecastRecurringPayment,
    ForecastResponse,
    ForecastScenarioCreate,
    ForecastScenarioSeries,
    ForecastScenarioUpdate,
    LoanForecast,
    LoanLine,
    LoanPoint,
    LoanScenarioLine,
    SpendingEstimate,
)
from app.services.loan_projection import (
    LoanChange,
    LoanSchedule,
    LoanTerms,
    cash_difference,
    loan_terms,
    project_loan,
)
from app.services.text_similarity import token_overlap

MATCH_AMOUNT_TOLERANCE = Decimal("0.05")
MATCH_WINDOW_DAYS = 10
# Fork: how often a budget's amount is spent.
BUDGET_FREQUENCIES = ("weekly", "biweekly", "monthly")
# Rows that are not money arriving from the bank or the user's own books.
_NOT_REAL_SOURCES = ("opening_balance", "recurring")

# ---------------------------------------------------------------------------
# expected payments: matching and projection
# ---------------------------------------------------------------------------


def _utc(value: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands back naive UTC timestamps; compare everything aware."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _is_match(
    item: ForecastItem,
    tx: Transaction,
    today: date,
    when: Optional[date] = None,
    window: int = MATCH_WINDOW_DAYS,
    check_stored: bool = True,
) -> bool:
    """Whether ``tx`` is the bank row of ``item``'s payment on ``when``
    (default: the one-off's date), within ``window`` days of it."""
    if tx.account_id != item.account_id or tx.type != item.type:
        return False
    # Fork: a transfer is settled only by a leg of a bank transfer pair; the
    # other leg's account is checked by the caller (`_paired_into`).
    if item.transfer_account_id is not None and tx.transfer_pair_id is None:
        return False
    # An expectation is for a payment still to come: a row stored before the
    # item was created belongs to something else (an earlier occurrence, an
    # unrelated payment of a similar amount). A row with no created_at is old.
    stored, expected_since = _utc(tx.created_at), _utc(item.created_at)
    if check_stored and (stored is None or (expected_since is not None and stored < expected_since)):
        return False
    # A row a recurring schedule already claims is that schedule's payment.
    if tx.recurring_transaction_id is not None:
        return False
    if (tx.currency or "") != item.currency:
        return False
    if tx.status != "posted" or tx.date > today or tx.source in _NOT_REAL_SOURCES:
        return False
    if abs((tx.date - cast(date, when or item.date)).days) > window:
        return False
    expected = abs(Decimal(item.amount or 0))
    actual = abs(Decimal(tx.amount or 0))
    if actual == expected:
        return True
    # Near enough in amount is only a match when the wording shares a word
    # too: a power bill is not the transfer of a similar amount expected the
    # same week on the same account.
    return (
        abs(actual - expected) <= expected * MATCH_AMOUNT_TOLERANCE
        and token_overlap(item.description, tx.description) > 0
    )


async def _paired_into(session: AsyncSession, tx: Transaction, account_id: uuid.UUID) -> bool:
    """Fork: whether ``tx``'s transfer partner is on ``account_id``."""
    found = (
        await session.execute(
            select(Transaction.id)
            .where(
                Transaction.transfer_pair_id == tx.transfer_pair_id,
                Transaction.id != tx.id,
                Transaction.account_id == account_id,
            )
            .limit(1)
        )
    ).first()
    return found is not None


def series_dates(item: ForecastItem, first: date, last: date) -> list[date]:
    """Fork: a repeating item's payment dates in ``[first, last]``.

    The recurring schedules' own helper, asked a year at a time because it
    stops after 200 dates."""
    from app.services.recurring_transaction_service import get_occurrences_in_range

    out: list[date] = []
    lo = first
    while lo <= last:
        hi = min(lo + timedelta(days=366), last + timedelta(days=1))
        out += get_occurrences_in_range(
            start=cast(date, item.start_date),
            frequency=cast(str, item.frequency),
            end_date=item.end_date,
            range_start=lo,
            range_end=hi,
        )
        lo = hi
    return out


def _series_window(frequency: Optional[str]) -> int:
    """Fork: how far a bank row may be from a repeating payment's date. Less
    than half the gap between payments, so a row can't fit its neighbour."""
    return {"weekly": 3, "biweekly": 6}.get(frequency or "", MATCH_WINDOW_DAYS)


async def _matched_occurrences(session: AsyncSession, workspace_id: uuid.UUID) -> set[tuple[uuid.UUID, date]]:
    """Fork: (item, occurrence date) of every saved series match."""
    rows = await session.execute(
        select(ForecastItemMatch.item_id, ForecastItemMatch.occurrence_date)
        .join(ForecastItem, ForecastItem.id == ForecastItemMatch.item_id)
        .where(ForecastItem.workspace_id == workspace_id)
    )
    return {(item_id, occurrence) for item_id, occurrence in rows.all()}


async def match_expected_payments(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: Optional[Iterable[Transaction]] = None,
) -> int:
    """Mark expected payments whose bank row has arrived. Returns how many.

    With ``transactions`` (the rows a sync or import just stored) only those
    are candidates; without, the workspace's rows around each payment are.
    One bank row settles at most one expected payment. Only rows stored
    after the payment was created, and not linked to a recurring payment,
    are candidates; the amount must be exact, or within 5% with a word in
    common; the most similar description wins, then the closest date, then
    the closest amount. Flushes, never commits. Called by sync
    and import only; reads use ``pending_expected_matches``.

    Fork: each occurrence of a repeating expected payment is matched on its
    own and saved as a ``ForecastItemMatch``; one dated before the series
    was created may match a row stored before then (its money was already
    in the bank when the series was entered).
    """
    found = await _find_expected_matches(session, workspace_id, transactions)
    for item, occurrence, tx_id in found:
        if occurrence is None:
            item.matched_transaction_id = tx_id
        else:
            session.add(ForecastItemMatch(item_id=item.id, occurrence_date=occurrence, transaction_id=tx_id))
    if found:
        await session.flush()
    return len(found)


async def pending_expected_matches(
    session: AsyncSession, workspace_id: uuid.UUID
) -> set[tuple[uuid.UUID, Optional[date]]]:
    """(item id, occurrence date) of expected payments whose bank row has
    arrived but isn't saved as a match yet; the date is None for a one-off.
    Read-only: a forecast or report leaves them out without writing, so
    viewing never changes data (a viewer can't write anyway)."""
    return {
        (item.id, occurrence)
        for item, occurrence, _ in await _find_expected_matches(session, workspace_id, None)
    }


async def _find_expected_matches(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: Optional[Iterable[Transaction]],
) -> list[tuple[ForecastItem, Optional[date], uuid.UUID]]:
    candidates = list(transactions) if transactions is not None else None
    if candidates is not None and not candidates:
        return []
    today = app_today()
    items = list(
        (
            await session.execute(
                select(ForecastItem)
                .where(
                    ForecastItem.workspace_id == workspace_id,
                    ForecastItem.scenario_id.is_(None),
                    ForecastItem.account_id.is_not(None),
                    or_(
                        and_(
                            ForecastItem.kind == "one_off",
                            ForecastItem.matched_transaction_id.is_(None),
                            ForecastItem.date.is_not(None),
                        ),
                        # Fork: series with a payment due by now (or soon).
                        and_(
                            ForecastItem.kind == "repeating",
                            ForecastItem.start_date.is_not(None),
                            ForecastItem.frequency.is_not(None),
                            ForecastItem.start_date <= today + timedelta(days=MATCH_WINDOW_DAYS),
                        ),
                    ),
                )
                .order_by(ForecastItem.date, ForecastItem.start_date, ForecastItem.created_at)
            )
        ).scalars()
    )
    if not items:
        return []
    taken = set(
        (
            await session.execute(
                select(ForecastItem.matched_transaction_id).where(
                    ForecastItem.workspace_id == workspace_id,
                    ForecastItem.matched_transaction_id.is_not(None),
                )
            )
        ).scalars()
    ) | set(
        (
            await session.execute(
                select(ForecastItemMatch.transaction_id)
                .join(ForecastItem, ForecastItem.id == ForecastItemMatch.item_id)
                .where(ForecastItem.workspace_id == workspace_id)
            )
        ).scalars()
    )
    matched = await _matched_occurrences(session, workspace_id)
    found: list[tuple[ForecastItem, Optional[date], uuid.UUID]] = []
    for item in items:
        # The payments to settle: a one-off's date, or each unmatched date of
        # a series up to the last one a bank row could already be for.
        if item.kind == "one_off":
            window = MATCH_WINDOW_DAYS
            dates: list[Optional[date]] = [None]
            first = last = cast(date, item.date)
        else:
            window = _series_window(item.frequency)
            due = [
                d for d in series_dates(
                    item, cast(date, item.start_date), min(item.end_date or date.max, today + timedelta(days=window))
                )
                if (item.id, d) not in matched
            ]
            if not due:
                continue
            dates = list(due)
            first, last = due[0], due[-1]
        if candidates is not None:
            pool = candidates
        else:
            pool = list(
                (
                    await session.execute(
                        select(Transaction).where(
                            Transaction.workspace_id == workspace_id,
                            Transaction.account_id == item.account_id,
                            Transaction.type == item.type,
                            Transaction.date >= first - timedelta(days=window),
                            Transaction.date <= last + timedelta(days=window),
                        )
                    )
                ).scalars()
            )
        created = _utc(item.created_at)
        for occurrence in dates:
            when = occurrence or cast(date, item.date)
            # A series date already past when the series was entered may be
            # a row stored before then.
            check_stored = occurrence is None or created is None or occurrence >= created.date()
            fits = [
                tx for tx in pool
                if tx.id not in taken and _is_match(item, tx, today, when, window, check_stored)
            ]
            if item.transfer_account_id is not None:
                to_account = item.transfer_account_id
                fits = [tx for tx in fits if await _paired_into(session, tx, to_account)]
            if not fits:
                continue
            expected = abs(Decimal(item.amount or 0))
            best = min(
                fits,
                key=lambda tx: (
                    -token_overlap(item.description, tx.description),
                    abs((tx.date - when).days),
                    abs(abs(Decimal(tx.amount)) - expected),
                ),
            )
            found.append((item, occurrence, best.id))
            taken.add(best.id)
    return found


async def expected_payment_projections(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    today: date,
    end: date,
    account_ids: list[uuid.UUID],
    *,
    narrowed: bool = False,
) -> list[dict]:
    """Unmatched expected payments as cash-flow projection dicts.

    One still unmatched after its date is still expected, so it lands
    tomorrow. One on an account outside ``account_ids`` (a loan, an
    investment, or outside a Collection filter) is left out; one with no
    account counts unless the report is narrowed to particular accounts.
    A transfer gives one row per leg in scope (see ``_legs``).

    Fork: a repeating expected payment gives one row per unmatched
    occurrence up to ``end``; like a one-off, an occurrence still unmatched
    after its date lands tomorrow.
    """
    arrived = await pending_expected_matches(session, workspace_id)
    matched = await _matched_occurrences(session, workspace_id) | {
        (item_id, occurrence) for item_id, occurrence in arrived if occurrence is not None
    }
    stmt = select(ForecastItem).where(
        ForecastItem.workspace_id == workspace_id,
        ForecastItem.scenario_id.is_(None),
        or_(
            and_(
                ForecastItem.kind == "one_off",
                ForecastItem.matched_transaction_id.is_(None),
                ForecastItem.date.is_not(None),
                ForecastItem.date <= end,
            ),
            and_(
                ForecastItem.kind == "repeating",
                ForecastItem.start_date.is_not(None),
                ForecastItem.frequency.is_not(None),
                ForecastItem.start_date <= end,
            ),
        ),
    )
    in_scope = _scope_test(account_ids, narrowed)
    tomorrow = today + timedelta(days=1)
    out: list[dict] = []
    for item in (await session.execute(stmt)).scalars():
        legs = _legs(item, in_scope)
        if not legs:
            continue
        if item.kind == "one_off":
            if (item.id, None) in arrived:
                continue
            dates = [cast(date, item.date)]
        else:
            last = min(item.end_date, end) if item.end_date else end
            dates = [
                d for d in series_dates(item, cast(date, item.start_date), last)
                if (item.id, d) not in matched
            ]
        for d in dates:
            for leg_type in legs:
                out.append({
                    "date": max(d, tomorrow),
                    "amount": float(abs(item.amount or 0)),
                    "currency": item.currency,
                    "type": leg_type,
                    "category_id": item.category_id,
                })
    return out


def _scope_test(account_ids: Iterable[uuid.UUID], narrowed: bool) -> Callable[[Optional[uuid.UUID]], bool]:
    """Whether a row on an account (None: no account) is in scope."""
    scope = set(account_ids)

    def in_scope(account_id: Optional[uuid.UUID]) -> bool:
        return account_id in scope if account_id is not None else not narrowed

    return in_scope


def _legs(item: ForecastItem, in_scope: Callable[[Optional[uuid.UUID]], bool]) -> list[str]:
    """Fork: the direction of each side of an item that is in scope.

    A plain payment has one side. A transfer has a debit on ``account_id``
    and a credit on ``transfer_account_id``; with both in scope they net to
    nothing, so neither is returned (the money stays in the line).
    """
    if item.transfer_account_id is None:
        return [item.type or "debit"] if in_scope(item.account_id) else []
    legs = [
        leg_type
        for account_id, leg_type in ((item.account_id, "debit"), (item.transfer_account_id, "credit"))
        if in_scope(account_id)
    ]
    return [] if len(legs) == 2 else legs


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def list_scenarios(session: AsyncSession, workspace_id: uuid.UUID) -> list[ForecastScenario]:
    result = await session.execute(
        select(ForecastScenario)
        .where(ForecastScenario.workspace_id == workspace_id)
        .order_by(ForecastScenario.position, ForecastScenario.created_at)
    )
    return list(result.scalars())


async def get_scenario(
    session: AsyncSession, workspace_id: uuid.UUID, scenario_id: uuid.UUID
) -> Optional[ForecastScenario]:
    result = await session.execute(
        select(ForecastScenario).where(
            ForecastScenario.id == scenario_id, ForecastScenario.workspace_id == workspace_id
        )
    )
    return result.scalar_one_or_none()


async def create_scenario(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, data: ForecastScenarioCreate
) -> ForecastScenario:
    position = data.position
    if position is None:
        last = (
            await session.execute(
                select(func.max(ForecastScenario.position)).where(
                    ForecastScenario.workspace_id == workspace_id
                )
            )
        ).scalar_one_or_none()
        position = 0 if last is None else last + 1
    scenario = ForecastScenario(
        user_id=user_id,
        workspace_id=workspace_id,
        name=data.name,
        color=data.color,
        is_enabled=data.is_enabled,
        position=position,
    )
    session.add(scenario)
    await session.commit()
    await session.refresh(scenario)
    return scenario


async def update_scenario(
    session: AsyncSession, workspace_id: uuid.UUID, scenario_id: uuid.UUID, data: ForecastScenarioUpdate
) -> Optional[ForecastScenario]:
    scenario = await get_scenario(session, workspace_id, scenario_id)
    if scenario is None:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        if value is None:
            raise ValueError(f"{field} cannot be empty")
        setattr(scenario, field, value)
    await session.commit()
    await session.refresh(scenario)
    return scenario


async def delete_scenario(session: AsyncSession, workspace_id: uuid.UUID, scenario_id: uuid.UUID) -> bool:
    scenario = await get_scenario(session, workspace_id, scenario_id)
    if scenario is None:
        return False
    await session.delete(scenario)
    await session.commit()
    return True


async def list_items(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    scenario_id: Optional[uuid.UUID] = None,
    expected_only: bool = False,
) -> list[ForecastItem]:
    stmt = select(ForecastItem).where(ForecastItem.workspace_id == workspace_id)
    if expected_only:
        stmt = stmt.where(ForecastItem.scenario_id.is_(None))
    elif scenario_id is not None:
        stmt = stmt.where(ForecastItem.scenario_id == scenario_id)
    stmt = stmt.order_by(ForecastItem.date, ForecastItem.start_date, ForecastItem.created_at)
    return list((await session.execute(stmt)).scalars())


async def get_item(
    session: AsyncSession, workspace_id: uuid.UUID, item_id: uuid.UUID
) -> Optional[ForecastItem]:
    result = await session.execute(
        select(ForecastItem).where(ForecastItem.id == item_id, ForecastItem.workspace_id == workspace_id)
    )
    return result.scalar_one_or_none()


async def _check_refs(session: AsyncSession, workspace_id: uuid.UUID, values: dict) -> Optional[Account]:
    """Every referenced row must belong to the workspace. Returns the account."""
    account = None
    if values.get("scenario_id") is not None:
        if await get_scenario(session, workspace_id, values["scenario_id"]) is None:
            raise ValueError("Scenario not found")
    if values.get("account_id") is not None:
        account = (
            await session.execute(
                select(Account)
                .outerjoin(BankConnection)
                .where(
                    Account.id == values["account_id"],
                    or_(
                        Account.workspace_id == workspace_id,
                        BankConnection.workspace_id == workspace_id,
                    ),
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise ValueError("Account not found")
    # Fork: a transfer's to-account belongs to the workspace too.
    if values.get("transfer_account_id") is not None:
        found = (
            await session.execute(
                select(Account.id)
                .outerjoin(BankConnection)
                .where(
                    Account.id == values["transfer_account_id"],
                    or_(
                        Account.workspace_id == workspace_id,
                        BankConnection.workspace_id == workspace_id,
                    ),
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise ValueError("Transfer account not found")
    if values.get("category_id") is not None:
        found = (
            await session.execute(
                select(Category.id).where(
                    Category.id == values["category_id"], Category.workspace_id == workspace_id
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise ValueError("Category not found")
    if values.get("recurring_transaction_id") is not None:
        found = (
            await session.execute(
                select(RecurringTransaction.id).where(
                    RecurringTransaction.id == values["recurring_transaction_id"],
                    RecurringTransaction.workspace_id == workspace_id,
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise ValueError("Recurring transaction not found")
    return account


def _validate_item(values: dict) -> None:
    """What each kind needs. Raises ValueError with a readable message."""
    kind = values["kind"]
    amount = values.get("amount")
    if values.get("scenario_id") is None and kind not in ("one_off", "repeating", "budget"):
        raise ValueError("An expected payment is a one-off or repeating payment, or a budget")
    start, end = values.get("start_date"), values.get("end_date")
    if start and end and end < start:
        raise ValueError("End date is before the start date")
    # Fork: a transfer leaves one own account for another.
    if values.get("transfer_account_id") is not None:
        if kind not in ("one_off", "repeating"):
            raise ValueError("Only a payment can be a transfer")
        if values.get("account_id") is None:
            raise ValueError("A transfer needs the account it leaves")
        if values["transfer_account_id"] == values["account_id"]:
            raise ValueError("A transfer needs two different accounts")
        if values.get("type") != "debit":
            raise ValueError("A transfer is money out of the from-account (type debit)")
    if kind == "budget":
        # Fork: a plan for everyday spending, part of the base forecast.
        if values.get("scenario_id") is not None:
            raise ValueError("A budget is part of the plan, not of a scenario")
        if amount is None or amount <= 0:
            raise ValueError("Amount must be greater than zero")
        if values.get("type") != "debit":
            raise ValueError("A budget is money out (type debit)")
        if values.get("frequency") not in BUDGET_FREQUENCIES or start is None:
            raise ValueError("A budget needs a weekly, fortnightly or monthly frequency and a start date")
        return
    if kind in ("one_off", "repeating"):
        if amount is None or amount <= 0:
            raise ValueError("Amount must be greater than zero")
        if values.get("type") not in ("debit", "credit"):
            raise ValueError("Type is required")
        if kind == "one_off" and values.get("date") is None:
            raise ValueError("Date is required")
        if kind == "repeating" and (values.get("frequency") is None or start is None):
            raise ValueError("Frequency and start date are required")
        return
    if start is None:
        raise ValueError("Start date is required")
    mode = values.get("change_mode")
    if kind == "recurring_change":
        if values.get("recurring_transaction_id") is None:
            raise ValueError("Recurring transaction is required")
        if mode not in ("amount", "pause", "stop"):
            raise ValueError("Change must be amount, pause or stop")
        if mode == "amount" and (amount is None or amount < 0):
            raise ValueError("New amount is required")
        if mode == "pause" and end is None:
            raise ValueError("A pause needs an end date")
        return
    if kind == "loan_change":
        if values.get("account_id") is None:
            raise ValueError("Loan is required")
        if mode == "rate":
            rate = values.get("percent")
            if rate is None or not 0 <= rate <= 100:
                raise ValueError("Interest rate must be between 0 and 100")
            # Optional: a repayment set alongside the new rate (owner 2026-10-14).
            if amount is not None and amount <= 0:
                raise ValueError("Repayment must be greater than zero")
        elif mode in ("amount", "lump_sum"):
            if amount is None or amount <= 0:
                raise ValueError("Amount must be greater than zero")
        else:
            raise ValueError("Change must be rate, amount or lump_sum")
        return
    # income_change, spending_change: a signed change, by amount or percent.
    if kind == "spending_change" and values.get("category_id") is None:
        raise ValueError("Category is required")
    if mode == "percent":
        if values.get("percent") is None:
            raise ValueError("Percent is required")
    elif mode == "amount":
        if amount is None:
            raise ValueError("Amount is required")
    else:
        raise ValueError("Change must be amount or percent")


def _require_loan(account: Optional[Account]) -> None:
    """A loan change must point at an open loan account."""
    if account is None or account.type != "loan":
        raise ValueError("The account is not a loan")
    if account.is_closed:
        raise ValueError("Loan account is closed")


async def _primary_currency(session: AsyncSession, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    return user.primary_currency if user else get_settings().default_currency


async def create_item(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, data: ForecastItemCreate
) -> ForecastItem:
    values = data.model_dump()
    if values.get("type") is None and (values.get("transfer_account_id") is not None or values["kind"] == "budget"):
        values["type"] = "debit"  # Fork: a transfer leaves the from-account; a budget is spent
    account = await _check_refs(session, workspace_id, values)
    _validate_item(values)
    if values["kind"] == "loan_change":
        _require_loan(account)
        if values["change_mode"] == "rate" and values.get("amount"):
            values["recalculate_repayment"] = False  # the repayment is the one given
        elif values["change_mode"] == "rate" and values.get("recalculate_repayment") is None:
            values["recalculate_repayment"] = True  # a refix recalculates by default
    if not values.get("currency"):
        values["currency"] = account.currency if account else await _primary_currency(session, user_id)
    item = ForecastItem(user_id=user_id, workspace_id=workspace_id, **values)
    session.add(item)
    await session.flush()
    # A payment whose bank row has already landed is matched at once.
    if item.scenario_id is None:
        await match_expected_payments(session, workspace_id)
    await session.commit()
    await session.refresh(item)
    await session.refresh(item, ["matches"])
    return item


async def update_item(
    session: AsyncSession, workspace_id: uuid.UUID, item_id: uuid.UUID, data: ForecastItemUpdate
) -> Optional[ForecastItem]:
    item = await get_item(session, workspace_id, item_id)
    if item is None:
        return None
    changes = data.model_dump(exclude_unset=True)
    await _check_refs(session, workspace_id, changes)
    merged = {
        column: getattr(item, column)
        for column in (
            "scenario_id", "kind", "amount", "type", "date", "start_date", "end_date", "frequency",
            "account_id", "transfer_account_id", "category_id", "recurring_transaction_id", "change_mode",
            "percent", "recalculate_repayment",
        )
    }
    merged.update(changes)
    _validate_item(merged)
    if merged["kind"] == "loan_change":
        _require_loan(await _check_refs(session, workspace_id, {"account_id": merged["account_id"]}))
    for field, value in changes.items():
        if field == "currency" and value is None:
            continue
        setattr(item, field, value)
    # An edited payment is a new promise; let the matcher look again.
    if item.scenario_id is None and {
        "amount", "date", "account_id", "transfer_account_id", "type", "currency", "start_date", "frequency",
    } & changes.keys():
        item.matched_transaction_id = None
        await session.execute(delete(ForecastItemMatch).where(ForecastItemMatch.item_id == item.id))
        await session.flush()
        await match_expected_payments(session, workspace_id)
    elif item.scenario_id is None and "end_date" in changes and item.end_date is not None:
        # Fork: a series cut short keeps the matches up to its new end.
        await session.execute(
            delete(ForecastItemMatch).where(
                ForecastItemMatch.item_id == item.id, ForecastItemMatch.occurrence_date > item.end_date
            )
        )
        await session.flush()
        await match_expected_payments(session, workspace_id)
    await session.commit()
    await session.refresh(item)
    await session.refresh(item, ["matches"])
    return item


async def delete_item(session: AsyncSession, workspace_id: uuid.UUID, item_id: uuid.UUID) -> bool:
    item = await get_item(session, workspace_id, item_id)
    if item is None:
        return False
    await session.delete(item)
    await session.commit()
    return True


# ---------------------------------------------------------------------------
# the forecast
# ---------------------------------------------------------------------------


def _sign(tx_type: Optional[str]) -> float:
    return 1.0 if tx_type == "credit" else -1.0


def _summary(daily: dict[date, float]) -> ForecastLineSummary:
    days = sorted(daily)
    lowest = min(days, key=lambda d: (daily[d], d))
    return ForecastLineSummary(
        end_balance=round(daily[days[-1]], 2),
        lowest_balance=round(daily[lowest], 2),
        lowest_date=lowest,
    )


def _points(daily: dict[date, float], start: date, end: date, interval: str) -> list[ForecastPoint]:
    from app.services.report_service import _date_points

    dates = _date_points(start, end, interval)
    if dates and dates[0] != start:
        dates.insert(0, start)  # monthly points are month ends; the line starts today
    return [ForecastPoint(date=p, value=round(daily[p], 2)) for p in dates if p in daily]


async def _scenario_deltas(
    session: AsyncSession,
    scenario: ForecastScenario,
    *,
    today: date,
    end: date,
    to_primary,
    account_ids: set[uuid.UUID],
    recurring_projections: list[dict],
    recurring_by_id: dict[uuid.UUID, RecurringTransaction],
    estimates: dict[uuid.UUID, float],
    include_spending: bool,
    narrowed: bool = False,
    budgeted: Optional[Callable[[date, Optional[uuid.UUID]], bool]] = None,
) -> dict[date, float]:
    """The change a scenario makes to the base line, per day (primary currency).

    Fork: in an account view (``narrowed``) a payment with no account is
    left out, as expected payments are."""
    from app.services.recurring_transaction_service import get_occurrences_in_range
    from app.services.report_service import _spread_monthly

    deltas: dict[date, float] = {}
    first = today + timedelta(days=1)

    def add(d: date, value: float) -> None:
        if today < d <= end and value:
            deltas[d] = deltas.get(d, 0.0) + value

    def window(item: ForecastItem) -> tuple[date, date]:
        return max(cast(date, item.start_date), first), min(item.end_date or end, end)

    in_scope = _scope_test(account_ids, narrowed)
    for item in scenario.items:
        if item.kind == "loan_change":
            continue  # projected per loan in compute_forecast
        sign = 0.0
        if item.kind in ("one_off", "repeating"):
            # Fork: a transfer counts by its legs in scope.
            sign = sum(_sign(leg) for leg in _legs(item, in_scope))
            if not sign:
                continue
        elif item.account_id is not None and item.account_id not in account_ids:
            continue  # a loan, an investment, a closed account: not cash
        if item.kind == "one_off":
            if item.date is not None:
                add(item.date, sign * await to_primary(Decimal(item.amount or 0), item.currency))
        elif item.kind == "repeating":
            amount = sign * await to_primary(Decimal(item.amount or 0), item.currency)
            for d in get_occurrences_in_range(
                start=cast(date, item.start_date),
                frequency=cast(str, item.frequency),
                end_date=item.end_date,
                range_start=first,
                range_end=end + timedelta(days=1),
            ):
                add(d, amount)
        elif item.kind == "recurring_change":
            rec = recurring_by_id.get(item.recurring_transaction_id)
            if rec is None:
                continue
            for proj in recurring_projections:
                if proj["recurring_transaction_id"] != rec.id:
                    continue
                d = proj["date"]
                signed = _sign(proj["type"]) * await to_primary(Decimal(str(proj["amount"])), proj["currency"])
                if item.change_mode == "stop" and d >= item.start_date:
                    add(d, -signed)
                elif item.change_mode == "pause" and item.start_date <= d <= item.end_date:
                    add(d, -signed)
                elif item.change_mode == "amount" and d >= item.start_date and rec.amount:
                    # A loan payment's legs scale with the whole payment.
                    ratio = float(Decimal(item.amount or 0) / Decimal(rec.amount))
                    add(d, signed * (ratio - 1))
        elif item.kind == "income_change":
            start, stop = window(item)
            for proj in recurring_projections:
                rec = recurring_by_id.get(proj["recurring_transaction_id"])
                if rec is None or rec.type != "credit" or proj["type"] != "credit":
                    continue
                if item.recurring_transaction_id and rec.id != item.recurring_transaction_id:
                    continue
                d = proj["date"]
                if not start <= d <= stop:
                    continue
                if item.change_mode == "percent":
                    base = await to_primary(Decimal(str(proj["amount"])), proj["currency"])
                    add(d, base * float(item.percent or 0) / 100)
                else:
                    add(d, await to_primary(Decimal(item.amount or 0), item.currency))
        elif item.kind == "spending_change":
            # Applied against the everyday spending estimate, so with the
            # estimate switched off there is nothing to change.
            if not include_spending or item.category_id is None:
                continue
            monthly = estimates.get(item.category_id, 0.0)
            if item.change_mode == "percent":
                change = monthly * float(item.percent or 0) / 100
            else:
                change = await to_primary(Decimal(item.amount or 0), item.currency)
            change = max(change, -monthly)  # spending can't go below nothing
            start, stop = window(item)
            for d, daily in _spread_monthly(change, start, stop):
                # Fork: a budget switches the estimate off, and its change too.
                if budgeted is None or not budgeted(d, item.category_id):
                    add(d, -daily)
    return deltas


async def _plan_budgets(
    session: AsyncSession, workspace_id: uuid.UUID, account_ids: list[uuid.UUID], narrowed: bool
) -> list[ForecastItem]:
    """Fork: the budgets in scope (on an account in it, or on none unless
    the forecast is an account view)."""
    in_scope = _scope_test(account_ids, narrowed)
    rows = await session.execute(
        select(ForecastItem).where(
            ForecastItem.workspace_id == workspace_id,
            ForecastItem.scenario_id.is_(None),
            ForecastItem.kind == "budget",
            ForecastItem.start_date.is_not(None),
            ForecastItem.frequency.is_not(None),
        )
    )
    return [item for item in rows.scalars() if in_scope(item.account_id)]


def _budgeted(budgets: list[ForecastItem], end: date) -> Callable[[date, Optional[uuid.UUID]], bool]:
    """Fork: whether a budget covers a day's spending in a category, so the
    spending estimate is switched off there. A budget with no category
    covers every category over its dates; several cover their union."""
    spans = [
        (cast(date, b.start_date), b.end_date or end, b.category_id) for b in budgets
    ]

    def covered(d: date, category_id: Optional[uuid.UUID]) -> bool:
        return any(
            start <= d <= stop and (cat is None or cat == category_id) for start, stop, cat in spans
        )

    return covered


def _budget_projections(budgets: list[ForecastItem], today: date, end: date) -> list[dict]:
    """Fork: each budget's amount on each of its dates after today, as
    cash-flow projection dicts (money out)."""
    out: list[dict] = []
    for item in budgets:
        last = min(item.end_date, end) if item.end_date else end
        for d in series_dates(item, cast(date, item.start_date), last):
            if d > today:
                out.append({
                    "date": d,
                    "amount": float(abs(item.amount or 0)),
                    "currency": item.currency,
                    "type": "debit",
                    "category_id": item.category_id,
                })
    return out


def _history_dates(start: date, today: date, interval: str) -> list[date]:
    from app.services.report_service import _date_points

    dates = [d for d in _date_points(start, today, interval) if d <= today]
    if not dates or dates[0] != start:
        dates.insert(0, start)
    if dates[-1] != today:
        dates.append(today)
    return dates


async def _balance_history(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    account_ids: list[uuid.UUID],
    dates: list[date],
    currency: str,
) -> dict[date, float]:
    """The real balance of ``account_ids`` at the end of each of ``dates``.

    Built the way Balance Evolution builds its line: the dashboard's balance
    the day before the first date, plus each day's posted movements.
    """
    from app.services.dashboard_service import _balance_at, _daily_balance_deltas_by_date

    start, last = dates[0], dates[-1]
    running = await _balance_at(
        session, workspace_id, start - timedelta(days=1),
        primary_currency_hint=currency, account_ids=account_ids,
    )
    deltas = await _daily_balance_deltas_by_date(
        session, workspace_id, start, last + timedelta(days=1),
        primary_currency_hint=currency, account_ids=account_ids,
    )
    out: dict[date, float] = {}
    day, i = start, 0
    ordered = sorted(dates)
    while day <= last:
        running += deltas.get(day, 0.0)
        if day == ordered[i]:
            out[day] = round(running, 2)
            i += 1
        day += timedelta(days=1)
    return out


@dataclass
class _Loan:
    account: Account
    debt: float  # owed today, positive
    terms: Optional[LoanTerms]
    base: Optional[LoanSchedule]


async def _open_loans(session: AsyncSession, workspace_id: uuid.UUID, today: date) -> list[_Loan]:
    """Open loans that still owe money, with their base schedule when the
    details allow one. The balance is the one the rest of the app shows."""
    from app.services.dashboard_service import _account_balance_at, _get_open_accounts

    loans: list[_Loan] = []
    accounts = sorted(
        (a for a in await _get_open_accounts(session, workspace_id) if a.type == "loan"),
        key=lambda a: (a.position, a.display_name or a.name),
    )
    for account in accounts:
        debt = round(-await _account_balance_at(session, account, today), 2)
        if debt <= 0:
            continue
        terms = loan_terms(account.loan_details, debt, today)
        loans.append(_Loan(account, debt, terms, project_loan(terms) if terms else None))
    return loans


async def loan_repayment_projections(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    today: date,
    end: date,
    *,
    narrowed: bool = False,
) -> list[dict]:
    """Fork: repayments of loans no recurring schedule pays, as cash-flow
    projection dicts (money leaving cash), until each loan is paid off.

    A loan whose ``loan_details`` give a table loan's rate, repayment and
    frequency, and that no active recurring payment (a loan payment schedule
    or a plain recurring transfer) pays into, would otherwise be missing from
    every forecast. The rows have no account, so a report narrowed to
    particular accounts leaves them out, as it does invoices.
    """
    from app.services.category_service import find_default_category
    from app.services.loan_repayment_category import LOAN_REPAYMENTS_KEY

    if narrowed:
        return []
    loans = [loan for loan in await _open_loans(session, workspace_id, today) if loan.base is not None]
    if not loans:
        return []
    scheduled = set(
        (
            await session.execute(
                select(RecurringTransaction.transfer_account_id).where(
                    RecurringTransaction.workspace_id == workspace_id,
                    RecurringTransaction.is_active.is_(True),
                    RecurringTransaction.transfer_account_id.in_([loan.account.id for loan in loans]),
                )
            )
        ).scalars()
    )
    # Fork: a repeating expected transfer into the loan (a plan's repayments)
    # stands in for the loan card's repayments over the dates it covers.
    planned: dict[uuid.UUID, list[tuple[date, Optional[date]]]] = {}
    for item in (
        await session.execute(
            select(ForecastItem).where(
                ForecastItem.workspace_id == workspace_id,
                ForecastItem.scenario_id.is_(None),
                ForecastItem.kind == "repeating",
                ForecastItem.transfer_account_id.in_([loan.account.id for loan in loans]),
                ForecastItem.start_date.is_not(None),
            )
        )
    ).scalars():
        planned.setdefault(cast(uuid.UUID, item.transfer_account_id), []).append(
            (cast(date, item.start_date), item.end_date)
        )
    category_id = await find_default_category(session, workspace_id, LOAN_REPAYMENTS_KEY)
    out: list[dict] = []
    for loan in loans:
        if loan.account.id in scheduled or loan.base is None:
            continue
        covered = planned.get(loan.account.id, [])
        for period in loan.base.periods:
            if period.date > end:
                break
            if any(start <= period.date and (last is None or period.date <= last) for start, last in covered):
                continue
            if period.date > today and period.repayment:
                out.append({
                    "date": period.date,
                    "amount": period.repayment,
                    "currency": loan.account.currency,
                    "type": "debit",
                    "category_id": category_id,
                })
    return out


def _loan_changes(scenario: ForecastScenario, loan_id: uuid.UUID, today: date) -> list[LoanChange]:
    changes: list[LoanChange] = []
    for item in scenario.items:
        if item.kind != "loan_change" or item.account_id != loan_id or item.start_date is None:
            continue
        if item.change_mode == "rate" and item.percent is not None:
            # A repayment given with the rate replaces whatever the rate
            # change would have made it.
            changes.append(LoanChange(
                start=item.start_date, rate=float(item.percent),
                recalculate=item.recalculate_repayment is not False and not item.amount,
                repayment=float(item.amount) if item.amount else None,
            ))
        elif item.change_mode == "amount" and item.amount:
            changes.append(LoanChange(start=item.start_date, repayment=float(item.amount)))
        elif item.change_mode == "lump_sum" and item.amount:
            # Like an overdue expected payment, a lump sum dated today or
            # earlier is still to come: it leaves cash tomorrow.
            changes.append(LoanChange(
                start=max(item.start_date, today + timedelta(days=1)), lump_sum=float(item.amount),
            ))
    return changes


# The whole-term chart stops here even if a line never pays off.
TERM_CHART_MAX_YEARS = 40


def _term_points(schedule: LoanSchedule, loan: _Loan, today: date, end: date) -> list[LoanPoint]:
    """Balance owed at today and each month-end to ``end``; zero once paid off."""
    from app.services.report_service import _date_points

    dates = _date_points(today, end, "monthly")
    if not dates or dates[0] != today:
        dates.insert(0, today)
    periods = schedule.periods
    points: list[LoanPoint] = []
    balance, i = loan.debt, 0
    for p in dates:
        interest = principal = 0.0
        while i < len(periods) and periods[i].date <= p:
            interest += periods[i].interest
            principal += periods[i].principal
            balance = periods[i].closing
            i += 1
        points.append(LoanPoint(
            date=p, balance=round(balance, 2), interest=round(interest, 2), principal=round(principal, 2),
        ))
    return points


def _fill_term_points(
    forecast: LoanForecast, schedules: list[LoanSchedule], loan: _Loan, today: date
) -> None:
    """Give every line of a loan the whole-term balance, to a shared end."""
    from app.services.report_service import _add_months

    if forecast.base is None or not schedules:
        return
    payoffs = [s.payoff_date for s in schedules if s.payoff_date is not None]
    cap = _add_months(today, TERM_CHART_MAX_YEARS * 12)
    end = min(max(payoffs), cap) if payoffs else cap
    lines: list[LoanLine] = [forecast.base, *forecast.scenarios]
    for line, schedule in zip(lines, schedules):
        line.term_points = _term_points(schedule, loan, today, end)


def _loan_line(schedule: LoanSchedule, loan: _Loan, today: date, end: date, interval: str) -> LoanLine:
    from app.services.report_service import _date_points

    dates = _date_points(today, end, "weekly" if interval == "daily" else interval)
    if not dates or dates[0] != today:
        dates.insert(0, today)
    periods = schedule.periods
    points: list[LoanPoint] = []
    balance, i = loan.debt, 0
    for p in dates:
        interest = principal = 0.0
        while i < len(periods) and periods[i].date <= p:
            interest += periods[i].interest
            principal += periods[i].principal
            balance = periods[i].closing
            i += 1
        points.append(LoanPoint(
            date=p, balance=round(balance, 2), interest=round(interest, 2), principal=round(principal, 2),
        ))
    in_horizon = [p for p in periods if p.date <= end]
    last = in_horizon[-1] if in_horizon else (periods[0] if periods else None)
    return LoanLine(
        points=points,
        payoff_date=schedule.payoff_date,
        total_interest=schedule.total_interest,
        repayment=last.payment if last else (loan.terms.repayment if loan.terms else 0.0),
    )


async def compute_forecast(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: int = 12,
    interval: str = "monthly",
    scenario_ids: Optional[list[uuid.UUID]] = None,
    include_spending: bool = True,
    account_ids: Optional[list[uuid.UUID]] = None,
) -> ForecastResponse:
    """Base line and one line per scenario from today to ``months`` ahead.

    ``scenario_ids`` picks the scenarios; by default the enabled ones.

    Fork: ``account_ids`` is an account view: the line, its history, the
    spending estimate, recurring and expected payments and scenario changes
    keep to those of the cash-flow accounts. Rows with no account (invoices,
    repayments of loans no schedule pays, payments with no account) are left
    out of it, as Reports → Cash Flow leaves them out under a filter.
    """
    from app.services.dashboard_service import _get_recurring_projections
    from app.services.report_service import (
        _add_months,
        _get_category_spending_estimate,
        _make_to_primary,
        _spread_monthly,
        cash_flow_account_ids,
        get_cash_flow_report,
    )

    today = app_today()
    end = _add_months(today, months)
    primary_currency = await _primary_currency(session, user_id)
    to_primary = _make_to_primary(session, primary_currency)
    narrowed = account_ids is not None
    account_ids = await cash_flow_account_ids(session, workspace_id, account_ids)

    estimates = await _get_category_spending_estimate(
        session, workspace_id, today, to_primary, account_ids
    )
    # Fork: plan budgets project on their schedule and switch the estimate off.
    budgets = await _plan_budgets(session, workspace_id, account_ids, narrowed)
    budgeted = _budgeted(budgets, end)
    extra: list[dict] = _budget_projections(budgets, today, end)
    if include_spending:
        for est in estimates:
            for d, amount in _spread_monthly(est["monthly_amount"], today + timedelta(days=1), end):
                if budgeted(d, est["category_id"]):
                    continue
                extra.append({
                    "date": d,
                    "amount": amount,
                    "currency": primary_currency,
                    "type": "debit",
                    "category_id": est["category_id"],
                })

    report = await get_cash_flow_report(
        session, workspace_id, user_id, months, "daily", primary_currency,
        account_ids=account_ids if narrowed else None,
        extra_projections=extra,
    )
    daily: dict[date, float] = {}
    for point in report.trend:
        d = date.fromisoformat(point.date)
        if today <= d <= end:
            daily[d] = point.value
    starting = daily.get(today, 0.0)

    stmt = (
        select(ForecastScenario)
        .where(ForecastScenario.workspace_id == workspace_id)
        .options(selectinload(ForecastScenario.items))
        .order_by(ForecastScenario.position, ForecastScenario.created_at)
    )
    if scenario_ids is not None:
        stmt = stmt.where(ForecastScenario.id.in_(scenario_ids))
    else:
        stmt = stmt.where(ForecastScenario.is_enabled.is_(True))
    scenarios = list((await session.execute(stmt)).scalars())

    # The same rows the base line projected, so a change removes exactly what
    # the base added, and the list of recurring payments shows exactly what the
    # base line counts.
    recurring_by_id: dict[uuid.UUID, RecurringTransaction] = {}
    recurring_projections: list[dict] = await _get_recurring_projections(
        session, workspace_id, today + timedelta(days=1), end + timedelta(days=1), account_ids,
        with_recurring_id=True,
    )
    ids = {p["recurring_transaction_id"] for p in recurring_projections}
    if ids:
        recurring_by_id = {
            r.id: r
            for r in (
                await session.execute(
                    select(RecurringTransaction).where(RecurringTransaction.id.in_(ids))
                )
            ).scalars()
        }

    # Fork (owner 2026-10-14): as much history as the forecast looks ahead,
    # same accounts. Its last point is today, at the projection's start.
    history = _history_dates(_add_months(today, -months), today, interval)
    actual = await _balance_history(session, workspace_id, account_ids, history, primary_currency)
    actual[today] = round(starting, 2)

    base_summary = _summary(daily)
    estimate_by_cat = {e["category_id"]: e["monthly_amount"] for e in estimates}
    account_set = set(account_ids)
    loans = await _open_loans(session, workspace_id, today)
    # Fork: in an account view a loan change moves cash only when the
    # account paying the loan (by a recurring schedule) is in it.
    payers: dict[uuid.UUID, uuid.UUID] = {}
    if narrowed and loans:
        payers = {
            loan_id: payer
            for loan_id, payer in (
                await session.execute(
                    select(RecurringTransaction.transfer_account_id, RecurringTransaction.account_id).where(
                        RecurringTransaction.workspace_id == workspace_id,
                        RecurringTransaction.is_active.is_(True),
                        RecurringTransaction.transfer_account_id.in_([loan.account.id for loan in loans]),
                    )
                )
            ).all()
            if loan_id is not None and payer in account_set
        }
    loan_forecasts = [
        LoanForecast(
            account_id=loan.account.id,
            name=loan.account.display_name or loan.account.name,
            currency=loan.account.currency,
            balance=loan.debt,
            rate=loan.terms.rate if loan.terms else None,
            frequency=loan.terms.frequency if loan.terms else None,
            missing_details=loan.base is None,
            base=_loan_line(loan.base, loan, today, end, interval) if loan.base else None,
            actuals=[
                ForecastPoint(date=d, value=round(-v, 2))
                for d, v in (await _balance_history(
                    session, workspace_id, [loan.account.id], history, loan.account.currency
                )).items()
            ][:-1] + [ForecastPoint(date=today, value=loan.debt)],
        )
        for loan in loans
    ]
    # Per loan, the base schedule then each scenario's, in the order of its lines.
    loan_schedules: list[list[LoanSchedule]] = [[loan.base] if loan.base else [] for loan in loans]
    series: list[ForecastScenarioSeries] = []
    for scenario in scenarios:
        deltas = await _scenario_deltas(
            session,
            scenario,
            today=today,
            end=end,
            to_primary=to_primary,
            account_ids=account_set,
            recurring_projections=recurring_projections,
            recurring_by_id=recurring_by_id,
            estimates=estimate_by_cat,
            include_spending=include_spending,
            narrowed=narrowed,
            budgeted=budgeted,
        )
        for loan, forecast, schedules in zip(loans, loan_forecasts, loan_schedules):
            changes = _loan_changes(scenario, loan.account.id, today)
            if not changes or loan.terms is None or loan.base is None or forecast.base is None:
                continue
            schedule = project_loan(loan.terms, changes)
            for d, value in cash_difference(loan.base, schedule).items():
                if narrowed and loan.account.id not in payers:
                    break
                if today < d <= end:
                    deltas[d] = deltas.get(d, 0.0) + await to_primary(
                        Decimal(str(value)), loan.account.currency
                    )
            loan_line = _loan_line(schedule, loan, today, end, interval)
            schedules.append(schedule)
            both_pay_off = schedule.payoff_date is not None and loan.base.payoff_date is not None
            forecast.scenarios.append(LoanScenarioLine(
                **loan_line.model_dump(),
                scenario_id=scenario.id,
                name=scenario.name,
                color=scenario.color,
                interest_saved=(
                    round(forecast.base.total_interest - loan_line.total_interest, 2) if both_pay_off else None
                ),
                payoff_change_days=(
                    (schedule.payoff_date - loan.base.payoff_date).days
                    if both_pay_off else None
                ),
            ))
        line: dict[date, float] = {}
        running = 0.0
        for d in sorted(daily):
            running += deltas.get(d, 0.0)
            line[d] = daily[d] + running
        summary = _summary(line)
        series.append(ForecastScenarioSeries(
            id=scenario.id,
            name=scenario.name,
            color=scenario.color,
            points=_points(line, today, end, interval),
            summary=summary,
            difference=round(summary.end_balance - base_summary.end_balance, 2),
        ))

    for loan, forecast, schedules in zip(loans, loan_forecasts, loan_schedules):
        _fill_term_points(forecast, schedules, loan, today)
    return ForecastResponse(
        currency=primary_currency,
        interval=interval,
        start_date=today,
        end_date=end,
        starting_balance=round(starting, 2),
        include_spending=include_spending,
        account_ids=account_ids,
        base=_points(daily, today, end, interval),
        actuals=[ForecastPoint(date=d, value=actual[d]) for d in history],
        summary=base_summary,
        scenarios=series,
        spending_estimates=[SpendingEstimate(**e) for e in estimates],
        loans=loan_forecasts,
        recurring_payments=_recurring_payments(recurring_projections, recurring_by_id),
    )


def _recurring_payments(
    projections: list[dict], by_id: dict[uuid.UUID, RecurringTransaction],
) -> list[ForecastRecurringPayment]:
    """One row per schedule and date (a schedule split into legs is one
    payment), in date order."""
    totals: dict[tuple[uuid.UUID, date, str, str], float] = {}
    for p in projections:
        rec_id = p.get("recurring_transaction_id")
        if rec_id is None or rec_id not in by_id:
            continue
        key = (rec_id, p["date"], p["type"], p["currency"])
        totals[key] = totals.get(key, 0.0) + float(p["amount"])
    rows = [
        ForecastRecurringPayment(
            recurring_transaction_id=rec_id, date=d, description=by_id[rec_id].description,
            amount=round(abs(amount), 2), type=type_, currency=currency, account_id=by_id[rec_id].account_id,
        )
        for (rec_id, d, type_, currency), amount in totals.items()
    ]
    rows.sort(key=lambda r: (r.date, r.description))
    return rows
