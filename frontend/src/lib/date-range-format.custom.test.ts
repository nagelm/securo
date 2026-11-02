import { describe, expect, it } from 'vitest'

import { normalizeCustomRange } from '@/lib/date-range-format'

describe('normalizeCustomRange', () => {
  it('keeps a from-only range open-ended', () => {
    expect(normalizeCustomRange('2026-01-01', '')).toEqual(['2026-01-01', ''])
  })

  it('keeps a to-only range open-ended', () => {
    expect(normalizeCustomRange('', '2026-03-31')).toEqual(['', '2026-03-31'])
  })

  it('swaps ends picked in reverse', () => {
    expect(normalizeCustomRange('2026-03-31', '2026-01-01')).toEqual(['2026-01-01', '2026-03-31'])
  })
})
