/**
 * Fork: a recurring item's nominal payment dates, as the backend steps them
 * (recurring_transaction_service._advance_date): weeks for weekly and
 * fortnightly, calendar months otherwise, the intended day clamped only in
 * a shorter month. Dates are `"YYYY-MM-DD"` strings, timezone-naive.
 */
import type { RecurringTransaction } from '@/types'

type Frequency = RecurringTransaction['frequency']

const MONTHS: Partial<Record<Frequency, number>> = { monthly: 1, quarterly: 3, semiannual: 6, yearly: 12 }

function parse(iso: string): [number, number, number] {
  const [y, m, d] = iso.split('-').map(Number)
  return [y, m, d]
}

function format(y: number, m: number, d: number): string {
  return `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
}

/** The next nominal date after `iso`. */
export function advanceDate(iso: string, frequency: Frequency, intendedDay?: number | null): string {
  const [y, m, d] = parse(iso)
  if (frequency === 'weekly' || frequency === 'biweekly') {
    // Noon UTC, so adding days never crosses a daylight-saving change.
    const next = new Date(Date.UTC(y, m - 1, d, 12) + (frequency === 'weekly' ? 7 : 14) * 86_400_000)
    return format(next.getUTCFullYear(), next.getUTCMonth() + 1, next.getUTCDate())
  }
  const index = m - 1 + (MONTHS[frequency] ?? 1)
  const year = y + Math.floor(index / 12)
  const month = (index % 12) + 1
  const last = new Date(Date.UTC(year, month, 0)).getUTCDate()
  return format(year, month, Math.min(intendedDay || d, last))
}

/** The date of the `count`-th payment from `start` (the first is `start`). */
export function nthOccurrence(start: string, frequency: Frequency, count: number, dayOfMonth?: number | null): string {
  const day = dayOfMonth || parse(start)[2]
  let current = start
  for (let n = 1; n < count; n++) current = advanceDate(current, frequency, day)
  return current
}

/**
 * How many payments are still to come: from `next` (the next occurrence)
 * through `end`, inclusive. Null when the item has no end.
 */
export function remainingCount(
  item: Pick<RecurringTransaction, 'next_occurrence' | 'end_date' | 'frequency' | 'day_of_month' | 'start_date'>,
): number | null {
  if (!item.end_date) return null
  const day = item.day_of_month || parse(item.start_date)[2]
  let count = 0
  let current = item.next_occurrence
  while (current <= item.end_date && count < 10_000) {
    count++
    current = advanceDate(current, item.frequency, day)
  }
  return count
}
