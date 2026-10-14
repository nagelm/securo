import { describe, expect, it } from 'vitest'
import { moneyMapWindow } from './money-map-window'

const today = new Date(2026, 9, 15) // 15 Oct 2026

describe('moneyMapWindow', () => {
  it('is the normal window at the right-hand end', () => {
    expect(moneyMapWindow(0, { months: 12 }, today)).toBeNull()
  })

  it('moves a whole-month window back, keeping its size', () => {
    expect(moneyMapWindow(1, { months: 12 }, today)).toEqual({ start: '2025-10-01', end: '2026-09-30' })
    expect(moneyMapWindow(7, { months: 3 }, today)).toEqual({ start: '2026-01-01', end: '2026-03-31' })
  })

  it('keeps a day window to that many days', () => {
    expect(moneyMapWindow(2, { months: 1, days: 30 }, today)).toEqual({ start: '2026-08-02', end: '2026-08-31' })
  })

  it('runs YTD from 1 January of the window end', () => {
    expect(moneyMapWindow(14, { months: 12, period: 'ytd' }, today)).toEqual({ start: '2025-01-01', end: '2025-08-31' })
  })
})
