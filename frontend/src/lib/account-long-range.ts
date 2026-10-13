import type { PaginatedTransactions } from '@/types'
import { localDateString } from '@/lib/date-utils'

/** Years a long-horizon account's page opens on. */
export const LONG_RANGE_YEARS = 5

/** Most rows the API returns per page (its `limit` ceiling). */
export const TRANSACTION_PAGE_LIMIT = 500

/** Pages fetched at most for one range: 10,000 rows, decades of weekly loan rows. */
const MAX_TRANSACTION_PAGES = 20

/** Loans and investments move over years, not within a month, so their page
 *  opens on years of history and charts it month by month. */
export function isLongRangeAccount(type: string | null | undefined): boolean {
  return type === 'loan' || type === 'investment'
}

/** The default range for a long-horizon account: from the first day of the
 *  month five years back to the last day of the current month. */
export function longRangeDefault(today: Date = new Date()): { start: string; end: string } {
  return {
    start: localDateString(new Date(today.getFullYear() - LONG_RANGE_YEARS, today.getMonth(), 1)),
    end: localDateString(new Date(today.getFullYear(), today.getMonth() + 1, 0)),
  }
}

/** Fetch every page of a transactions query.
 *
 *  The account page seeds a running balance at the start of the range and
 *  walks every row in it, so a list cut at the API's page size would start
 *  the walk from the wrong rows (the API returns newest first and drops the
 *  oldest). Years of weekly loan rows go past one page. */
export async function fetchAllTransactionPages<P extends object>(
  list: (params: NoInfer<P> & { page: number; limit: number }) => Promise<PaginatedTransactions>,
  params: P,
): Promise<PaginatedTransactions> {
  const first = await list({ ...params, page: 1, limit: TRANSACTION_PAGE_LIMIT })
  const items = [...first.items]
  let page = 1
  while (items.length < first.total && page < MAX_TRANSACTION_PAGES) {
    page += 1
    const next = await list({ ...params, page, limit: TRANSACTION_PAGE_LIMIT })
    if (next.items.length === 0) break
    items.push(...next.items)
  }
  return { ...first, items }
}

/** One point per month (the month's last day in range), from a daily series.
 *  A daily line over five years is 1,800 points that a chart can't label. */
export function monthlyPoints<T extends { date: string }>(daily: T[]): T[] {
  const out: T[] = []
  for (let i = 0; i < daily.length; i++) {
    const next = daily[i + 1]
    if (!next || next.date.slice(0, 7) !== daily[i].date.slice(0, 7)) out.push(daily[i])
  }
  return out
}

/** The x-axis ticks for a monthly series: the first point of each year. */
export function yearTicks<T extends { date: string; label: string }>(points: T[]): string[] {
  const ticks: string[] = []
  let year = ''
  for (const p of points) {
    const y = p.date.slice(0, 4)
    if (y !== year) {
      ticks.push(p.label)
      year = y
    }
  }
  return ticks
}
