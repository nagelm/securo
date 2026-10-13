import { sortAccountsByDisplayName } from '@/lib/account-utils'
import { formatCurrency } from '@/lib/format'
import type { Account, ContributionPeriod, ContributionPeriodResult, ContributionProgress, Goal } from '@/types'

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

/** Days left in a period, today included; 0 once it has ended. */
export function periodDaysLeft(end: string, today: Date): number {
  return Math.max(0, isoDay(end) - localDay(today) + 1)
}

/** How many finished periods the record shows, as dots. */
export const RECORD_PERIODS = 10

/** A period that reached its target. */
export function isMet(status: ContributionPeriodResult['status']): boolean {
  return status === 'met' || status === 'over'
}

/** The last finished periods, oldest first and at most `max` of them, and
 * how many reached their target. The current period is left out: it can
 * still go either way. */
export function recentRecord(
  progress: ContributionProgress,
  max: number = RECORD_PERIODS,
): { periods: ContributionPeriodResult[]; met: number; total: number } {
  const periods = progress.history.filter((p) => !p.current).slice(-max)
  return { periods, met: periods.filter((p) => isMet(p.status)).length, total: periods.length }
}

/** How many periods' target fills one half of the ahead/behind scale. */
export const SCALE_PERIODS = 4

/** Width of the bar, in percent of the track, when it would be thinner. */
export const SCALE_MIN_WIDTH = 2

export type PlanState = 'ahead' | 'behind' | 'on'

/** Ahead, behind, or on plan: on plan when the difference rounds to zero cents. */
export function planState(aheadBy: number): PlanState {
  const cents = Math.round(aheadBy * 100)
  if (cents > 0) return 'ahead'
  if (cents < 0) return 'behind'
  return 'on'
}

/** Where the bar sits on the ahead/behind scale, in percent of the track. The
 * plan is the middle; the bar grows right when ahead and left when behind,
 * `SCALE_PERIODS` periods' target filling a half and anything beyond it
 * saturating. On plan, a thin bar sits centred on the plan. */
export function planScale(
  aheadBy: number,
  periodTarget: number,
): { state: PlanState; left: number; width: number } {
  const state = planState(aheadBy)
  if (state === 'on') return { state, left: 50 - SCALE_MIN_WIDTH / 2, width: SCALE_MIN_WIDTH }
  const half = periodTarget * SCALE_PERIODS
  const share = half > 0 ? Math.min(1, Math.abs(aheadBy) / half) : 1
  const width = Math.max(SCALE_MIN_WIDTH, share * 50)
  return { state, left: state === 'ahead' ? 50 : 50 - width, width }
}

/** The ahead/behind amount for its label: whole units from 10 up ("$40"),
 * cents below ("$8.57"). Always positive; the label says which way. */
export function planAmount(aheadBy: number, currency: string, locale: string): string {
  const amount = Math.abs(aheadBy)
  if (amount < 10) return formatCurrency(amount, currency, locale)
  try {
    return new Intl.NumberFormat(locale, {
      style: 'currency',
      currency,
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    }).format(amount)
  } catch {
    return formatCurrency(Math.round(amount), currency, locale)
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
