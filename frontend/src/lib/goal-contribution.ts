import { sortAccountsByDisplayName } from '@/lib/account-utils'
import type { Account, ContributionPeriod, ContributionProgress, Goal } from '@/types'

type Translate = (key: string, options?: Record<string, unknown>) => string

export const CONTRIBUTION_PERIODS: ContributionPeriod[] = ['weekly', 'biweekly', 'monthly']

/** "$50 / week" */
export function contributionTargetLabel(
  amount: number,
  period: ContributionPeriod,
  money: (amount: number) => string,
  t: Translate,
): string {
  return `${money(amount)} ${t(`goals.savings.per.${period}`)}`
}

/** Share of the current period's target already contributed, 0-100. */
export function periodPercentage(contributed: number, target: number): number {
  if (target <= 0) return contributed > 0 ? 100 : 0
  return Math.max(0, Math.min(100, (contributed / target) * 100))
}

/** Day number of an ISO date ("2026-03-05"), independent of the time zone. */
function isoDay(iso: string): number {
  const [y, m, d] = iso.split('-').map(Number)
  return Date.UTC(y, m - 1, d) / 86_400_000
}

/** Day number of a moment's local calendar date, so late evening is not
 * pushed into tomorrow by the UTC offset. */
function localDay(moment: Date): number {
  return Date.UTC(moment.getFullYear(), moment.getMonth(), moment.getDate()) / 86_400_000
}

/** Share of a period gone by the end of today, 0-1. Both ends are inclusive:
 * on the first day of a week 1/7 has gone, on the last day all of it. */
export function periodElapsedFraction(start: string, end: string, today: Date): number {
  const first = isoDay(start)
  const days = isoDay(end) - first + 1
  if (days <= 0) return 1
  return Math.max(0, Math.min(1, (localDay(today) - first + 1) / days))
}

/** Days left in a period, today included; 0 once it has ended. */
export function periodDaysLeft(end: string, today: Date): number {
  return Math.max(0, isoDay(end) - localDay(today) + 1)
}

/** How many recent periods reached their target. The current period counts
 * only once it has: until then it can still go either way. */
export function periodsMet(progress: ContributionProgress): { met: number; total: number } {
  const counted = progress.history.filter((p) => !p.current || p.status === 'met' || p.status === 'over')
  return {
    met: counted.filter((p) => p.status === 'met' || p.status === 'over').length,
    total: counted.length,
  }
}

/** Accounts a savings goal can sit on: open, not a card or a loan, not yet
 * linked to a goal. Savings accounts first. */
export function quickSetupCandidates(accounts: Account[], goals: Goal[]): Account[] {
  const linked = new Set(goals.map((g) => g.account_id).filter(Boolean))
  const open = accounts.filter(
    (a) => !a.is_closed && a.type !== 'credit_card' && a.type !== 'loan' && !linked.has(a.id),
  )
  const sorted = sortAccountsByDisplayName(open)
  return [...sorted.filter((a) => a.type === 'savings'), ...sorted.filter((a) => a.type !== 'savings')]
}
