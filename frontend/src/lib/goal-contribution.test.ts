import { describe, expect, it } from 'vitest'

import { contributionTargetLabel, periodPercentage, quickSetupCandidates } from '@/lib/goal-contribution'
import type { Account, Goal } from '@/types'

const acc = (id: string, name: string, type: string, extra: Partial<Account> = {}) =>
  ({ id, name, display_name: null, type, is_closed: false, ...extra }) as Account

describe('periodPercentage', () => {
  it('is the share of the target, clamped to 0..100', () => {
    expect(periodPercentage(30, 50)).toBe(60)
    expect(periodPercentage(80, 50)).toBe(100)
    expect(periodPercentage(-10, 50)).toBe(0)
    expect(periodPercentage(5, 0)).toBe(100)
    expect(periodPercentage(0, 0)).toBe(0)
  })
})

describe('contributionTargetLabel', () => {
  it('joins the amount and the period', () => {
    const t = (key: string) => ({ 'goals.savings.per.biweekly': '/ fortnight' })[key] ?? key
    expect(contributionTargetLabel(12, 'biweekly', (n) => `$${n}`, t)).toBe('$12 / fortnight')
  })
})

describe('quickSetupCandidates', () => {
  it('offers open, non-card, non-loan accounts without a goal, savings first', () => {
    const accounts = [
      acc('a', 'Zeta savings', 'savings'),
      acc('b', 'Alpha checking', 'checking'),
      acc('c', 'Beta savings', 'savings'),
      acc('d', 'Card', 'credit_card'),
      acc('e', 'Mortgage', 'loan'),
      acc('f', 'Closed savings', 'savings', { is_closed: true }),
      acc('g', 'Linked savings', 'savings'),
    ]
    const goals = [{ account_id: 'g' }, { account_id: null }] as Goal[]
    expect(quickSetupCandidates(accounts, goals).map((a) => a.id)).toEqual(['c', 'a', 'b'])
  })
})
