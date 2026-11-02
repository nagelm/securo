import { describe, expect, it } from 'vitest'
import { historicTrend } from './trend-line'

describe('historicTrend', () => {
  it('needs two points', () => {
    expect(historicTrend([{ date: '2026-01-01', value: 100 }])).toBeNull()
  })

  it('extends a straight history forward', () => {
    const trend = historicTrend([
      { date: '2026-01-01', value: 1000 },
      { date: '2026-01-11', value: 1100 },
      { date: '2026-01-21', value: 1200 },
    ])!
    expect(trend('2026-01-31')).toBe(1300)
    expect(trend('2026-01-01')).toBe(1000)
  })

  it('fits through noise with least squares', () => {
    const trend = historicTrend([
      { date: '2026-01-01', value: 0 },
      { date: '2026-01-02', value: 10 },
      { date: '2026-01-03', value: 0 },
      { date: '2026-01-04', value: 10 },
    ])!
    // slope 2 a day, through the mean (5 on day 1.5).
    expect(trend('2026-01-05')).toBe(10)
  })
})
