import { describe, expect, it } from 'vitest'

import { shouldShowPendingBadge } from './transaction-status'

describe('shouldShowPendingBadge', () => {
  it('shows pending for transactions managed by Securo', () => {
    expect(shouldShowPendingBadge({ status: 'pending', source: 'manual' })).toBe(true)
    expect(shouldShowPendingBadge({ status: 'pending', source: 'recurring' })).toBe(true)
  })

  it('shows pending for bank-synced transactions the bank has not settled (fork)', () => {
    expect(shouldShowPendingBadge({ status: 'pending', source: 'sync' })).toBe(true)
  })

  it('hides the badge for posted transactions', () => {
    expect(shouldShowPendingBadge({ status: 'posted', source: 'manual' })).toBe(false)
  })
})
