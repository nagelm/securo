import { localDateString } from '@/lib/date-utils'

/**
 * Fork (owner 2026-10-15): the Money Map slider moves a window of the selected
 * size back through time. `monthsBack` 0 is the normal, current window; n
 * ends the window on the last day of the month n months ago.
 *
 * - a day range (30D) is that many days ending there;
 * - YTD is 1 January of that year to the end;
 * - a month range (3M .. 10Y) is that many whole months ending there.
 */
export function moneyMapWindow(
  monthsBack: number,
  range: { months: number; period?: 'ytd'; days?: number },
  today: Date = new Date(),
): { start: string; end: string } | null {
  if (monthsBack <= 0) return null
  const end = new Date(today.getFullYear(), today.getMonth() - monthsBack + 1, 0)
  let start: Date
  if (range.days) {
    start = new Date(end.getFullYear(), end.getMonth(), end.getDate() - range.days + 1)
  } else if (range.period === 'ytd') {
    start = new Date(end.getFullYear(), 0, 1)
  } else {
    start = new Date(end.getFullYear(), end.getMonth() - range.months + 1, 1)
  }
  return { start: localDateString(start), end: localDateString(end) }
}

/** How far back the slider reaches, in months. */
export const MONEY_MAP_MAX_MONTHS_BACK = 120
