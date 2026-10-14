type PendingBadgeTransaction = {
  source: string | null
  status: string | null
}

/** Bank-sync pending state already matches the provider and needs no UI badge. */
export function shouldShowPendingBadge(transaction: PendingBadgeTransaction): boolean {
  return transaction.status === 'pending' && transaction.source !== 'sync'
}

/**
 * Fork (owner 2026-10-15): a bank-synced row the bank hasn't settled yet.
 * Shown as "Unsettled" (it has no merchant or payee until it settles), unlike
 * Securo's own scheduled rows, which keep the plain pending clock.
 */
export function isUnsettled(transaction: PendingBadgeTransaction): boolean {
  return transaction.status === 'pending' && transaction.source === 'sync'
}
