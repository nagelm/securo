import { describe, expect, it } from 'vitest'
import { applyAccountOrder, moveId, sortAccountsByDisplayName, sortAccountsByPosition } from './account-utils'

describe('sortAccountsByDisplayName', () => {
  it('orders accounts by display name when present and falls back to name', () => {
    const accounts = [
      { id: '1', name: 'Alpha', display_name: 'Vacation' },
      { id: '2', name: 'Zulu', display_name: null },
      { id: '3', name: 'Checking', display_name: 'Bills' },
    ]

    expect(sortAccountsByDisplayName(accounts).map((account) => account.id)).toEqual([
      '3',
      '1',
      '2',
    ])
  })

  it('does not mutate the API account list', () => {
    const accounts = [{ name: 'Zulu' }, { name: 'Alpha' }]

    sortAccountsByDisplayName(accounts)

    expect(accounts.map((account) => account.name)).toEqual(['Zulu', 'Alpha'])
  })
})

describe('sortAccountsByPosition', () => {
  it('orders accounts by the position the user gave them, not by name or balance', () => {
    const accounts = [
      { id: 'alpha', name: 'Alpha', position: 2, current_balance: 9000 },
      { id: 'zulu', name: 'Zulu', position: 0, current_balance: 1 },
      { id: 'mike', name: 'Mike', position: 1, current_balance: -500 },
    ]

    expect(sortAccountsByPosition(accounts).map((a) => a.id)).toEqual(['zulu', 'mike', 'alpha'])
  })

  it('keeps the API order for tied positions and for items without one', () => {
    const tied = [{ id: 'b', position: 0 }, { id: 'a', position: 0 }]
    expect(sortAccountsByPosition(tied).map((a) => a.id)).toEqual(['b', 'a'])

    const mapped = [{ id: 'second' }, { id: 'first' }]
    expect(sortAccountsByPosition(mapped).map((a) => a.id)).toEqual(['second', 'first'])
  })

  it('does not mutate the API account list', () => {
    const accounts = [{ id: 'late', position: 5 }, { id: 'early', position: 1 }]

    sortAccountsByPosition(accounts)

    expect(accounts.map((a) => a.id)).toEqual(['late', 'early'])
  })
})

describe('applyAccountOrder', () => {
  const list = ['A', 'B', 'C', 'D', 'E'].map((id, position) => ({ id, position }))

  it('swaps the named accounts among their own places and leaves the rest', () => {
    const result = applyAccountOrder(list, ['E', 'A', 'C'])

    expect(result.map((a) => a.id)).toEqual(['E', 'B', 'A', 'D', 'C'])
    expect(result.map((a) => a.position)).toEqual([0, 1, 2, 3, 4])
    expect(list.map((a) => a.id)).toEqual(['A', 'B', 'C', 'D', 'E'])
  })

  it('ignores ids that are not in the list', () => {
    expect(applyAccountOrder(list, ['B', 'nope', 'A']).map((a) => a.id)).toEqual(['B', 'A', 'C', 'D', 'E'])
  })
})

describe('moveId', () => {
  it('moves one id and clamps at the ends', () => {
    expect(moveId(['a', 'b', 'c'], 0, 2)).toEqual(['b', 'c', 'a'])
    expect(moveId(['a', 'b', 'c'], 2, 1)).toEqual(['a', 'c', 'b'])
    expect(moveId(['a', 'b', 'c'], 0, -1)).toEqual(['a', 'b', 'c'])
    expect(moveId(['a', 'b', 'c'], 5, 0)).toEqual(['a', 'b', 'c'])
  })
})
