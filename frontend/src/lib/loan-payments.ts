import type { Account, Category, ProjectedTransaction, Transaction } from '@/types'

/**
 * Form helpers for recording a loan payment. The backend validates every
 * rule again; these only keep the dialog from offering what it would refuse.
 */

/** Whole cents from a form value, or null when it is not a number. */
export function toCents(value: string): number | null {
  if (value.trim() === '') return null
  const n = Number(value)
  if (!Number.isFinite(n)) return null
  return Math.round(n * 100)
}

/** Two-decimal string the API's Decimal fields accept without float noise. */
export function centsToAmount(cents: number): string {
  return (cents / 100).toFixed(2)
}

/**
 * Accounts that may pay a loan: open, not the loan itself, not another loan,
 * not a credit card (a card-funded payment would misstate the card's bill),
 * and in the loan's currency (cross-currency payments are not supported).
 */
export function payingAccountsFor(loan: Account, accounts: Account[]): Account[] {
  return accounts.filter(
    (a) =>
      a.id !== loan.id &&
      !a.is_closed &&
      a.type !== 'loan' &&
      a.type !== 'credit_card' &&
      a.currency === loan.currency,
  )
}

/**
 * Categories the interest may go to. Transfer-like and ignored categories are
 * left out: either would silently take the interest out of P&L.
 */
export function interestCategoriesFrom(categories: Category[]): Category[] {
  return categories.filter((c) => !c.treat_as_transfer && !c.is_ignored)
}

/** How many days either side of the payment date an existing debit may sit. */
export const EXISTING_DEBIT_WINDOW_DAYS = 5

/**
 * Whether a transaction on the paying account can be paired with a payment of
 * `totalCents`: a posted, unpaired debit of exactly that amount. The date
 * window is applied by the query that fetched the candidates.
 */
export function isExistingDebitCandidate(tx: Transaction, totalCents: number): boolean {
  return (
    tx.type === 'debit' &&
    tx.status === 'posted' &&
    tx.transfer_pair_id == null &&
    Math.round(tx.amount * 100) === totalCents
  )
}

/**
 * The part of a projected recurring transaction that is income or expense.
 *
 * A recurring loan payment projects the whole payment leaving the paying
 * account, but only its interest is an expense: the rest is principal, a
 * transfer to the loan. P&L views must show the interest, in its own
 * category, and nothing else. Returns null when none of it is P&L (a loan
 * payment with no interest). Any other projection is returned unchanged.
 */
export function projectedPnlShare<T extends ProjectedTransaction>(pt: T): T | null {
  if (pt.kind !== 'loan_payment') return pt
  const interest = Number(pt.interest_amount ?? 0)
  if (!(interest > 0)) return null
  return {
    ...pt,
    amount: interest,
    amount_primary: pt.interest_amount_primary ?? null,
    category_id: pt.interest_category_id ?? null,
    category_name: pt.interest_category_name ?? null,
    category_icon: pt.interest_category_icon ?? null,
    category_color: pt.interest_category_color ?? null,
  }
}

/** What stops a recurring loan payment from being saved, as a locale key, or null. */
export function loanScheduleProblem(
  totalCents: number | null,
  interestCents: number | null,
  interestCategoryId: string,
): string | null {
  if (totalCents == null || totalCents <= 0) return 'loans.recurring.totalRequired'
  if (interestCents != null && interestCents < 0) return 'loans.recurring.interestNegative'
  if ((interestCents ?? 0) > totalCents) return 'loans.payment.interestTooHigh'
  if ((interestCents ?? 0) > 0 && !interestCategoryId) return 'loans.recurring.interestCategoryRequired'
  return null
}
