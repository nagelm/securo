import { describe, expect, it } from 'vitest'

import {
  groupTransferPairs,
  primaryTransaction,
  rowTransactions,
  toggleDisplayRowSelection,
} from '@/lib/transfer-pair-rows'

type Tx = {
  id: string
  type: 'debit' | 'credit'
  transfer_pair_id: string | null
  date: string
  amount: number
  currency: string
  is_shared?: boolean
}

const tx = (id: string, overrides: Partial<Tx> = {}): Tx => ({
  id,
  type: 'debit',
  transfer_pair_id: null,
  date: '2026-03-10',
  amount: 10,
  currency: 'USD',
  ...overrides,
})

const out = (id: string, pair: string, overrides: Partial<Tx> = {}) =>
  tx(id, { type: 'debit', transfer_pair_id: pair, ...overrides })
const inn = (id: string, pair: string, overrides: Partial<Tx> = {}) =>
  tx(id, { type: 'credit', transfer_pair_id: pair, ...overrides })

const keys = (items: Tx[]) => groupTransferPairs(items).map((row) => row.ids.join('+'))

describe('groupTransferPairs', () => {
  it('draws both legs of a pair as one row', () => {
    const rows = groupTransferPairs([out('a', 'p1'), inn('b', 'p1')])

    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ kind: 'pair', ids: ['a', 'b'] })
    if (rows[0].kind !== 'pair') throw new Error('expected a pair row')
    expect(rows[0].out.id).toBe('a')
    expect(rows[0].in.id).toBe('b')
  })

  it('keeps a lone leg as its own row', () => {
    const rows = groupTransferPairs([tx('x'), inn('b', 'p1'), tx('y')])

    expect(rows.map((row) => row.kind)).toEqual(['single', 'single', 'single'])
    expect(keys([tx('x'), inn('b', 'p1'), tx('y')])).toEqual(['x', 'b', 'y'])
  })

  it('puts the pair where the outgoing leg sat, whichever leg comes first', () => {
    expect(keys([tx('x'), out('a', 'p1'), tx('y'), inn('b', 'p1'), tx('z')])).toEqual(['x', 'a+b', 'y', 'z'])
    expect(keys([tx('x'), inn('b', 'p1'), tx('y'), out('a', 'p1'), tx('z')])).toEqual(['x', 'y', 'a+b', 'z'])
  })

  it('preserves the order of unrelated rows and several pairs', () => {
    const items = [
      tx('1'),
      out('a', 'p1'),
      inn('c', 'p2'),
      inn('b', 'p1'),
      tx('2'),
      out('d', 'p2'),
      tx('3'),
    ]

    expect(keys(items)).toEqual(['1', 'a+b', '2', 'd+c', '3'])
  })

  it('pairs legs that landed on different dates and keeps each date', () => {
    const rows = groupTransferPairs([inn('b', 'p1', { date: '2026-03-12' }), out('a', 'p1', { date: '2026-03-10' })])

    expect(rows).toHaveLength(1)
    const row = rows[0]
    if (row.kind !== 'pair') throw new Error('expected a pair row')
    expect(row.out.date).toBe('2026-03-10')
    expect(row.in.date).toBe('2026-03-12')
    expect(primaryTransaction(row).date).toBe('2026-03-10')
  })

  it('keeps each leg of a cross-currency transfer as sent', () => {
    const rows = groupTransferPairs([
      out('a', 'p1', { amount: 100, currency: 'USD' }),
      inn('b', 'p1', { amount: 92.5, currency: 'EUR' }),
    ])

    const row = rows[0]
    if (row.kind !== 'pair') throw new Error('expected a pair row')
    expect([row.out.amount, row.out.currency]).toEqual([100, 'USD'])
    expect([row.in.amount, row.in.currency]).toEqual([92.5, 'EUR'])
  })

  it('leaves odd pairs alone: two debits, three legs, or a shared row', () => {
    expect(keys([out('a', 'p1'), out('b', 'p1')])).toEqual(['a', 'b'])
    expect(keys([out('a', 'p1'), inn('b', 'p1'), inn('c', 'p1')])).toEqual(['a', 'b', 'c'])
    expect(keys([out('a', 'p1'), inn('b', 'p1', { is_shared: true })])).toEqual(['a', 'b'])
  })

  it('accounts for every transaction exactly once', () => {
    const items = [tx('1'), out('a', 'p1'), inn('b', 'p1'), inn('c', 'p2'), tx('2')]
    const ids = groupTransferPairs(items).flatMap((row) => rowTransactions(row).map((t) => t.id))

    expect(ids.sort()).toEqual(items.map((t) => t.id).sort())
  })
})

describe('toggleDisplayRowSelection', () => {
  const rows = groupTransferPairs([tx('x'), out('a', 'p1'), inn('b', 'p1'), tx('y'), tx('z')])

  it('selects both legs from either leg id, and clears both', () => {
    const selected = toggleDisplayRowSelection(new Set(), null, 'a', rows, false)
    expect([...selected].sort()).toEqual(['a', 'b'])

    expect([...toggleDisplayRowSelection(new Set(), null, 'b', rows, false)].sort()).toEqual(['a', 'b'])
    expect(toggleDisplayRowSelection(selected, 'a', 'a', rows, false).size).toBe(0)
  })

  it('completes a half-selected pair instead of clearing it', () => {
    expect([...toggleDisplayRowSelection(new Set(['a']), null, 'a', rows, false)].sort()).toEqual(['a', 'b'])
  })

  it('toggles a plain row by itself', () => {
    expect([...toggleDisplayRowSelection(new Set(['x']), 'x', 'y', rows, false)].sort()).toEqual(['x', 'y'])
  })

  it('extends a shift range across rows, taking both legs of a pair inside it', () => {
    const selected = toggleDisplayRowSelection(new Set(['x']), 'x', 'z', rows, true)

    expect([...selected].sort()).toEqual(['a', 'b', 'x', 'y', 'z'])
  })

  it('skips rows the caller cannot select inside a shift range', () => {
    const selected = toggleDisplayRowSelection(
      new Set(['x']), 'x', 'z', rows, true, (row) => row.kind !== 'pair',
    )

    expect([...selected].sort()).toEqual(['x', 'y', 'z'])
  })

  it('toggles an id it cannot find in the rows', () => {
    expect([...toggleDisplayRowSelection(new Set(), null, 'missing', rows, false)]).toEqual(['missing'])
  })
})
