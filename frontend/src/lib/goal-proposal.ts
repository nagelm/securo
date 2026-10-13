import type { ContributionPeriod, Goal } from '@/types'

/**
 * The POST /api/goals body for an agent's create-goal proposal. The agent
 * tool already proposes exactly that body; proposals saved before it did
 * used `deadline` and `initial_amount`, which are read as `target_date` and
 * `current_amount`.
 */
export function goalProposalPayload(p: Record<string, unknown>): Partial<Goal> {
  const numOrNull = (v: unknown) => (v == null || v === '' ? null : Number(v))
  const accountId = (p.account_id as string) || null
  return {
    name: String(p.name),
    target_amount: numOrNull(p.target_amount),
    contribution_amount: numOrNull(p.contribution_amount),
    contribution_period: ((p.contribution_period as string) || null) as ContributionPeriod | null,
    currency: (p.currency as string) || undefined,
    target_date: ((p.target_date ?? p.deadline) as string) || null,
    tracking_type: accountId ? 'account' : 'manual',
    account_id: accountId,
    current_amount: accountId ? 0 : (numOrNull(p.current_amount ?? p.initial_amount) ?? 0),
    icon: (p.icon as string) || null,
    color: (p.color as string) || null,
  }
}
