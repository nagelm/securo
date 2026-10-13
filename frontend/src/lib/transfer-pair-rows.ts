/**
 * Display grouping for linked transfers in a transaction list.
 *
 * A linked transfer is two rows sharing a `transfer_pair_id`: the debit leg
 * on the source account and the credit leg on the destination. When both legs
 * are in the same loaded list they read as one movement, so the list draws
 * them as one row. This is display only: the legs stay two transactions for
 * counts, totals, balances and every API call.
 */

type PairableTransaction = {
  id: string
  type: 'debit' | 'credit'
  transfer_pair_id: string | null
  is_shared?: boolean
}

export type TransactionDisplayRow<T> =
  | { kind: 'single'; key: string; tx: T; ids: [string] }
  | { kind: 'pair'; key: string; out: T; in: T; ids: [string, string] }

/**
 * Group the list into display rows, keeping its order.
 *
 * Both legs of a pair become one row, placed where the outgoing (debit) leg
 * sat; the credit leg's own position is dropped. A pair is grouped only when
 * exactly one debit and one credit with that pair id are in `items` and
 * neither is a row shared from another user's group. Anything else (one leg
 * on another page or filtered out, odd data) stays one row per transaction.
 */
export function groupTransferPairs<T extends PairableTransaction>(
  items: readonly T[],
): TransactionDisplayRow<T>[] {
  const legsByPair = new Map<string, T[]>()
  for (const tx of items) {
    if (!tx.transfer_pair_id) continue
    const legs = legsByPair.get(tx.transfer_pair_id)
    if (legs) legs.push(tx)
    else legsByPair.set(tx.transfer_pair_id, [tx])
  }

  const pairs = new Map<string, { out: T; in: T }>()
  for (const [pairId, legs] of legsByPair) {
    if (legs.length !== 2) continue
    const out = legs.find((leg) => leg.type === 'debit')
    const inLeg = legs.find((leg) => leg.type === 'credit')
    if (!out || !inLeg || out.is_shared || inLeg.is_shared) continue
    pairs.set(pairId, { out, in: inLeg })
  }

  const rows: TransactionDisplayRow<T>[] = []
  for (const tx of items) {
    const pair = tx.transfer_pair_id ? pairs.get(tx.transfer_pair_id) : undefined
    if (!pair) {
      rows.push({ kind: 'single', key: tx.id, tx, ids: [tx.id] })
    } else if (pair.out === tx) {
      rows.push({ kind: 'pair', key: `pair:${tx.id}`, out: pair.out, in: pair.in, ids: [pair.out.id, pair.in.id] })
    }
  }
  return rows
}

/** The leg a row opens and dates by: the transaction, or a pair's debit leg. */
export function primaryTransaction<T>(row: TransactionDisplayRow<T>): T {
  return row.kind === 'pair' ? row.out : row.tx
}

/** Every transaction a row stands for. */
export function rowTransactions<T>(row: TransactionDisplayRow<T>): T[] {
  return row.kind === 'pair' ? [row.out, row.in] : [row.tx]
}

/**
 * Click-to-select over display rows. Selecting a pair row selects both legs,
 * so bulk actions act on the whole transfer. Shift extends the range from the
 * last clicked row as the plain list does, skipping rows `isSelectable`
 * rejects; the selection itself stays a set of transaction ids.
 */
export function toggleDisplayRowSelection<T>(
  currentSelected: ReadonlySet<string>,
  lastSelectedId: string | null,
  clickedId: string,
  rows: readonly TransactionDisplayRow<T>[],
  isShiftKey: boolean,
  isSelectable: (row: TransactionDisplayRow<T>) => boolean = () => true,
): Set<string> {
  const next = new Set(currentSelected)
  const indexOf = (id: string) => rows.findIndex((row) => row.ids.includes(id))
  const clickedIndex = indexOf(clickedId)
  if (clickedIndex === -1) {
    if (next.has(clickedId)) next.delete(clickedId)
    else next.add(clickedId)
    return next
  }

  const shouldSelect = !rows[clickedIndex].ids.every((id) => currentSelected.has(id))
  const apply = (row: TransactionDisplayRow<T>) => {
    for (const id of row.ids) {
      if (shouldSelect) next.add(id)
      else next.delete(id)
    }
  }

  const lastIndex = isShiftKey && lastSelectedId ? indexOf(lastSelectedId) : -1
  if (lastIndex === -1) {
    apply(rows[clickedIndex])
    return next
  }
  const start = Math.min(lastIndex, clickedIndex)
  const end = Math.max(lastIndex, clickedIndex)
  for (let i = start; i <= end; i++) {
    if (isSelectable(rows[i])) apply(rows[i])
  }
  return next
}
