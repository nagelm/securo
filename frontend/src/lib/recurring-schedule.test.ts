import { describe, expect, it } from 'vitest'

import { advanceDate, nthOccurrence, remainingCount } from './recurring-schedule'

describe('recurring schedule', () => {
  it('steps weeks, fortnights and calendar months like the backend', () => {
    expect(advanceDate('2026-10-16', 'weekly')).toBe('2026-10-23')
    expect(advanceDate('2026-12-28', 'biweekly')).toBe('2027-01-11')
    // A short month clamps; the intended day comes back after it.
    expect(advanceDate('2027-01-31', 'monthly', 31)).toBe('2027-02-28')
    expect(advanceDate('2027-02-28', 'monthly', 31)).toBe('2027-03-31')
    expect(advanceDate('2026-11-30', 'quarterly', 30)).toBe('2027-02-28')
    expect(advanceDate('2026-08-15', 'semiannual')).toBe('2027-02-15')
    expect(advanceDate('2028-02-29', 'yearly', 29)).toBe('2029-02-28')
  })

  it('finds the date of the n-th payment', () => {
    expect(nthOccurrence('2026-10-16', 'weekly', 1)).toBe('2026-10-16')
    expect(nthOccurrence('2026-10-16', 'weekly', 16)).toBe('2027-01-29')
    expect(nthOccurrence('2026-10-31', 'monthly', 4)).toBe('2027-01-31')
  })

  it('counts the payments left until the end', () => {
    const item = {
      start_date: '2026-10-01', next_occurrence: '2026-11-01', end_date: '2027-01-01',
      frequency: 'monthly' as const, day_of_month: null,
    }
    expect(remainingCount(item)).toBe(3)
    expect(remainingCount({ ...item, end_date: null })).toBeNull()
    expect(remainingCount({ ...item, next_occurrence: '2027-02-01' })).toBe(0)
  })
})
