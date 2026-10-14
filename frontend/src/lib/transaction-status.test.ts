import { describe, expect, it } from 'vitest'

import { isUnsettled, shouldShowPendingBadge } from './transaction-status'

describe('shouldShowPendingBadge', () => {
  it('shows pending for transactions managed by Securo', () => {
    expect(shouldShowPendingBadge({ status: 'pending', source: 'manual' })).toBe(true)
    expect(shouldShowPendingBadge({ status: 'pending', source: 'recurring' })).toBe(true)
  })

  it('hides pending for bank-synced transactions', () => {
    expect(shouldShowPendingBadge({ status: 'pending', source: 'sync' })).toBe(false)
  })

  it('hides the badge for posted transactions', () => {
    expect(shouldShowPendingBadge({ status: 'posted', source: 'manual' })).toBe(false)
  })
})

describe('isUnsettled (fork)', () => {
  it('marks bank-synced rows the bank has not settled', () => {
    expect(isUnsettled({ status: 'pending', source: 'sync' })).toBe(true)
  })

  it('leaves settled rows and Securo-scheduled rows alone', () => {
    expect(isUnsettled({ status: 'posted', source: 'sync' })).toBe(false)
    expect(isUnsettled({ status: 'pending', source: 'manual' })).toBe(false)
  })
})
