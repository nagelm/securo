"""Loan payments: a transfer pair plus an optional interest row, one commit.

A payment of 2,000 with 1,200 interest writes
  1. a debit of 2,000 on the paying account (or pairs a debit that already
     exists there),
  2. a credit of 2,000 on the loan, paired with 1 (`transfer_pair_id`),
     both in Loan repayments (else Transfers) unless the bank's row already
     has a category of its own,
  3. a debit of 1,200 on the loan in the user's interest category,
and one `loan_payments` row linking them. The loan moves by the 800 of
principal; P&L moves by the 1,200 of interest only, because every P&L filter
drops paired rows (and credits on loans). Principal is never stored as an
amount: read endpoints derive it from the rows.

Nothing here commits before every check has passed, and a payment commits
once: an error part-way leaves the session uncommitted, and the request's
session is closed without committing, so nothing is left behind.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.category import Category
from app.models.loan_payment import LoanPayment
from app.models.transaction import Transaction
from app.schemas.loan_payment import LoanPaymentCreate, LoanPaymentRead, LoanPaymentUpdate
from app.services import account_service
from app.services._query_filters import counts_as_user_pnl
from app.services.credit_card_service import apply_effective_date
from app.services.fx_rate_service import stamp_primary_amount
from app.services.transfer_pair_category import categorise_pair_legs
from app.services.transaction_service import _add_transfer_legs, _pair_with_counterpart

DEFAULT_DESCRIPTION = "Loan payment"
DEFAULT_INTEREST_DESCRIPTION = "Loan interest"


class LoanPaymentConflict(Exception):
    """The loan records its payments some other way (409)."""


class LoanPaymentNotFound(Exception):
    """No such payment, loan or account in the workspace (404)."""


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _cents(value: Decimal, field: str) -> Decimal:
    value = Decimal(str(value))
    if not value.is_finite() or value != value.quantize(Decimal("0.01")):
        raise ValueError(f"{field} must have at most two decimals")
    return value.quantize(Decimal("0.01"))


def _validate_amounts(total: Decimal, interest: Decimal) -> tuple[Decimal, Decimal]:
    total = _cents(total, "total_amount")
    interest = _cents(interest, "interest_amount")
    if total <= 0:
        raise ValueError("total_amount must be greater than zero")
    if interest < 0:
        raise ValueError("interest_amount cannot be negative")
    if interest > total:
        # Capitalised interest (negative amortisation) is out of scope.
        raise ValueError("interest_amount cannot be more than total_amount")
    return total, interest


async def _get_account(session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID) -> Account:
    account = await account_service.get_account(session, account_id, workspace_id)
    if account is None:
        raise LoanPaymentNotFound("Account not found")
    return account


def manual_payments_allowed(loan: Account) -> bool:
    """Whether the manual payment form may be offered for this account."""
    return loan.type == "loan" and not loan.is_closed and loan.connection_id is None


async def _check_loan(session: AsyncSession, loan: Account) -> None:
    if loan.type != "loan":
        raise ValueError("Account is not a loan")
    if loan.is_closed:
        raise ValueError("Loan account is closed")
    if loan.connection_id is not None:
        synced = (
            await session.execute(
                select(Transaction.id)
                .where(Transaction.account_id == loan.id, Transaction.source == "sync")
                .limit(1)
            )
        ).first()
        if synced is not None:
            # Sync already writes the repayment credit and the interest
            # debit; a second copy would double count both.
            raise LoanPaymentConflict("This loan syncs its own payments and interest")
        raise LoanPaymentConflict("Payments can't be recorded on a bank-connected loan yet")


def _check_paying_account(paying: Account, loan: Account) -> None:
    if paying.id == loan.id:
        raise ValueError("The paying account must be different from the loan")
    if paying.type == "loan":
        raise ValueError("A loan can't pay another loan")
    if paying.type == "credit_card":
        # A card payment would be dropped from the card's bill
        # (counts_on_bill excludes paired rows) and misstate it.
        raise ValueError("A credit card can't be the paying account")
    if paying.is_closed:
        raise ValueError("Paying account is closed")
    if paying.currency != loan.currency:
        raise ValueError("The paying account must use the loan's currency")


async def _check_interest_category(
    session: AsyncSession, workspace_id: uuid.UUID, category_id: Optional[uuid.UUID]
) -> Category:
    if category_id is None:
        raise ValueError("interest_category_id is required when there is interest")
    category = (
        await session.execute(
            select(Category).where(Category.id == category_id, Category.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if category is None:
        raise ValueError("Interest category not found")
    # Either flag would take the interest out of P&L without a word.
    if category.treat_as_transfer:
        raise ValueError("The interest category can't be a transfer category")
    if category.is_ignored:
        raise ValueError("The interest category can't be an ignored category")
    return category


async def _is_ignored(session: AsyncSession, tx: Transaction) -> bool:
    if tx.is_ignored:
        return True
    if tx.category_id is None:
        return False
    category = await session.get(Category, tx.category_id)
    return bool(category and category.is_ignored)


async def _check_existing_debit(
    session: AsyncSession, workspace_id: uuid.UUID, transaction_id: uuid.UUID, total: Decimal
) -> Transaction:
    tx = (
        await session.execute(
            select(Transaction).where(
                Transaction.id == transaction_id, Transaction.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if tx is None:
        raise ValueError("Transaction not found")
    if tx.type != "debit" or tx.status != "posted":
        raise ValueError("The existing transaction must be a posted debit")
    if tx.transfer_pair_id is not None:
        raise ValueError("The existing transaction is already part of a transfer")
    if await _is_ignored(session, tx):
        raise ValueError("The existing transaction is ignored")
    if Decimal(str(tx.amount)) != total:
        raise ValueError("total_amount must equal the existing transaction's amount")
    linked = (
        await session.execute(
            select(LoanPayment.id).where(LoanPayment.cash_transaction_id == tx.id)
        )
    ).first()
    if linked is not None:
        raise ValueError("The existing transaction already belongs to a loan payment")
    return tx


# ---------------------------------------------------------------------------
# writes
# ---------------------------------------------------------------------------


def _interest_row(
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    loan: Account,
    amount: Decimal,
    category_id: uuid.UUID,
    when: date,
    description: str,
    notes: Optional[str],
    recurring_transaction_id: Optional[uuid.UUID],
) -> Transaction:
    tx = Transaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=loan.id,
        category_id=category_id,
        description=description,
        amount=amount,
        currency=loan.currency,
        date=when,
        type="debit",
        source="manual",
        status="posted",
        notes=notes,
        recurring_transaction_id=recurring_transaction_id,
    )
    apply_effective_date(tx, loan)
    return tx


async def record_payment(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: LoanPaymentCreate,
    *,
    recurring_transaction_id: Optional[uuid.UUID] = None,
) -> LoanPayment:
    """Write the pair and the interest row and link them, in one commit."""
    total, interest = _validate_amounts(data.total_amount, data.interest_amount)

    loan = await _get_account(session, workspace_id, data.loan_account_id)
    await _check_loan(session, loan)

    if data.existing_transaction_id is None and data.cash_account_id is None:
        raise ValueError("Give either cash_account_id or existing_transaction_id")

    anchor: Optional[Transaction] = None
    if data.existing_transaction_id is not None:
        anchor = await _check_existing_debit(session, workspace_id, data.existing_transaction_id, total)
        if data.cash_account_id is not None and data.cash_account_id != anchor.account_id:
            raise ValueError("The existing transaction is not on cash_account_id")
        paying = await _get_account(session, workspace_id, anchor.account_id)
    else:
        assert data.cash_account_id is not None
        if data.date is None:
            raise ValueError("date is required")
        paying = await _get_account(session, workspace_id, data.cash_account_id)
    _check_paying_account(paying, loan)

    if interest > 0:
        await _check_interest_category(session, workspace_id, data.interest_category_id)

    # Every check has passed; nothing has been written yet.
    description = (data.description or "").strip() or DEFAULT_DESCRIPTION
    if anchor is None:
        assert data.date is not None
        when = data.date
        cash_tx, loan_tx = await _add_transfer_legs(
            session,
            workspace_id,
            user_id,
            from_account=paying,
            to_account=loan,
            amount=total,
            date=when,
            description=description,
            notes=data.notes,
        )
        if recurring_transaction_id is not None:
            cash_tx.recurring_transaction_id = recurring_transaction_id
    else:
        when = data.date or anchor.date
        # The bank's row keeps its date and description, and its category
        # unless it has none or is in Transfers; it is excluded from P&L by
        # the pair.
        cash_tx, loan_tx = await _pair_with_counterpart(session, workspace_id, user_id, anchor, loan)
        loan_tx.date = when
        loan_tx.description = (data.description or "").strip() or anchor.description
        if data.notes is not None:
            loan_tx.notes = data.notes
        apply_effective_date(loan_tx, loan)
    await categorise_pair_legs(session, workspace_id, (cash_tx, loan_tx))

    interest_tx: Optional[Transaction] = None
    if interest > 0:
        assert data.interest_category_id is not None
        interest_tx = _interest_row(
            workspace_id=workspace_id,
            user_id=user_id,
            loan=loan,
            amount=interest,
            category_id=data.interest_category_id,
            when=when,
            description=(data.description or "").strip() or DEFAULT_INTEREST_DESCRIPTION,
            notes=data.notes,
            recurring_transaction_id=recurring_transaction_id,
        )
        session.add(interest_tx)
        await session.flush()
        await stamp_primary_amount(session, user_id, interest_tx)

    payment = LoanPayment(
        workspace_id=workspace_id,
        user_id=user_id,
        loan_account_id=loan.id,
        cash_transaction_id=cash_tx.id,
        loan_transaction_id=loan_tx.id,
        interest_transaction_id=interest_tx.id if interest_tx is not None else None,
        cash_leg_created=anchor is None,
        recurring_transaction_id=recurring_transaction_id,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


async def _load(
    session: AsyncSession, workspace_id: uuid.UUID, payment_id: uuid.UUID
) -> tuple[LoanPayment, Transaction, Transaction, Optional[Transaction]]:
    payment = (
        await session.execute(
            select(LoanPayment).where(
                LoanPayment.id == payment_id, LoanPayment.workspace_id == workspace_id
            )
        )
    ).scalar_one_or_none()
    if payment is None:
        raise LoanPaymentNotFound("Loan payment not found")
    cash_tx = await session.get(Transaction, payment.cash_transaction_id)
    loan_tx = await session.get(Transaction, payment.loan_transaction_id)
    if cash_tx is None or loan_tx is None:
        # A leg was removed without the database cascade (never on
        # PostgreSQL); treat the record as gone.
        raise LoanPaymentNotFound("Loan payment not found")
    interest_tx = (
        await session.get(Transaction, payment.interest_transaction_id)
        if payment.interest_transaction_id is not None
        else None
    )
    return payment, cash_tx, loan_tx, interest_tx


async def update_payment(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    payment_id: uuid.UUID,
    data: LoanPaymentUpdate,
) -> LoanPayment:
    """Change date, amounts, interest category or description in one commit.

    Both legs move together, so the pair keeps principal out of P&L
    whatever the new amounts are. The interest row is created, updated or
    deleted to match the new interest.
    """
    payment, cash_tx, loan_tx, interest_tx = await _load(session, workspace_id, payment_id)
    loan = await _get_account(session, workspace_id, payment.loan_account_id)
    await _check_loan(session, loan)

    current_total = Decimal(str(cash_tx.amount))
    current_interest = Decimal(str(interest_tx.amount)) if interest_tx is not None else Decimal("0")
    new_total = data.total_amount if data.total_amount is not None else current_total
    new_interest = data.interest_amount if data.interest_amount is not None else current_interest
    new_total, new_interest = _validate_amounts(new_total, new_interest)

    if not payment.cash_leg_created:
        # The cash leg is the bank's row: its amount and date are facts.
        if new_total != current_total:
            raise ValueError("This payment pairs a bank transaction; its total can't change")
        if data.date is not None and data.date != loan_tx.date:
            raise ValueError("This payment pairs a bank transaction; its date can't change")

    category_id = data.interest_category_id or (interest_tx.category_id if interest_tx else None)
    if new_interest > 0:
        await _check_interest_category(session, workspace_id, category_id)

    # Every check has passed.
    when = data.date or loan_tx.date
    description = (data.description or "").strip() or None

    if payment.cash_leg_created:
        paying = await _get_account(session, workspace_id, cash_tx.account_id)
        for leg, account in ((cash_tx, paying), (loan_tx, loan)):
            if new_total != Decimal(str(leg.amount)) or leg.date != when:
                leg.amount = new_total
                leg.date = when
                apply_effective_date(leg, account)
                await stamp_primary_amount(session, user_id, leg)
            if description:
                leg.description = description
    elif description:
        loan_tx.description = description

    if new_interest == 0:
        if interest_tx is not None:
            payment.interest_transaction_id = None
            await session.delete(interest_tx)
    elif interest_tx is None:
        assert category_id is not None
        interest_tx = _interest_row(
            workspace_id=workspace_id,
            user_id=user_id,
            loan=loan,
            amount=new_interest,
            category_id=category_id,
            when=when,
            description=description or DEFAULT_INTEREST_DESCRIPTION,
            notes=loan_tx.notes,
            recurring_transaction_id=payment.recurring_transaction_id,
        )
        session.add(interest_tx)
        await session.flush()
        await stamp_primary_amount(session, user_id, interest_tx)
        payment.interest_transaction_id = interest_tx.id
    else:
        interest_tx.amount = new_interest
        interest_tx.category_id = category_id
        interest_tx.date = when
        apply_effective_date(interest_tx, loan)
        if description:
            interest_tx.description = description
        await stamp_primary_amount(session, user_id, interest_tx)

    await session.commit()
    await session.refresh(payment)
    return payment


async def delete_payment(session: AsyncSession, workspace_id: uuid.UUID, payment_id: uuid.UUID) -> None:
    """Undo a payment in one commit.

    Deletes the loan leg, the interest row and the record. The cash leg is
    deleted when the payment wrote it; a bank row it paired is unpaired
    instead. That row keeps its category: if recording the payment moved it
    into Loan repayments (it had none, or Transfers), it stays there, out of
    P&L, because nothing records which of the two it was.
    """
    from app.services.attachment_service import cleanup_attachment_files

    payment, cash_tx, loan_tx, interest_tx = await _load(session, workspace_id, payment_id)
    doomed = [loan_tx] + ([interest_tx] if interest_tx is not None else [])
    if payment.cash_leg_created:
        doomed.append(cash_tx)
    else:
        cash_tx.transfer_pair_id = None

    await cleanup_attachment_files(session, [tx.id for tx in doomed])
    await session.delete(payment)
    await session.flush()
    for tx in doomed:
        await session.delete(tx)
    await session.commit()


# ---------------------------------------------------------------------------
# reads
# ---------------------------------------------------------------------------


async def list_payments(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    loan_account_id: Optional[uuid.UUID] = None,
    payment_id: Optional[uuid.UUID] = None,
) -> list[LoanPaymentRead]:
    """Payments with amounts derived from their rows, newest first."""
    cash = aliased(Transaction)
    leg = aliased(Transaction)
    interest = aliased(Transaction)
    query = (
        select(LoanPayment, cash, leg, interest)
        .join(cash, cash.id == LoanPayment.cash_transaction_id)
        .join(leg, leg.id == LoanPayment.loan_transaction_id)
        .outerjoin(interest, interest.id == LoanPayment.interest_transaction_id)
        .where(LoanPayment.workspace_id == workspace_id)
        .order_by(leg.date.desc(), LoanPayment.created_at.desc())
    )
    if loan_account_id is not None:
        query = query.where(LoanPayment.loan_account_id == loan_account_id)
    if payment_id is not None:
        query = query.where(LoanPayment.id == payment_id)

    out: list[LoanPaymentRead] = []
    for payment, cash_tx, loan_tx, interest_tx in (await session.execute(query)).all():
        total = Decimal(str(cash_tx.amount))
        interest_amount = Decimal(str(interest_tx.amount)) if interest_tx is not None else Decimal("0")
        out.append(
            LoanPaymentRead(
                id=payment.id,
                loan_account_id=payment.loan_account_id,
                cash_account_id=cash_tx.account_id,
                cash_transaction_id=cash_tx.id,
                loan_transaction_id=loan_tx.id,
                interest_transaction_id=interest_tx.id if interest_tx is not None else None,
                interest_category_id=interest_tx.category_id if interest_tx is not None else None,
                cash_leg_created=payment.cash_leg_created,
                recurring_transaction_id=payment.recurring_transaction_id,
                date=loan_tx.date,
                description=loan_tx.description,
                currency=loan_tx.currency,
                total=total,
                interest=interest_amount,
                principal=total - interest_amount,
                created_at=payment.created_at,
            )
        )
    return out


async def get_payment(session: AsyncSession, workspace_id: uuid.UUID, payment_id: uuid.UUID) -> LoanPaymentRead:
    rows = await list_payments(session, workspace_id, payment_id=payment_id)
    if not rows:
        raise LoanPaymentNotFound("Loan payment not found")
    return rows[0]


def _shift_month(month_start: date, months: int) -> date:
    index = month_start.year * 12 + month_start.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


async def get_loan_summary(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID, months: int = 12
) -> dict:
    """Paid, interest and principal reduction per month, from the ledger.

    Works the same for synced and manual loans and never writes. Only
    posted, non-ignored rows count; the opening balance is not a payment.
    `interest` is the loan's debits that count in P&L (interest and fees);
    `principal_reduction` is credits minus all debits, i.e. how much the
    amount owed fell.
    """
    loan = await _get_account(session, workspace_id, account_id)
    if loan.type != "loan":
        raise ValueError("Account is not a loan")

    this_month = app_today().replace(day=1)
    first = _shift_month(this_month, -(months - 1))
    end = _shift_month(this_month, 1) - timedelta(days=1)

    rows = (
        await session.execute(
            select(
                Transaction.date,
                Transaction.type,
                Transaction.amount,
                case((counts_as_user_pnl(), True), else_=False),
            ).where(
                Transaction.account_id == loan.id,
                Transaction.source != "opening_balance",
                Transaction.status == "posted",
                Transaction.is_ignored.is_(False),
                Transaction.date >= first,
                Transaction.date <= end,
            )
        )
    ).all()

    buckets: dict[str, dict[str, Decimal]] = {}
    cursor = first
    while cursor <= this_month:
        buckets[cursor.strftime("%Y-%m")] = {"paid": Decimal("0"), "interest": Decimal("0"), "debits": Decimal("0")}
        cursor = _shift_month(cursor, 1)

    for when, tx_type, amount, in_pnl in rows:
        bucket = buckets[when.strftime("%Y-%m")]
        amount = Decimal(str(amount))
        if tx_type == "credit":
            bucket["paid"] += amount
        else:
            bucket["debits"] += amount
            if in_pnl:
                bucket["interest"] += amount

    zero = Decimal("0")
    return {
        "account_id": loan.id,
        "currency": loan.currency,
        "manual_payments_allowed": manual_payments_allowed(loan),
        "months": [
            {
                "month": key,
                "paid": float(b["paid"]),
                "interest": float(b["interest"]),
                "principal_reduction": float(b["paid"] - b["debits"]),
            }
            for key, b in buckets.items()
        ],
        "total_paid": float(sum((b["paid"] for b in buckets.values()), zero)),
        "total_interest": float(sum((b["interest"] for b in buckets.values()), zero)),
        "total_principal_reduction": float(
            sum((b["paid"] - b["debits"] for b in buckets.values()), zero)
        ),
    }
