"""Bank edits and removals of already-synced transactions.

Some providers (Akahu) keep a transaction's id when the bank later changes
its date, description or amount, delete it when the bank removes it, and,
for the rare row changed beyond recognition, delete it and create a new one
under a new id. Their sync must therefore handle additions, edits and
deletions, not only additions.

Ownership decides what a sync may touch. The bank owns a synced row's date,
amount (with its currency and conversion), type, status, raw data and bank
text (``original_description``); the user owns everything else: category,
payee, notes, the displayed description once edited or rule-managed, the
ignore and P&L flags, splits, attachments, transfer pairs, recurring links,
loan payments and invoice links. An edit rewrites only the bank's fields.

A removal deletes the row only when nothing the user built hangs off it.
A row with links that a delete would destroy (attachments, splits, a
transfer pair, a loan payment, an invoice allocation or deduction, a group
settlement) stays, tagged ``#removed-by-bank`` in its notes so the user can
find it with the tag filter and decide, and marked in ``raw_data``. When the
bank re-issued the row under a new id, the new row inherits the old row's
user fields and links, and the old row goes: one row, nothing lost.

Each provider opts in through ``BankProvider.edits_transactions_in_place``
and ``BankProvider.reports_removed_transactions``; others are unaffected.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional, cast

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.group_settlement import GroupSettlement
from app.models.invoice import InvoiceAllocation, InvoiceDeduction
from app.models.loan_payment import LoanPayment
from app.models.reconciliation import ReconciliationEvent, ReconciliationSuggestion
from app.models.transaction import Transaction
from app.models.transaction_attachment import TransactionAttachment
from app.models.transaction_split import TransactionSplit
from app.schemas.transaction_split import ShareType, TransactionSplitInput, TransactionSplitsInput
from app.services.credit_card_service import apply_effective_date
from app.services.fx_rate_service import stamp_primary_amount
from app.services.split_service import _materialize

logger = logging.getLogger(__name__)

# Tag added to the notes of a row the bank removed but that can't be deleted
# without losing what the user attached to it. The transactions list filters
# on it like any other tag.
REMOVED_TAG = "#removed-by-bank"
# raw_data key holding the day the removal was noticed. A later sync that
# sees the row again replaces raw_data, which clears it.
REMOVED_MARKER = "_securo_removed_by_provider"
# Banks move a row's date by a day or two after posting. A stored row this
# close to the start of the fetched window may have moved just outside it,
# so only rows further in count as missing.
REMOVAL_EDGE_DAYS = 3
# A row re-issued under a new id keeps its account, type and amount; its
# date may move by a few days.
REIDENTIFY_DAYS = 3
# Mass-removal guard: a complete read that would remove more than this share
# of the window's stored rows (and more than MASS_REMOVAL_FLOOR rows) looks
# like a provider fault, not the bank, so nothing is removed that sync.
MASS_REMOVAL_SHARE = Decimal("0.2")
MASS_REMOVAL_FLOOR = 2

_NOTES_MAX = 1000


@dataclass
class RemovalOutcome:
    deleted: int = 0
    reidentified: int = 0
    flagged: int = 0
    skipped: Optional[str] = None
    flagged_ids: list[uuid.UUID] = field(default_factory=list)


# ----- edits ----------------------------------------------------------------


def _has_user_description(tx: Transaction) -> bool:
    """The displayed text is the user's or a rule's, not the bank's."""
    return bool(tx.description_is_rule_managed) or (
        tx.original_description is not None and tx.description != tx.original_description
    )


def _remove_tag(notes: Optional[str], tag: str) -> Optional[str]:
    if not notes or tag not in notes:
        return notes
    # Same boundaries as transaction_service.bulk_remove_tags.
    pattern = r"(?:(?<=^)|(?<=[^\wÀ-ž-]))" + re.escape(tag) + r"(?=$|[^\wÀ-ž-])"
    updated = re.sub(r"\s{2,}", " ", re.sub(pattern, "", notes)).strip()
    return updated or None


def clear_removed_flag(tx: Transaction) -> bool:
    """Undo a removal flag on a row the provider returns again."""
    cleared = False
    if isinstance(tx.raw_data, dict) and REMOVED_MARKER in tx.raw_data:
        tx.raw_data = {k: v for k, v in tx.raw_data.items() if k != REMOVED_MARKER}
        cleared = True
    if tx.notes and REMOVED_TAG in tx.notes:
        stripped = _remove_tag(tx.notes, REMOVED_TAG)
        if stripped != tx.notes:
            tx.notes = stripped
            cleared = True
    if cleared:
        logger.info("Sync: transaction %s is back at the provider; removal flag cleared", tx.id)
    return cleared


async def _rescale_splits(session: AsyncSession, tx: Transaction) -> None:
    """Re-materialize equal/percent splits against a changed amount.

    Exact splits are the user's own numbers and can't be re-derived; they
    are left alone and logged, as the user has to re-split the row.
    """
    splits = list(
        (
            await session.execute(
                select(TransactionSplit)
                .where(TransactionSplit.transaction_id == tx.id)
                .order_by(TransactionSplit.created_at, TransactionSplit.id)
            )
        )
        .scalars()
        .all()
    )
    if not splits:
        return
    share_types = {s.share_type for s in splits}
    if len(share_types) != 1 or share_types & {"exact"}:
        logger.warning(
            "Sync: bank changed the amount of split transaction %s; its exact "
            "splits no longer add up and need re-splitting",
            tx.id,
        )
        return
    share_type = share_types.pop()
    payload = TransactionSplitsInput(
        share_type=cast(ShareType, share_type),
        splits=[
            TransactionSplitInput(group_member_id=s.group_member_id, share_pct=s.share_pct)
            for s in splits
        ],
    )
    try:
        materialized = _materialize(tx.amount, payload)
    except ValueError:
        logger.warning("Sync: could not re-split transaction %s after a bank amount change", tx.id)
        return
    for split, (_, share_amount, _) in zip(splits, materialized):
        split.share_amount = share_amount


async def _follow_pair(
    session: AsyncSession,
    user_id: uuid.UUID,
    tx: Transaction,
    *,
    amount_changed: bool,
    date_changed: bool,
) -> None:
    """Keep a transfer pair consistent after a bank edit of one leg.

    Pairs don't require equal amounts (``link_existing_as_transfer`` is
    permissive), so an edit never breaks the link. A counterpart Securo wrote
    to mirror this row (source ``transfer``, same currency, amounts not typed
    by the user) follows it, as it does when the user edits the row. Any
    other counterpart is another statement line with its own source of truth
    (its bank corrects it in its own feed), so it is left alone and logged.
    """
    counterpart = (
        await session.execute(
            select(Transaction).where(
                Transaction.transfer_pair_id == tx.transfer_pair_id,
                Transaction.id != tx.id,
            )
        )
    ).scalars().first()
    if counterpart is None:
        return
    mirrors = (
        counterpart.source == "transfer"
        and counterpart.currency == tx.currency
        and not (tx.transfer_amount_explicit or counterpart.transfer_amount_explicit)
    )
    if not mirrors:
        if amount_changed and counterpart.amount != tx.amount:
            logger.warning(
                "Sync: bank changed the amount of transfer leg %s; its counterpart %s "
                "keeps its own amount (pair left linked)",
                tx.id,
                counterpart.id,
            )
        return
    if amount_changed:
        counterpart.amount = tx.amount
    if date_changed:
        counterpart.date = tx.date
        apply_effective_date(counterpart, await session.get(Account, counterpart.account_id))
    await stamp_primary_amount(session, user_id, counterpart)
    logger.info("Sync: mirrored transfer leg %s follows bank edit of %s", counterpart.id, tx.id)


async def apply_provider_edit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    account: Account,
    tx: Transaction,
    txn_data,
    incoming_currency: str,
    account_currency: str,
    user_currency: str,
    bill_due_date: Optional[date] = None,
) -> list[str]:
    """Bring a synced row's bank-owned fields up to the provider's copy.

    Returns the names of the fields that changed. The displayed description
    follows the bank only while it still is the bank's text; a user's or a
    rule's description is kept and only ``original_description`` moves.
    """
    changed: list[str] = []

    new_text = txn_data.description
    if tx.original_description is not None and new_text != tx.original_description:
        if not _has_user_description(tx):
            tx.description = new_text
        tx.original_description = new_text
        changed.append("description")

    date_changed = tx.date != txn_data.date
    if date_changed:
        tx.date = txn_data.date
        changed.append("date")

    amount_changed = Decimal(str(tx.amount)) != Decimal(str(txn_data.amount))
    if amount_changed:
        tx.amount = txn_data.amount
        changed.append("amount")
    if tx.type != txn_data.type:
        tx.type = txn_data.type
        changed.append("type")
    currency_changed = tx.currency != incoming_currency
    if currency_changed:
        tx.currency = incoming_currency
        changed.append("currency")
    if txn_data.status and tx.status != txn_data.status:
        tx.status = txn_data.status
        changed.append("status")
    if txn_data.raw_data is not None and tx.raw_data != txn_data.raw_data:
        # Replace, not merge: a key the provider dropped is gone.
        tx.raw_data = txn_data.raw_data

    bank_conversion = (
        txn_data.amount_in_account_currency is not None
        and txn_data.amount
        and account_currency == user_currency
        and txn_data.currency != account_currency
    )
    if bank_conversion:
        primary = txn_data.amount_in_account_currency
        if tx.amount_primary is None or Decimal(str(tx.amount_primary)) != Decimal(str(primary)):
            tx.amount_primary = primary
            tx.fx_rate_used = primary / txn_data.amount
            if "amount" not in changed:
                changed.append("amount_primary")
    elif amount_changed or currency_changed or date_changed:
        await stamp_primary_amount(session, user_id, tx)

    if date_changed:
        # A row whose bill isn't known any more keeps its stored cycle date.
        if tx.bill_id is None or bill_due_date is not None:
            apply_effective_date(tx, account, bill_due_date=bill_due_date)

    if amount_changed:
        await _rescale_splits(session, tx)
        has_loan_payment = await session.scalar(
            select(
                exists().where(
                    or_(
                        LoanPayment.cash_transaction_id == tx.id,
                        LoanPayment.loan_transaction_id == tx.id,
                        LoanPayment.interest_transaction_id == tx.id,
                    )
                )
            )
        )
        if has_loan_payment:
            # A loan payment stores no amounts; its totals follow the rows.
            logger.info("Sync: bank changed the amount of loan-payment row %s", tx.id)
    if tx.transfer_pair_id and (amount_changed or date_changed):
        await _follow_pair(
            session, user_id, tx, amount_changed=amount_changed, date_changed=date_changed
        )

    if changed:
        logger.info("Sync: bank edited transaction %s (%s)", tx.id, ", ".join(changed))
    return changed


# ----- removals -------------------------------------------------------------


async def _lossy_links(session: AsyncSession, tx: Transaction) -> list[str]:
    """What deleting this row would destroy that the user made."""
    reasons: list[str] = []
    if tx.transfer_pair_id is not None:
        reasons.append("transfer pair")
    checks = (
        ("attachment", exists().where(TransactionAttachment.transaction_id == tx.id)),
        ("split", exists().where(TransactionSplit.transaction_id == tx.id)),
        ("invoice allocation", exists().where(InvoiceAllocation.transaction_id == tx.id)),
        ("invoice deduction", exists().where(InvoiceDeduction.transaction_id == tx.id)),
        (
            "loan payment",
            exists().where(
                or_(
                    LoanPayment.cash_transaction_id == tx.id,
                    LoanPayment.loan_transaction_id == tx.id,
                    LoanPayment.interest_transaction_id == tx.id,
                )
            ),
        ),
        (
            "group settlement",
            exists().where(
                or_(
                    GroupSettlement.transaction_id == tx.id,
                    GroupSettlement.receiver_transaction_id == tx.id,
                )
            ),
        ),
    )
    for label, clause in checks:
        if await session.scalar(select(clause)):
            reasons.append(label)
    return reasons


async def _delete_synced_row(session: AsyncSession, tx: Transaction) -> None:
    """Delete a synced row the bank removed.

    Only rows without user-made links get here (or rows whose links were
    just moved to their re-issued twin), so the transaction service's delete
    (which also drops a paired leg and attachment files) has nothing extra
    to do. The suggestion queue's questions about the row go with it and the
    matching history keeps its record, as the database's ON DELETE rules do.
    """
    await session.execute(
        delete(ReconciliationSuggestion)
        .where(ReconciliationSuggestion.transaction_id == tx.id)
        .execution_options(synchronize_session=False)
    )
    await session.execute(
        update(ReconciliationEvent)
        .where(ReconciliationEvent.transaction_id == tx.id)
        .values(transaction_id=None)
        .execution_options(synchronize_session=False)
    )
    session.expire(tx, ["attachments", "splits"])
    await session.delete(tx)


def _flag_removed(tx: Transaction, today: date) -> bool:
    """Mark a removed row that can't be deleted. True when newly flagged."""
    raw = tx.raw_data if isinstance(tx.raw_data, dict) else {}
    if REMOVED_MARKER in raw:
        return False
    tx.raw_data = {**raw, REMOVED_MARKER: today.isoformat()}
    notes = (tx.notes or "").strip()
    if REMOVED_TAG not in notes:
        tagged = f"{notes} {REMOVED_TAG}".strip()
        if len(tagged) <= _NOTES_MAX:
            tx.notes = tagged
    return True


def _same_charge(old: Transaction, new: Transaction) -> bool:
    return (
        old.type == new.type
        and old.currency == new.currency
        and Decimal(str(old.amount)) == Decimal(str(new.amount))
        and abs((old.date - new.date).days) <= REIDENTIFY_DAYS
        and new.transfer_pair_id is None
    )


async def _carry_over(session: AsyncSession, old: Transaction, new: Transaction) -> None:
    """Give a re-issued row the old row's user fields and links."""
    if old.category_id is not None:
        new.category_id = old.category_id
    if old.payee_id is not None:
        new.payee_id = old.payee_id
    if old.payee:
        new.payee = old.payee
    if old.notes:
        new.notes = old.notes
    if (
        not old.description_is_rule_managed
        and old.original_description is not None
        and old.description != old.original_description
    ):
        # The user renamed it; the new row's bank text stays as provenance.
        new.description = old.description
        new.description_is_rule_managed = False
    new.is_ignored = bool(new.is_ignored or old.is_ignored)
    new.exclude_from_pnl = bool(new.exclude_from_pnl or old.exclude_from_pnl)
    if old.recurring_transaction_id is not None:
        if new.recurring_transaction_id is None:
            new.recurring_transaction_id = old.recurring_transaction_id
        elif new.recurring_transaction_id != old.recurring_transaction_id:
            logger.info(
                "Sync: re-issued row %s keeps its own recurring link, not %s's", new.id, old.id
            )
    if old.effective_bill_date is not None and new.effective_bill_date is None:
        new.effective_bill_date = old.effective_bill_date
        new.effective_date = old.effective_bill_date
    if old.transfer_pair_id is not None and new.transfer_pair_id is None:
        new.transfer_pair_id = old.transfer_pair_id
        new.transfer_amount_explicit = old.transfer_amount_explicit
        old.transfer_pair_id = None

    repoint = (
        (TransactionAttachment, TransactionAttachment.transaction_id),
        (TransactionSplit, TransactionSplit.transaction_id),
        (InvoiceAllocation, InvoiceAllocation.transaction_id),
        (InvoiceDeduction, InvoiceDeduction.transaction_id),
        (LoanPayment, LoanPayment.cash_transaction_id),
        (LoanPayment, LoanPayment.loan_transaction_id),
        (LoanPayment, LoanPayment.interest_transaction_id),
        (GroupSettlement, GroupSettlement.transaction_id),
        (GroupSettlement, GroupSettlement.receiver_transaction_id),
        (ReconciliationSuggestion, ReconciliationSuggestion.transaction_id),
        (ReconciliationEvent, ReconciliationEvent.transaction_id),
    )
    await session.flush()
    for model, column in repoint:
        await session.execute(
            update(model)
            .where(column == old.id)
            .values({column.key: new.id})
            .execution_options(synchronize_session=False)
        )
    session.expire(new, ["attachments", "splits"])


async def reconcile_removed_transactions(
    session: AsyncSession,
    *,
    account: Account,
    window: tuple[date, date],
    returned_external_ids: set[str],
    new_rows: list[Transaction],
) -> RemovalOutcome:
    """Remove stored rows a complete provider read no longer returns.

    ``window`` is the (first, last) day the provider read completely for
    this account; ``returned_external_ids`` every id that read returned;
    ``new_rows`` the rows this sync inserted for the account. Only synced
    rows with a provider id, dated strictly inside the window (more than
    ``REMOVAL_EDGE_DAYS`` after its first day and before its last), count.
    """
    outcome = RemovalOutcome()
    if not returned_external_ids:
        # A complete read returning nothing at all is far likelier a provider
        # fault than a bank deleting a whole window.
        outcome.skipped = "empty read"
        return outcome

    first, last = window
    lo = first + timedelta(days=REMOVAL_EDGE_DAYS)
    stored = list(
        (
            await session.execute(
                select(Transaction).where(
                    Transaction.account_id == account.id,
                    Transaction.source == "sync",
                    Transaction.external_id.is_not(None),
                    ~Transaction.external_id.startswith("bill_charge:"),
                    Transaction.date >= lo,
                    Transaction.date < last,
                )
            )
        )
        .scalars()
        .all()
    )
    new_ids = {row.id for row in new_rows}
    existing = [row for row in stored if row.id not in new_ids]
    missing = [row for row in existing if row.external_id not in returned_external_ids]
    if not missing:
        return outcome

    # Re-issued rows: one missing row and one new row of the same charge,
    # each the other's only candidate. Ambiguity means no carry-over.
    candidates = {old.id: [new for new in new_rows if _same_charge(old, new)] for old in missing}
    claims: dict[uuid.UUID, int] = {}
    for found in candidates.values():
        for new in found:
            claims[new.id] = claims.get(new.id, 0) + 1
    pairs = [
        (old, candidates[old.id][0])
        for old in missing
        if len(candidates[old.id]) == 1 and claims[candidates[old.id][0].id] == 1
    ]
    reissued = {old.id for old, _ in pairs}
    removed = [old for old in missing if old.id not in reissued]

    for old, new in pairs:
        await _carry_over(session, old, new)
        logger.info(
            "Sync: transaction %s was re-issued by the provider as %s; user data moved over",
            old.id,
            new.id,
        )
        await _delete_synced_row(session, old)
        outcome.reidentified += 1

    if not removed:
        return outcome
    if (
        len(removed) > MASS_REMOVAL_FLOOR
        and Decimal(len(removed)) > MASS_REMOVAL_SHARE * Decimal(len(existing))
    ):
        logger.warning(
            "Sync: provider no longer returns %d of %d stored transactions in account %s's "
            "window; too many to trust, nothing removed this sync",
            len(removed),
            len(existing),
            account.id,
        )
        outcome.skipped = "mass removal"
        return outcome

    today = last
    for old in removed:
        reasons = await _lossy_links(session, old)
        if reasons:
            if _flag_removed(old, today):
                logger.warning(
                    "Sync: provider removed transaction %s, kept and tagged %s because it has: %s",
                    old.id,
                    REMOVED_TAG,
                    ", ".join(reasons),
                )
            outcome.flagged += 1
            outcome.flagged_ids.append(old.id)
            continue
        logger.info("Sync: provider removed transaction %s; deleted", old.id)
        await _delete_synced_row(session, old)
        outcome.deleted += 1
    return outcome
