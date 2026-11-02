import { describe, expect, it } from 'vitest'
import { reorderWallets } from './wallet-order'

const w = (id: string, position: number) => ({ id, name: id, position })
const names = (list: { id: string; position: number }[]) =>
  list.slice().sort((a, b) => a.position - b.position).map((x) => x.id)

describe('reorderWallets', () => {
  it('puts the wallets in the new order', () => {
    expect(names(reorderWallets([w('a', 0), w('b', 1), w('c', 2)], ['c', 'a', 'b']))).toEqual(['c', 'a', 'b'])
  })

  it('keeps unnamed wallets in their places and separates ties by name', () => {
    const all = [w('a', 0), w('b', 0), w('c', 0), w('d', 0)]
    expect(names(reorderWallets(all, ['d', 'b']))).toEqual(['a', 'd', 'c', 'b'])
  })
})
