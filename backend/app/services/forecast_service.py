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

Fork: the plan's repeating payments are recurring items with ``plan`` set
(``RecurringTransaction``), projected with the other recurring payments;
``link_plan_payments`` links their bank rows occurrence by occurrence.

Fork: the spending estimate follows the canonical Budgets: in a month
where a category has a budget, the category projects its budget instead of
its estimate, and a budget group's cap scales its members' estimates (see
``_budgeted_spending``).

Fork: an expected payment with ``transfer_account_id`` is a transfer between
own accounts, projected as a debit on ``account_id`` and a credit on the
to-account on the same date. Only the legs on accounts in scope count, so in
the all-accounts line the two cancel (and are left out); it is matched by
the debit leg of a bank transfer pair between the same two accounts.
"""
import calendar
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Iterable, Optional, cast

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.category import Category
from app.models.forecast import ForecastBudgetChoice, ForecastItem, ForecastScenario
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.forecast import (
    ForecastBudgetChoices,
    ForecastBudgetUse,
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


def _series_window(frequency: Optional[str]) -> int:
    """Fork: how far a bank row may be from a repeating payment's date. Less
    than half the gap between payments, so a row can't fit its neighbour."""
    return {"weekly": 3, "biweekly": 6}.get(frequency or "", MATCH_WINDOW_DAYS)


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

    Fork: then links the rows of the Forecast plan's recurring items
    (``link_plan_payments``), counted in the total.
    """
    found = await _find_expected_matches(session, workspace_id, transactions)
    for item, tx_id in found:
        item.matched_transaction_id = tx_id
    if found:
        await session.flush()
    return len(found) + await link_plan_payments(session, workspace_id, transactions)


async def pending_expected_matches(session: AsyncSession, workspace_id: uuid.UUID) -> set[uuid.UUID]:
    """Ids of expected payments whose bank row has arrived but isn't saved
    as a match yet. Read-only: a forecast or report leaves them out without
    writing, so viewing never changes data (a viewer can't write anyway)."""
    return {item.id for item, _ in await _find_expected_matches(session, workspace_id, None)}


async def _find_expected_matches(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: Optional[Iterable[Transaction]],
) -> list[tuple[ForecastItem, uuid.UUID]]:
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
                    ForecastItem.kind == "one_off",
                    ForecastItem.matched_transaction_id.is_(None),
                    ForecastItem.date.is_not(None),
                )
                .order_by(ForecastItem.date, ForecastItem.created_at)
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
    )
    found: list[tuple[ForecastItem, uuid.UUID]] = []
    for item in items:
        when = cast(date, item.date)
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
                            Transaction.date >= when - timedelta(days=MATCH_WINDOW_DAYS),
                            Transaction.date <= when + timedelta(days=MATCH_WINDOW_DAYS),
                        )
                    )
                ).scalars()
            )
        fits = [tx for tx in pool if tx.id not in taken and _is_match(item, tx, today)]
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
        found.append((item, best.id))
        taken.add(best.id)
    return found


async def link_plan_payments(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    transactions: Optional[Iterable[Transaction]] = None,
    *,
    recurring_ids: Optional[list[uuid.UUID]] = None,
) -> int:
    """Fork: link bank rows to the Forecast plan's recurring items
    (``RecurringTransaction.plan``), occurrence by occurrence, through
    ``Transaction.recurring_transaction_id``. Returns how many rows it linked.

    Each due occurrence (from the item's start to its end, up to today plus
    the window) with no row linked near it takes the best unlinked row on the
    item's account in the same direction, within the window (3 days for a
    weekly item, 6 fortnightly, 10 otherwise), exact in amount or within 5%
    with a word in common, as an expected payment is matched. A transfer
    (``transfer_account_id``) is settled only by the debit leg of a bank
    transfer pair into that account. A row stored before the item was created
    only settles an occurrence dated before then. The item's
    ``next_occurrence`` moves past the occurrences that arrived. Flushes,
    never commits; with ``transactions`` only those rows are candidates.
    """
    from app.services.recurring_match_service import carry_payee
    from app.services.recurring_transaction_service import _advance_date, plan_occurrences

    candidates = list(transactions) if transactions is not None else None
    if candidates is not None and not candidates:
        return 0
    today = app_today()
    stmt = select(RecurringTransaction).where(
        RecurringTransaction.workspace_id == workspace_id,
        RecurringTransaction.plan.is_(True),
        RecurringTransaction.account_id.is_not(None),
        RecurringTransaction.start_date <= today + timedelta(days=MATCH_WINDOW_DAYS),
    )
    if recurring_ids is not None:
        stmt = stmt.where(RecurringTransaction.id.in_(recurring_ids))
    linked = 0
    for rec in (await session.execute(stmt.order_by(RecurringTransaction.created_at))).scalars():
        window = _series_window(rec.frequency)
        last = min(rec.end_date or date.max, today + timedelta(days=window))
        dates = plan_occurrences(rec, rec.start_date, last)
        if not dates:
            continue
        arrived = sorted(
            (
                await session.execute(
                    select(Transaction.date).where(
                        Transaction.recurring_transaction_id == rec.id, Transaction.source != "recurring",
                    )
                )
            ).scalars()
        )
        due: list[date] = []
        for d in dates:
            near = next((a for a in arrived if abs((a - d).days) <= window), None)
            if near is None:
                due.append(d)
            else:
                arrived.remove(near)
        if not due:
            continue
        if candidates is not None:
            pool = candidates
        else:
            pool = list(
                (
                    await session.execute(
                        select(Transaction).where(
                            Transaction.workspace_id == workspace_id,
                            Transaction.account_id == rec.account_id,
                            Transaction.type == rec.type,
                            Transaction.recurring_transaction_id.is_(None),
                            Transaction.date >= due[0] - timedelta(days=window),
                            Transaction.date <= due[-1] + timedelta(days=window),
                        )
                    )
                ).scalars()
            )
        created = _utc(rec.created_at)
        settled: list[date] = []
        for occurrence in due:
            check_stored = created is None or occurrence >= created.date()
            fits = [
                tx for tx in pool
                if tx.recurring_transaction_id is None
                and _plan_row_fits(rec, tx, today, occurrence, window, check_stored, created)
            ]
            if rec.transfer_account_id is not None:
                to_account = rec.transfer_account_id
                fits = [tx for tx in fits if await _paired_into(session, tx, to_account)]
            if not fits:
                continue
            expected = abs(Decimal(rec.amount or 0))
            best = min(
                fits,
                key=lambda tx: (
                    -token_overlap(rec.description, tx.description),
                    abs((tx.date - occurrence).days),
                    abs(abs(Decimal(tx.amount)) - expected),
                ),
            )
            best.recurring_transaction_id = rec.id
            carry_payee(rec, best)
            settled.append(occurrence)
            linked += 1
        if settled:
            # Past the occurrences that arrived, so they don't project again.
            intended = rec.day_of_month or rec.start_date.day
            while rec.next_occurrence <= max(settled):
                rec.next_occurrence = _advance_date(rec.next_occurrence, rec.frequency, intended_day=intended)
            if rec.end_date and rec.next_occurrence > rec.end_date:
                rec.is_active = False
    if linked:
        await session.flush()
    return linked


def _plan_row_fits(
    rec: RecurringTransaction,
    tx: Transaction,
    today: date,
    when: date,
    window: int,
    check_stored: bool,
    created: Optional[datetime],
) -> bool:
    """Fork: whether ``tx`` is the bank row of plan item ``rec``'s payment on
    ``when`` (the expected-payment rules of ``_is_match``)."""
    if tx.account_id != rec.account_id or tx.type != rec.type:
        return False
    if rec.transfer_account_id is not None and tx.transfer_pair_id is None:
        return False
    stored = _utc(tx.created_at)
    if check_stored and (stored is None or (created is not None and stored < created)):
        return False
    if (tx.currency or "") != rec.currency:
        return False
    if tx.status != "posted" or tx.date > today or tx.source in _NOT_REAL_SOURCES:
        return False
    if abs((tx.date - when).days) > window:
        return False
    expected = abs(Decimal(rec.amount or 0))
    actual = abs(Decimal(tx.amount or 0))
    if actual == expected:
        return True
    return (
        abs(actual - expected) <= expected * MATCH_AMOUNT_TOLERANCE
        and token_overlap(rec.description, tx.description) > 0
    )


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

    Fork: the Forecast plan's repeating payments are recurring items
    (``RecurringTransaction.plan``), projected with the other recurring
    payments.
    """
    arrived = await pending_expected_matches(session, workspace_id)
    stmt = select(ForecastItem).where(
        ForecastItem.workspace_id == workspace_id,
        ForecastItem.scenario_id.is_(None),
        ForecastItem.kind == "one_off",
        ForecastItem.matched_transaction_id.is_(None),
        ForecastItem.date.is_not(None),
        ForecastItem.date <= end,
    )
    in_scope = _scope_test(account_ids, narrowed)
    tomorrow = today + timedelta(days=1)
    out: list[dict] = []
    for item in (await session.execute(stmt)).scalars():
        if item.id in arrived:
            continue
        for leg_type in _legs(item, in_scope):
            out.append({
                "date": max(cast(date, item.date), tomorrow),
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
    if values.get("scenario_id") is None and kind != "one_off":
        # Fork: a repeating one is a plan recurring item (the recurring API).
        raise ValueError("An expected payment is a one-off payment")
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
    if values.get("transfer_account_id") is not None and values.get("type") is None:
        values["type"] = "debit"  # Fork: a transfer leaves the from-account
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
        "amount", "date", "account_id", "transfer_account_id", "type", "currency",
    } & changes.keys():
        item.matched_transaction_id = None
        await session.flush()
        await match_expected_payments(session, workspace_id)
    await session.commit()
    await session.refresh(item)
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
                # Fork: where a budget sets the category's spending, the
                # estimate's change does not apply.
                if budgeted is None or not budgeted(d, item.category_id):
                    add(d, -daily)
    return deltas


def _month_starts(first: date, last: date) -> list[date]:
    out: list[date] = []
    m = first.replace(day=1)
    while m <= last:
        out.append(m)
        m = (m + timedelta(days=32)).replace(day=1)
    return out


@dataclass
class _BudgetUse:
    category_id: Optional[uuid.UUID]
    budget_group_id: Optional[uuid.UUID]
    month: date
    amount: float
    committed: float
    remainder: float


def _budgeted_spending(
    estimates: list[dict],
    budgets: list,
    groups: list,
    chosen_categories: set[uuid.UUID],
    chosen_groups: set[uuid.UUID],
    today: date,
    end: date,
    committed: dict[tuple[uuid.UUID, date], float],
    narrowed: bool,
) -> tuple[list[tuple[uuid.UUID, date, float]], Callable[[date, Optional[uuid.UUID]], bool], list[_BudgetUse]]:
    """Fork: the everyday spending per category and day after today, with the
    chosen canonical Budgets laid over the history-based estimate, month by
    month. A budget is the whole of what its category (or group) spends that
    month, so what recurring and expected payments categorised there already
    project (``committed``, per category and month start) counts toward it:

    - a chosen category budget replaces the category's estimate with
      max(0, budget - committed);
    - a chosen group's cap, less its members' chosen budgets and the
      committed payments of its other members, is shared by those other
      members in proportion to their estimates (evenly when those are all
      zero), never below nothing;
    - every other category keeps its estimate, and payments in categories
      outside chosen budgets stay as they are.

    Monthly amounts are spread evenly over the month's days still to come (in
    the current month a share of the budget by the days left), as
    ``_spread_monthly`` spreads the estimate. In an account view
    (``narrowed``) a budget applies only to the categories the view's
    estimate has: a budget names no account. Returns the days' amounts,
    whether a budget sets a category's spending on a day (a scenario's
    spending change leaves those days alone), and each chosen budget's use
    per month.
    """
    from app.services.budget_service import resolve_budgets

    first = today + timedelta(days=1)
    estimate = {e["category_id"]: float(e["monthly_amount"]) for e in estimates}

    def eligible(cat: uuid.UUID) -> bool:
        return cat in estimate or not narrowed

    out: list[tuple[uuid.UUID, date, float]] = []
    uses: list[_BudgetUse] = []
    budgeted: set[tuple[uuid.UUID, date]] = set()
    for month in _month_starts(first, end):
        days_in_month = calendar.monthrange(month.year, month.month)[1]
        lo = max(month, first)
        hi = min(month.replace(day=days_in_month), end)
        days = (hi - lo).days + 1
        if days <= 0:
            continue
        share = days / days_in_month
        monthly = {cat: amount * share for cat, amount in estimate.items()}
        own = {
            b.category_id: float(b.amount)
            for b in resolve_budgets(budgets, month).values()
            if b.category_id in chosen_categories and eligible(b.category_id)
        }
        for cat, amount in own.items():
            taken = committed.get((cat, month), 0.0)
            monthly[cat] = max(amount * share - taken, 0.0)
            budgeted.add((cat, month))
            uses.append(_BudgetUse(cat, None, month, amount, round(taken, 2), round(monthly[cat], 2)))
        for group in groups:
            if group.id not in chosen_groups or not group.applies_to(month):
                continue
            members = group.category_ids
            rest = [c for c in members if c not in own and eligible(c)]
            taken = sum(committed.get((c, month), 0.0) for c in rest)
            remainder = max(
                float(group.amount) * share - sum(own.get(c, 0.0) * share for c in members) - taken, 0.0
            )
            total = sum(estimate.get(c, 0.0) for c in rest)
            for c in rest:
                monthly[c] = remainder * estimate.get(c, 0.0) / total if total > 0 else remainder / len(rest)
                budgeted.add((c, month))
            uses.append(_BudgetUse(None, group.id, month, float(group.amount), round(taken, 2), round(remainder, 2)))
        for cat, amount in monthly.items():
            if amount <= 0:
                continue
            per_day = amount / days
            d = lo
            while d <= hi:
                out.append((cat, d, per_day))
                d += timedelta(days=1)

    def covered(d: date, category_id: Optional[uuid.UUID]) -> bool:
        return category_id is not None and (category_id, d.replace(day=1)) in budgeted

    return out, covered, uses


async def get_budget_choices(session: AsyncSession, workspace_id: uuid.UUID) -> ForecastBudgetChoices:
    """Fork: the budgets the workspace's forecast applies."""
    rows = (
        await session.execute(
            select(ForecastBudgetChoice).where(ForecastBudgetChoice.workspace_id == workspace_id)
            .order_by(ForecastBudgetChoice.created_at)
        )
    ).scalars().all()
    return ForecastBudgetChoices(
        category_ids=[r.category_id for r in rows if r.category_id is not None],
        budget_group_ids=[r.budget_group_id for r in rows if r.budget_group_id is not None],
    )


async def set_budget_choices(
    session: AsyncSession, workspace_id: uuid.UUID, data: ForecastBudgetChoices
) -> ForecastBudgetChoices:
    """Fork: replace the chosen budgets. Each must be the workspace's own: a
    category with a budget, or a budget group."""
    from app.models.budget import Budget
    from app.models.budget_group import BudgetGroup

    category_ids = list(dict.fromkeys(data.category_ids))
    group_ids = list(dict.fromkeys(data.budget_group_ids))
    if category_ids:
        found = set(
            (
                await session.execute(
                    select(Budget.category_id).where(
                        Budget.workspace_id == workspace_id, Budget.category_id.in_(category_ids)
                    )
                )
            ).scalars()
        )
        if found != set(category_ids):
            raise ValueError("Budget not found")
    if group_ids:
        found = set(
            (
                await session.execute(
                    select(BudgetGroup.id).where(BudgetGroup.workspace_id == workspace_id, BudgetGroup.id.in_(group_ids))
                )
            ).scalars()
        )
        if found != set(group_ids):
            raise ValueError("Budget group not found")
    await session.execute(delete(ForecastBudgetChoice).where(ForecastBudgetChoice.workspace_id == workspace_id))
    session.add_all(
        [ForecastBudgetChoice(workspace_id=workspace_id, category_id=c) for c in category_ids]
        + [ForecastBudgetChoice(workspace_id=workspace_id, budget_group_id=g) for g in group_ids]
    )
    await session.commit()
    return await get_budget_choices(session, workspace_id)


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
    # A recurring payment into the loan (a loan payment schedule, a plain
    # recurring transfer, or a plan item) stands in for the loan card's
    # repayments over the dates it covers, from its start to its end. Fork:
    # bounded, so the card's repayments resume after a schedule ends.
    covered_by: dict[uuid.UUID, list[tuple[date, Optional[date]]]] = {}
    for loan_id, start, last in (
        await session.execute(
            select(
                RecurringTransaction.transfer_account_id,
                RecurringTransaction.start_date,
                RecurringTransaction.end_date,
            ).where(
                RecurringTransaction.workspace_id == workspace_id,
                RecurringTransaction.is_active.is_(True),
                RecurringTransaction.transfer_account_id.in_([loan.account.id for loan in loans]),
            )
        )
    ).all():
        covered_by.setdefault(cast(uuid.UUID, loan_id), []).append((start, last))
    category_id = await find_default_category(session, workspace_id, LOAN_REPAYMENTS_KEY)
    out: list[dict] = []
    for loan in loans:
        if loan.base is None:
            continue
        covered = covered_by.get(loan.account.id, [])
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
    budget_choices: Optional[ForecastBudgetChoices] = None,
) -> ForecastResponse:
    """Base line and one line per scenario from today to ``months`` ahead.

    ``scenario_ids`` picks the scenarios; by default the enabled ones.

    Fork: ``account_ids`` is an account view: the line, its history, the
    spending estimate, recurring and expected payments and scenario changes
    keep to those of the cash-flow accounts. Rows with no account (invoices,
    repayments of loans no schedule pays, payments with no account) are left
    out of it, as Reports → Cash Flow leaves them out under a filter.

    Fork: ``budget_choices`` overrides the workspace's chosen budgets (see
    ``_budgeted_spending``); by default the saved choice.
    """
    from app.services.budget_service import load_budget_plan
    from app.services.dashboard_service import _get_recurring_projections
    from app.services.report_service import (
        _add_months,
        _get_category_spending_estimate,
        _make_to_primary,
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
    # Fork: the chosen canonical Budgets set the spending of the months they
    # cover, counting the recurring and expected payments already in them.
    if budget_choices is None:
        budget_choices = await get_budget_choices(session, workspace_id)
    chosen_categories = set(budget_choices.category_ids)
    chosen_groups = set(budget_choices.budget_group_ids)
    budgets, groups = await load_budget_plan(session, workspace_id)

    async def committed_in(ids: list[uuid.UUID], view: bool) -> dict[tuple[uuid.UUID, date], float]:
        committed: dict[tuple[uuid.UUID, date], float] = {}
        for proj in [
            *await _get_recurring_projections(
                session, workspace_id, today + timedelta(days=1), end + timedelta(days=1), ids,
                external_transfers=True,
            ),
            *await expected_payment_projections(session, workspace_id, today, end, ids, narrowed=view),
        ]:
            if proj["type"] == "debit" and proj["category_id"] is not None and today < proj["date"] <= end:
                key = (proj["category_id"], proj["date"].replace(day=1))
                committed[key] = committed.get(key, 0.0) + await to_primary(
                    Decimal(str(proj["amount"])), proj["currency"]
                )
        return committed

    if (chosen_categories or chosen_groups) and narrowed:
        # Fork: a budget is the household's and names no account, so an account
        # view works it out over every cash account and takes each category's
        # share of it by how much of that category's spending the view's
        # accounts carry (their estimate against the household's).
        all_ids = await cash_flow_account_ids(session, workspace_id)
        household = await _get_category_spending_estimate(session, workspace_id, today, to_primary, all_ids)
        spending, budgeted, uses = _budgeted_spending(
            household, budgets, groups, chosen_categories, chosen_groups, today, end,
            await committed_in(all_ids, False), False,
        )
        house_est = {e["category_id"]: float(e["monthly_amount"]) for e in household}
        view_est = {e["category_id"]: float(e["monthly_amount"]) for e in estimates}
        ratio = {c: (view_est.get(c, 0.0) / v if v > 0 else 0.0) for c, v in house_est.items()}
        spending = [(c, d, a * ratio.get(c, 0.0)) for c, d, a in spending if ratio.get(c, 0.0) > 0]
    else:
        committed = await committed_in(account_ids, narrowed) if (chosen_categories or chosen_groups) else {}
        spending, budgeted, uses = _budgeted_spending(
            estimates, budgets, groups, chosen_categories, chosen_groups, today, end, committed, narrowed
        )
    extra: list[dict] = []
    if include_spending:
        for category_id, d, amount in spending:
            extra.append({
                "date": d,
                "amount": amount,
                "currency": primary_currency,
                "type": "debit",
                "category_id": category_id,
            })
    budget_uses: list[ForecastBudgetUse] = []
    if include_spending and uses:
        rows = await session.execute(
            select(Category.id, Category.name).where(
                Category.id.in_([u.category_id for u in uses if u.category_id])
            )
        )
        names: dict[uuid.UUID, str] = {cat_id: name for cat_id, name in rows.all()}
        names.update({g.id: g.name for g in groups})
        budget_uses = [
            ForecastBudgetUse(
                category_id=u.category_id, budget_group_id=u.budget_group_id,
                name=names.get(u.category_id or u.budget_group_id, ""),
                month=u.month, amount=u.amount, committed=u.committed, remainder=u.remainder,
            )
            for u in uses
        ]

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
        budget_uses=budget_uses,
    )


def _recurring_payments(
    projections: list[dict], by_id: dict[uuid.UUID, RecurringTransaction],
) -> list[ForecastRecurringPayment]:
    """One row per schedule and date (a schedule split into legs is one
    payment), in date order."""
    totals: dict[tuple[uuid.UUID, date, str, str], float] = {}
    for p in projections:
        rec_id = p.get("recurring_transaction_id")
        # Fork: plan items are listed with the expected payments instead.
        if rec_id is None or rec_id not in by_id or by_id[rec_id].plan:
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
