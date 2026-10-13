import { describe, expect, it } from 'vitest'

import {
  contributionTargetLabel,
  periodDaysLeft,
  periodPercentage,
  planAmount,
  planScale,
  planState,
  quickSetupCandidates,
  recentRecord,
  SCALE_MIN_WIDTH,
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

describe('periodDaysLeft', () => {
  it('counts today and stops at zero', () => {
    expect(periodDaysLeft('2026-03-22', new Date(2026, 2, 19, 23, 30))).toBe(4)
    expect(periodDaysLeft('2026-03-22', new Date(2026, 2, 22, 0, 30))).toBe(1)
    expect(periodDaysLeft('2026-03-22', new Date(2026, 2, 23))).toBe(0)
  })
})

describe('recentRecord', () => {
  const period = (
    status: ContributionPeriodResult['status'],
    start: string,
    current = false,
  ): ContributionPeriodResult => ({ start, end: start, contributed: 0, target: 10, status, current })
  const progress = (history: ContributionPeriodResult[]): ContributionProgress => ({
    period: 'weekly',
    target: 10,
    current: history[history.length - 1],
    history,
    streak: 0,
  })

  it('counts met and over among finished periods and leaves the current one out, even once met', () => {
    const record = recentRecord(
      progress([period('met', 'a'), period('short', 'b'), period('over', 'c'), period('met', 'd', true)]),
    )
    expect(record.periods.map((p) => p.start)).toEqual(['a', 'b', 'c'])
    expect([record.met, record.total]).toEqual([2, 3])
  })

  it('keeps only the latest finished periods, oldest first', () => {
    const history = Array.from({ length: 12 }, (_, i) => period(i % 2 ? 'met' : 'short', `p${i}`))
    history.push(period('pending', 'now', true))
    const record = recentRecord(progress(history))
    expect(record.periods.map((p) => p.start)).toEqual(['p2', 'p3', 'p4', 'p5', 'p6', 'p7', 'p8', 'p9', 'p10', 'p11'])
    expect([record.met, record.total]).toEqual([5, 10])
    expect(recentRecord(progress(history), 3).total).toBe(3)
  })

  it('is empty with only the current period', () => {
    expect(recentRecord(progress([period('pending', 'now', true)]))).toEqual({ periods: [], met: 0, total: 0 })
  })
})

describe('planState', () => {
  it('is on plan only when the difference rounds to zero cents', () => {
    expect(planState(40)).toBe('ahead')
    expect(planState(0.01)).toBe('ahead')
    expect(planState(-950)).toBe('behind')
    expect(planState(0)).toBe('on')
    expect(planState(0.004)).toBe('on')
    expect(planState(-0.004)).toBe('on')
  })
})

describe('planScale', () => {
  it('grows right from the plan when ahead, four periods filling the half', () => {
    expect(planScale(40, 20)).toEqual({ state: 'ahead', left: 50, width: 25 })
    expect(planScale(80, 20)).toEqual({ state: 'ahead', left: 50, width: 50 })
  })

  it('grows left from the plan when behind', () => {
    expect(planScale(-20, 20)).toEqual({ state: 'behind', left: 37.5, width: 12.5 })
  })

  it('saturates beyond four periods', () => {
    expect(planScale(-950, 200)).toEqual({ state: 'behind', left: 0, width: 50 })
    expect(planScale(1_000_000, 20)).toEqual({ state: 'ahead', left: 50, width: 50 })
  })

  it('keeps a small difference visible', () => {
    expect(planScale(0.5, 200)).toEqual({ state: 'ahead', left: 50, width: SCALE_MIN_WIDTH })
    expect(planScale(-0.5, 200)).toEqual({ state: 'behind', left: 50 - SCALE_MIN_WIDTH, width: SCALE_MIN_WIDTH })
  })

  it('centres a thin bar on the plan when on plan', () => {
    expect(planScale(0, 20)).toEqual({ state: 'on', left: 50 - SCALE_MIN_WIDTH / 2, width: SCALE_MIN_WIDTH })
  })

  it('saturates without a period target to scale by', () => {
    expect(planScale(5, 0)).toEqual({ state: 'ahead', left: 50, width: 50 })
  })
})

describe('planAmount', () => {
  it('reads whole units from 10 up and cents below, always positive', () => {
    expect(planAmount(40.4, 'USD', 'en-US')).toBe('$40')
    expect(planAmount(-950.6, 'USD', 'en-US')).toBe('$951')
    expect(planAmount(10, 'USD', 'en-US')).toBe('$10')
    expect(planAmount(8.57, 'USD', 'en-US')).toBe('$8.57')
    expect(planAmount(-9.994, 'USD', 'en-US')).toBe('$9.99')
  })

  it('follows the locale and currency', () => {
    expect(planAmount(1234.5, 'EUR', 'de-DE')).toBe('1.235\u00a0€')
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
