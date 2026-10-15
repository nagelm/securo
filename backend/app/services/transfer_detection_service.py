"""Pairing the two legs of one transfer.

Money leaving one account and arriving in another is one event recorded
twice, and until the two rows know about each other the books are wrong
in a specific way: the same money counts once as an expense and once as
income, and every total that separates the two is overstated at both
ends.

## What changed, and what did not

This ran for a long time as a self-contained heuristic: same amount,
different account, within two days, link them. The signals were right.
What was wrong was that they were **constants nobody could see**, and
three reports say so in three different ways:

  - It never read the description, so two transfers of the same amount
    leaving one account on the same day were told apart by whichever row
    the database returned first, even when one line plainly named its
    destination.
  - It could only link, never ask, so a reimbursement that happened to
    match a purchase was silently removed from income and from the card's
    bill with nothing to review.
  - It could not be turned off.

So the rules moved into `reconciliation_policy` where a workspace can
read and change them, and this module became what is left: load the
candidates, ask the engine, write down what it decided. The engine holds
no session and decides without writing, which is what makes the rule
below possible at all.

## Both sides have to agree

The rule this module exists to enforce: **a pair is linked only when each
leg is the other's single best answer.**

The old code asked in one direction. It walked the debits, and for each
one took the nearest unpaired credit of the same amount. From a debit's
point of view a lone matching credit is unambiguous, so it linked, even
when a second debit was sitting right beside it with an equal claim.
That is the whole of the wrong-counterpart report, and asking the credit
the same question is the whole of the answer: it has two candidates, it
cannot choose, and the pair stops being certain.

Non-mutual pairs are not dropped. They become suggestions, which is the
honest description of them: two rows that look like a transfer, and a
reason we will not say so on our own.
"""
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.loan_payment import LoanPayment
from app.models.transaction import Transaction
from app.services import reconciliation_policy
from app.services import reconciliation_rule_service as rule_service
from app.services import reconciliation_suggestion_service as suggestions
from app.services.transfer_pair_category import categorise_pair_legs
from app.services.reconciliation_engine import (
    Decision,
    Expectation,
    Movement,
    evaluate,
)

NODE = reconciliation_policy.MATCH_TRANSFER["node"]

#: The opposite of what a leg did, which is what the other leg must do.
_OPPOSITE = {"debit": "credit", "credit": "debit"}


def _enabled(policy: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in policy.get("strategies", []) if s.get("enabled", True)]


def _widest_window(policy: dict[str, Any]) -> int:
    """The largest number of days any enabled rule reaches.

    A prefilter, never a decision. Nothing outside the widest window can
    satisfy any rule, so narrowing to it changes no outcome and keeps a
    workspace with years of unpaired rows from being compared against all
    of them.
    """
    days = 0
    for strategy in _enabled(policy):
        window = strategy.get("when", {}).get("date", {})
        days = max(
            days, int(window.get("before_days", 0)), int(window.get("after_days", 0))
        )
    return days


def _widest_amount_ratio(policy: dict[str, Any]) -> Optional[Decimal]:
    """How far from the figure any enabled rule is willing to look.

    `None` means no useful prefilter: a rule matching a part payment or a
    set has no band around the amount, so every candidate in the window
    has to reach the engine. Zero means every enabled rule wants the
    exact figure, which is the common case and the one worth being fast
    at.
    """
    worst = Decimal("0")
    for strategy in _enabled(policy):
        amount = strategy.get("when", {}).get("amount", {})
        match = amount.get("match", "exact")
        if match == "tolerance":
            worst = max(worst, Decimal(str(amount.get("percent", "0"))) / Decimal("100"))
        elif match != "exact":
            return None
    return worst


def _as_movement(tx: Transaction, account: Optional[Account]) -> Movement:
    return Movement(
        amount=abs(Decimal(tx.amount)),
        currency=tx.currency,
        direction=tx.type,
        when=tx.date,
        description=tx.description,
        counterparty=tx.payee or tx.original_description,
        payee_id=tx.payee_id,
        account_id=tx.account_id,
        account_name=account.name if account else None,
        account_type=account.type if account else None,
        source=tx.source,
    )


def _as_expectation(tx: Transaction, account: Optional[Account]) -> Expectation:
    """This leg, seen as the promise it makes about the other one.

    A debit of 100 is the expectation that a credit of 100 lands
    somewhere else, so the direction is flipped on the way in. That is
    what lets the engine's ordinary direction check keep meaning what it
    says: it compares a movement against what was expected, and here what
    is expected is the mirror of what this row did.
    """
    return Expectation(
        kind="transaction",
        id=tx.id,
        amount=abs(Decimal(tx.amount)),
        currency=tx.currency,
        direction=_OPPOSITE.get(tx.type, tx.type),
        when=tx.date,
        description=tx.description,
        counterparty=tx.payee or tx.original_description,
        payee_id=tx.payee_id,
        account_id=tx.account_id,
        account_name=account.name if account else None,
        account_type=account.type if account else None,
    )


def _excluded_leg(
    tx: Transaction, account: Optional[Account], rules_: list[dict[str, Any]]
) -> bool:
    """Is this row the kind of thing that is never half of a transfer?

    Asked once, before any rule sees it, and asked of **both sides**.
    That symmetry is the point: a card purchase has to disappear whether
    it is the row being examined or the row being offered as a
    counterpart, and a check that ran in only one direction would let it
    back in from the other.

    A veto rather than a rule, because there is nothing here to weigh. A
    debit on a credit card is money that went to a shop; it has no
    second leg to find, so the number of candidates is zero and a rule
    that got to look would only be choosing between wrong answers.
    """
    for rule in rules_:
        kinds = rule.get("account_types")
        if kinds and (account is None or account.type not in kinds):
            continue
        direction = rule.get("direction")
        if direction and tx.type != direction:
            continue
        return True
    return False


async def _pool(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    ignored_sources: set[str],
    excluded: list[dict[str, Any]],
    accounts: dict[uuid.UUID, Account],
) -> list[Transaction]:
    """Every row still looking for its other half."""
    query = select(Transaction).where(
        Transaction.workspace_id == workspace_id,
        Transaction.transfer_pair_id.is_(None),
        # An ignored row is out of every total, so it is nobody's other half:
        # a duplicate copy left in the pool would make its real twin ambiguous.
        Transaction.is_ignored.is_(False),
    )
    if ignored_sources:
        query = query.where(Transaction.source.not_in(ignored_sources))
    result = await session.execute(query)
    return [
        tx
        for tx in result.scalars().all()
        if not _excluded_leg(tx, accounts.get(tx.account_id), excluded)
    ]


async def detect_transfer_pairs(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    candidate_ids: Optional[list[uuid.UUID]] = None,
) -> int:
    """Link the transfers this workspace's rules are sure about.

    `candidate_ids` narrows the work to rows that just arrived, and
    carries one rule of its own: **a pair may only form when at least one
    leg is new.** Without it a sync would keep reconsidering years of
    unpaired history and pairing two old rows on the strength of a rule
    somebody edited this morning, which is a change nobody asked for
    arriving at a moment nobody is watching.

    Rows outside `candidate_ids` are still *examined*, because that is
    the only way to ask whether they have a competing claim. They are
    simply never paired with each other.

    Returns the number of pairs created.
    """
    policy = await rule_service.resolve(session, workspace_id, NODE)
    strategies = _enabled(policy)
    if not strategies:
        # Every rule turned off is a supported answer, and the one the
        # old code could not give: this workspace pairs its transfers by
        # hand.
        return 0

    scope = policy.get("scope", {})
    # Loaded before the pool, because what counts as a leg at all depends
    # on the kind of account it sits on.
    accounts = {
        account.id: account
        for account in (
            await session.execute(
                select(Account).where(Account.workspace_id == workspace_id)
            )
        ).scalars()
    }

    ignored = set(scope.get("ignore_transaction_sources", []))
    pool = await _pool(
        session, workspace_id, ignored, scope.get("exclude_legs", []), accounts
    )
    if len(pool) < 2:
        return 0

    window = _widest_window(policy)
    ratio = _widest_amount_ratio(policy)
    by_date: dict[Any, list[Transaction]] = defaultdict(list)
    for tx in pool:
        by_date[tx.date].append(tx)

    def neighbours(tx: Transaction) -> list[Transaction]:
        """Rows close enough in time and size to be worth asking about."""
        found: list[Transaction] = []
        for offset in range(-window, window + 1):
            for other in by_date.get(tx.date + timedelta(days=offset), ()):
                if other.id == tx.id or other.account_id == tx.account_id:
                    continue
                if other.type != _OPPOSITE.get(tx.type):
                    continue
                if ratio is not None:
                    moved = abs(Decimal(tx.amount))
                    theirs = abs(Decimal(other.amount))
                    if abs(moved - theirs) > (theirs * ratio):
                        continue
                found.append(other)
        return found

    # **An empty list is not the same as no list.** `None` means "look at
    # everything"; `[]` means "a sync just ran and brought nothing new",
    # which is the one case where there is certainly nothing to do.
    # Collapsing the two sent a quiet sync off to reconsider the whole
    # workspace and pair two rows that had both been sitting there for
    # months, which is exactly what restricting to new rows prevents.
    new_ids = None if candidate_ids is None else set(candidate_ids)

    # Which rows to ask about. Everything when this is a full run;
    # otherwise the new rows plus whatever could contest them, because a
    # claim you never asked about is a claim you cannot weigh.
    if new_ids is None:
        examined = pool
    else:
        relevant: dict[uuid.UUID, Transaction] = {}
        for tx in pool:
            if tx.id in new_ids:
                relevant[tx.id] = tx
                for other in neighbours(tx):
                    relevant.setdefault(other.id, other)
        examined = list(relevant.values())

    decisions: dict[uuid.UUID, tuple[Decision, Movement, Transaction]] = {}
    for tx in examined:
        candidates = [
            _as_expectation(other, accounts.get(other.account_id))
            for other in neighbours(tx)
        ]
        if not candidates:
            continue
        movement = _as_movement(tx, accounts.get(tx.account_id))
        decision = evaluate(movement, candidates, policy, trigger="money_arrives")
        if decision.port == "unmatched" or decision.expectation is None:
            continue
        decisions[tx.id] = (decision, movement, tx)

    def involves_new(a: uuid.UUID, b: uuid.UUID) -> bool:
        return new_ids is None or a in new_ids or b in new_ids

    # Pass one: the pairs both sides agree on.
    paired: set[uuid.UUID] = set()
    pairs_created = 0
    for tx_id, (decision, _movement, tx) in decisions.items():
        expectation = decision.expectation
        if decision.port != "linked" or tx_id in paired or expectation is None:
            continue
        other_id = expectation.id
        if other_id in paired or not involves_new(tx_id, other_id):
            continue
        mirror = decisions.get(other_id)
        if mirror is None:
            continue
        other_decision, _m, other_tx = mirror
        if other_decision.port != "linked" or other_decision.expectation is None:
            continue
        if other_decision.expectation.id != tx_id:
            # Each side named somebody else. Neither is wrong on its own
            # terms; together they are two claims on one row, and the
            # queue is where that gets settled.
            continue

        pair_id = uuid.uuid4()
        tx.transfer_pair_id = pair_id
        other_tx.transfer_pair_id = pair_id
        await categorise_pair_legs(session, workspace_id, (tx, other_tx))
        paired.update({tx_id, other_id})
        pairs_created += 1

    # Questions already open about rows that just paired can only fail now.
    await suggestions.expire_transfer_questions(session, workspace_id, paired)

    # Pass two: everything that looked like a transfer and was not certain.
    # Keyed on the unordered pair, because the same two rows reach here
    # from both ends and a person should be asked once.
    asked: set[frozenset[uuid.UUID]] = set()
    for tx_id, (decision, movement, _tx) in decisions.items():
        expectation = decision.expectation
        if tx_id in paired or expectation is None:
            continue
        other_id = expectation.id
        if other_id in paired or not involves_new(tx_id, other_id):
            continue
        key = frozenset({tx_id, other_id})
        if key in asked:
            continue
        asked.add(key)

        if decision.port == "linked":
            # Wanted to link and could not: the other leg named somebody
            # else, or never answered. Asking is what is left, and it is
            # the truthful version of what the old code did silently.
            decision.port = "suggested"
        await suggestions.record(
            session, workspace_id, tx_id, decision, movement, NODE
        )

    # Fork: pairs made in this pass can settle a row that was ambiguous only
    # because of them (two identical payments on one day, one of which just
    # paired on a stronger rule). Look at those rows again, as
    # recheck_after_link does after a link made by hand; a new pair also
    # retires the question just asked about it.
    if paired:
        freed = [
            tx_id
            for tx_id, (_decision, _movement, tx) in decisions.items()
            if tx_id not in paired
            and tx.transfer_pair_id is None
            and (new_ids is None or tx_id in new_ids)
            and any(other.id in paired for other in neighbours(tx))
        ]
        if freed:
            pairs_created += await detect_transfer_pairs(session, workspace_id, candidate_ids=freed)

    return pairs_created


async def recheck_after_link(
    session: AsyncSession, workspace_id: uuid.UUID, leg_ids: set[uuid.UUID], *, keep: frozenset[uuid.UUID] = frozenset()
) -> int:
    """After two rows become a transfer: retire the questions that offered
    either of them, and look again at the rows those questions offered.

    A row that just lost a competing claim may now have a single best
    answer (two identical payments on one day, one of them linked by hand,
    leave the other with exactly one candidate), so it links or becomes a
    fresh question. Returns the number of new pairs.
    """
    freed = await suggestions.expire_transfer_questions(session, workspace_id, leg_ids, keep=keep)
    if not freed:
        return 0
    return await detect_transfer_pairs(session, workspace_id, candidate_ids=list(freed))


class TransferPairLocked(Exception):
    """The pair is a recorded loan payment and can't be unlinked (409)."""


async def unlink_transfer_pair(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    pair_id: uuid.UUID,
) -> int:
    """Remove a transfer pair link. Returns number of transactions unlinked.

    Refused with `TransferPairLocked` when either leg belongs to a recorded
    loan payment: unpairing would turn the principal into an unpaired,
    possibly uncategorised debit that counts as an expense. Deleting the
    loan payment is the way to undo it.
    """
    result = await session.execute(
        select(Transaction).where(
            Transaction.workspace_id == workspace_id,
            Transaction.transfer_pair_id == pair_id,
        )
    )
    transactions = list(result.scalars().all())

    leg_ids = [tx.id for tx in transactions]
    if leg_ids:
        locked = (
            await session.execute(
                select(LoanPayment.id)
                .where(
                    or_(
                        LoanPayment.cash_transaction_id.in_(leg_ids),
                        LoanPayment.loan_transaction_id.in_(leg_ids),
                    )
                )
                .limit(1)
            )
        ).first()
        if locked is not None:
            raise TransferPairLocked(
                "This transfer is a recorded loan payment; delete the loan payment instead"
            )

    for tx in transactions:
        tx.transfer_pair_id = None

    return len(transactions)
