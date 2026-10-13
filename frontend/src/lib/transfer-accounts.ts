/**
 * The two accounts a linked transfer moved money between, read off either leg.
 *
 * The debit leg is always "from" and the credit leg "to", whichever leg's row
 * is asking. Returns null when the row is not a paired leg, or when either
 * account is not in `accountsById` (a closed account, say), so callers can
 * fall back to the plain description.
 */
export function resolveTransferAccounts<A>(
  tx: {
    type: 'debit' | 'credit'
    account_id: string | null
    transfer_pair_id: string | null
    transfer_counterpart_account_id?: string | null
  },
  accountsById: ReadonlyMap<string, A>,
): { from: A; to: A } | null {
  if (!tx.transfer_pair_id || !tx.account_id || !tx.transfer_counterpart_account_id) return null
  const own = accountsById.get(tx.account_id)
  const other = accountsById.get(tx.transfer_counterpart_account_id)
  if (!own || !other) return null
  return tx.type === 'debit' ? { from: own, to: other } : { from: other, to: own }
}
