import { describe, expect, it, vi } from 'vitest'

import type { PaginatedTransactions, Transaction } from '@/types'
import {
  fetchAllTransactionPages,
  isLongRangeAccount,
  longRangeDefault,
  monthlyPoints,
  yearTicks,
} from './account-long-range'

describe('isLongRangeAccount', () => {
  it('is true for loans and investments only', () => {
    expect(isLongRangeAccount('loan')).toBe(true)
    expect(isLongRangeAccount('investment')).toBe(true)
    for (const type of ['checking', 'savings', 'credit_card', 'wallet', undefined, null]) {
      expect(isLongRangeAccount(type)).toBe(false)
    }
  })
})

describe('longRangeDefault', () => {
  it('runs from the first of the month five years back to the end of this month', () => {
    expect(longRangeDefault(new Date(2026, 9, 13))).toEqual({ start: '2021-10-01', end: '2026-10-31' })
  })

  it('ends on the last day of February in a leap year', () => {
    expect(longRangeDefault(new Date(2028, 1, 10))).toEqual({ start: '2023-02-01', end: '2028-02-29' })
  })
})

function page(ids: number[], total: number): PaginatedTransactions {
  return {
    items: ids.map((n) => ({ id: `tx-${n}` }) as Transaction),
    total,
    page: 1,
    limit: 500,
  }
}

describe('fetchAllTransactionPages', () => {
  it('asks once when the first page holds every row', async () => {
    const list = vi.fn().mockResolvedValueOnce(page([1, 2], 2))
    const result = await fetchAllTransactionPages(list, { account_id: 'a' })
    expect(list).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledWith({ account_id: 'a', page: 1, limit: 500 })
    expect(result.items).toHaveLength(2)
  })

  it('follows the pages until it has the total, in order', async () => {
    const first = Array.from({ length: 500 }, (_, i) => i)
    const second = Array.from({ length: 120 }, (_, i) => 500 + i)
    const list = vi.fn()
      .mockResolvedValueOnce(page(first, 620))
      .mockResolvedValueOnce(page(second, 620))
    const result = await fetchAllTransactionPages(list, { account_id: 'a' })
    expect(list).toHaveBeenNthCalledWith(2, { account_id: 'a', page: 2, limit: 500 })
    expect(result.items).toHaveLength(620)
    expect(result.items[0].id).toBe('tx-0')
    expect(result.items[619].id).toBe('tx-619')
    expect(result.total).toBe(620)
  })

  it('stops on an empty page rather than asking forever', async () => {
    const list = vi.fn()
      .mockResolvedValueOnce(page([1], 900))
      .mockResolvedValueOnce(page([], 900))
    const result = await fetchAllTransactionPages(list, {})
    expect(list).toHaveBeenCalledTimes(2)
    expect(result.items).toHaveLength(1)
  })
})

describe('monthlyPoints', () => {
  it('keeps the last point of each month, and the last point of the range', () => {
    const daily = ['2024-01-30', '2024-01-31', '2024-02-01', '2024-02-29', '2024-03-01', '2024-03-02']
      .map((date, i) => ({ date, balance: i }))
    expect(monthlyPoints(daily).map((p) => p.date)).toEqual(['2024-01-31', '2024-02-29', '2024-03-02'])
  })

  it('is empty for an empty series', () => {
    expect(monthlyPoints([])).toEqual([])
  })
})

describe('yearTicks', () => {
  it('ticks the first point of each year', () => {
    const points = [
      { date: '2023-11-30', label: 'Nov 2023' },
      { date: '2023-12-31', label: 'Dec 2023' },
      { date: '2024-01-31', label: 'Jan 2024' },
      { date: '2024-02-29', label: 'Feb 2024' },
      { date: '2025-01-31', label: 'Jan 2025' },
    ]
    expect(yearTicks(points)).toEqual(['Nov 2023', 'Jan 2024', 'Jan 2025'])
  })
})
