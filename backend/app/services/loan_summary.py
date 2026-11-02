"""Fork: what a loan's rows say per month (the loan card)."""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.transaction import Transaction
from app.services._query_filters import counts_as_user_pnl


class LoanNotFound(Exception):
    pass


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
    loan = (
        await session.execute(
            select(Account).where(Account.id == account_id, Account.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if loan is None:
        raise LoanNotFound("Account not found")
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
