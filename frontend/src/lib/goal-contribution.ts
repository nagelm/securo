import { sortAccountsByDisplayName } from '@/lib/account-utils'
import type { Account, ContributionPeriod, Goal } from '@/types'

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
