import type { Account, Category, Transaction } from '@/types'

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
