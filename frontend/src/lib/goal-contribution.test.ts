import { describe, expect, it } from 'vitest'

import {
  contributionTargetLabel,
  periodDaysLeft,
  periodElapsedFraction,
  periodPercentage,
  periodsMet,
  quickSetupCandidates,
} from '@/lib/goal-contribution'
import type { Account, ContributionPeriodResult, ContributionProgress, Goal } from '@/types'

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

describe('periodElapsedFraction', () => {
  // A Monday-to-Sunday week.
  const start = '2026-03-16'
  const end = '2026-03-22'

  it('counts today as gone, both ends inclusive', () => {
    expect(periodElapsedFraction(start, end, new Date(2026, 2, 16, 9, 0))).toBeCloseTo(1 / 7)
    expect(periodElapsedFraction(start, end, new Date(2026, 2, 19, 12, 0))).toBeCloseTo(4 / 7)
    expect(periodElapsedFraction(start, end, new Date(2026, 2, 22, 18, 0))).toBe(1)
  })

  it('reads the local calendar date at either end of the day', () => {
    expect(periodElapsedFraction(start, end, new Date(2026, 2, 19, 0, 30))).toBeCloseTo(4 / 7)
    expect(periodElapsedFraction(start, end, new Date(2026, 2, 19, 23, 30))).toBeCloseTo(4 / 7)
  })

  it('is clamped outside the period', () => {
    expect(periodElapsedFraction(start, end, new Date(2026, 2, 10))).toBe(0)
    expect(periodElapsedFraction(start, end, new Date(2026, 2, 30))).toBe(1)
  })

  it('works for a month', () => {
    expect(periodElapsedFraction('2026-02-01', '2026-02-28', new Date(2026, 1, 14))).toBe(0.5)
  })
})

describe('periodDaysLeft', () => {
  it('counts today and stops at zero', () => {
    expect(periodDaysLeft('2026-03-22', new Date(2026, 2, 19, 23, 30))).toBe(4)
    expect(periodDaysLeft('2026-03-22', new Date(2026, 2, 22, 0, 30))).toBe(1)
    expect(periodDaysLeft('2026-03-22', new Date(2026, 2, 23))).toBe(0)
  })
})

describe('periodsMet', () => {
  const period = (status: ContributionPeriodResult['status'], current = false): ContributionPeriodResult => ({
    start: '2026-01-01',
    end: '2026-01-07',
    contributed: 0,
    target: 10,
    status,
    current,
  })
  const progress = (history: ContributionPeriodResult[]): ContributionProgress => ({
    period: 'weekly',
    target: 10,
    current: history[history.length - 1],
    history,
    streak: 0,
  })

  it('counts met and over among finished periods, leaving out a current one still in progress', () => {
    expect(periodsMet(progress([period('met'), period('short'), period('over'), period('pending', true)]))).toEqual({
      met: 2,
      total: 3,
    })
  })

  it('counts the current period once it is met', () => {
    expect(periodsMet(progress([period('short'), period('met', true)]))).toEqual({ met: 1, total: 2 })
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
