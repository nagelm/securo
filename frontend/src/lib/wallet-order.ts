/**
 * Fork: the wallets with new positions after the user put `ids` in this
 * order, as the server does it (PUT /asset-groups/order): every wallet is
 * numbered in its current order (position, then name), and the named ones
 * are dealt back into their own places in the requested order.
 */
export function reorderWallets<T extends { id: string; name: string; position: number }>(
  wallets: readonly T[],
  ids: readonly string[],
): T[] {
  const order = wallets
    .slice()
    .sort((a, b) => a.position - b.position || a.name.localeCompare(b.name))
    .map((w) => w.id)
  const requested = new Set(ids)
  const places = order.flatMap((id, i) => (requested.has(id) ? [i] : []))
  places.forEach((place, k) => {
    order[place] = ids[k]
  })
  const position = new Map(order.map((id, i) => [id, i]))
  return wallets.map((w) => ({ ...w, position: position.get(w.id) ?? w.position }))
}
