import { describe, expect, it } from 'vitest'
import {
  applyAccountOrder,
  moveId,
  sidebarAccountBalance,
  sidebarAccounts,
  sortAccountsByDisplayName,
  sortAccountsByPosition,
  sumAccountsTotal,
} from './account-utils'

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

describe('sumAccountsTotal', () => {
  it('leaves loans out of the accounts total', () => {
    const accounts = [
      { type: 'checking', current_balance: 4000, balance_primary: null, shared_balance_group: null },
      { type: 'credit_card', current_balance: -500, balance_primary: null, shared_balance_group: null },
      { type: 'loan', current_balance: -200000, balance_primary: null, shared_balance_group: null },
    ]

    expect(sumAccountsTotal(accounts)).toBe(3500)
  })
})

describe('sidebarAccounts', () => {
  it('leaves loans out of the sidebar list', () => {
    const accounts = [
      { id: 'checking', type: 'checking' },
      { id: 'card', type: 'credit_card' },
      { id: 'mortgage', type: 'loan' },
    ]

    expect(sidebarAccounts(accounts).map((account) => account.id)).toEqual(['checking', 'card'])
  })
})

describe('sidebarAccountBalance', () => {
  const sharedCard = (extra: Record<string, unknown> = {}) => ({
    type: 'credit_card',
    shared_balance_group: 'provider:line-1',
    balance_primary: null,
    current_balance: -1500,
    open_bill_total: null,
    open_bill_total_primary: null,
    ...extra,
  })

  it('shows a shared card own open bill instead of the line balance', () => {
    const { amount, isPrimary } = sidebarAccountBalance(
      sharedCard({ open_bill_total: 820.5 }),
    )

    expect(amount).toBe(-820.5)
    expect(isPrimary).toBe(false)
  })

  it('prefers the converted bill when the account is in a foreign currency', () => {
    const { amount, isPrimary } = sidebarAccountBalance(
      sharedCard({ open_bill_total: 200, open_bill_total_primary: 1000 }),
    )

    expect(amount).toBe(-1000)
    expect(isPrimary).toBe(true)
  })

  it('keeps the balance when a shared card has no open bill', () => {
    const { amount, isPrimary } = sidebarAccountBalance(sharedCard())

    expect(amount).toBe(-1500)
    expect(isPrimary).toBe(false)
  })

  it('keeps the balance for a non-shared card even if a bill value is present', () => {
    const { amount, isPrimary } = sidebarAccountBalance(
      sharedCard({ shared_balance_group: null, open_bill_total: 700 }),
    )

    expect(amount).toBe(-1500)
    expect(isPrimary).toBe(false)
  })

  it('keeps the converted balance for a non-credit-card account', () => {
    const { amount, isPrimary } = sidebarAccountBalance(
      sharedCard({ type: 'checking', balance_primary: 90, open_bill_total: null }),
    )

    expect(amount).toBe(90)
    expect(isPrimary).toBe(true)
  })
})
