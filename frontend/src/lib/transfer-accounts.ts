import type { TransferAccountRef } from '@/types'

/**
 * The two accounts a linked transfer moved money between, read off either leg.
 *
 * The debit leg is always "from" and the credit leg "to", whichever leg's row
 * is asking. Each account comes from `accountsById` when it is loaded there,
 * else from the summary the API sent with the row (the accounts list leaves
 * closed accounts out). Returns null when the row is not a paired leg or an
 * account cannot be found either way, so callers keep the plain description.
 */
export function resolveTransferAccounts<A>(
  tx: {
    type: 'debit' | 'credit'
    account_id: string | null
    transfer_pair_id: string | null
    transfer_counterpart_account_id?: string | null
    transfer_account?: TransferAccountRef | null
    transfer_counterpart_account?: TransferAccountRef | null
  },
  accountsById: ReadonlyMap<string, A>,
): { from: A | TransferAccountRef; to: A | TransferAccountRef } | null {
  if (!tx.transfer_pair_id || !tx.account_id || !tx.transfer_counterpart_account_id) return null
  const own = accountsById.get(tx.account_id) ?? tx.transfer_account ?? undefined
  const other = accountsById.get(tx.transfer_counterpart_account_id) ?? tx.transfer_counterpart_account ?? undefined
  if (!own || !other) return null
  return tx.type === 'debit' ? { from: own, to: other } : { from: other, to: own }
}
