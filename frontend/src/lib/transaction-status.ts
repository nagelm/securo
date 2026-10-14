type PendingBadgeTransaction = {
  source: string | null
  status: string | null
}

/**
 * Every pending row shows the pending badge. Fork (owner 2026-10-15): bank
 * rows too. Upstream hid it for synced rows, so a row the bank hadn't
 * settled looked settled, just without a payee (Akahu sends pending rows
 * without a merchant).
 */
export function shouldShowPendingBadge(transaction: PendingBadgeTransaction): boolean {
  return transaction.status === 'pending'
}
