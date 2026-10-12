import { describe, expect, it } from 'vitest'

import { loanScheduleProblem, projectedPnlShare } from '@/lib/loan-payments'
import type { ProjectedTransaction } from '@/types'

// Made-up figures: a 2,000 payment of which 1,200 is interest.
const base: ProjectedTransaction = {
  recurring_id: 'rec-1',
  account_id: 'chk-1',
  description: 'Mortgage payment',
  amount: 2000,
  amount_primary: 2400,
  currency: 'EUR',
  type: 'debit',
  date: '2026-03-20',
  category_id: null,
  category_name: null,
  category_icon: null,
  category_color: null,
}

describe('projectedPnlShare', () => {
  it('keeps an ordinary projection as it is', () => {
    expect(projectedPnlShare(base)).toBe(base)
  })

  it('turns a loan payment into its interest, in the interest category', () => {
    const share = projectedPnlShare({
      ...base,
      kind: 'loan_payment',
      transfer_account_id: 'loan-1',
      interest_amount: 1200,
      interest_amount_primary: 1440,
      interest_category_id: 'cat-interest',
      interest_category_name: 'Loan interest',
      interest_category_icon: 'percent',
      interest_category_color: '#f43f5e',
    })
    expect(share).toMatchObject({
      amount: 1200,
      amount_primary: 1440,
      type: 'debit',
      category_id: 'cat-interest',
      category_name: 'Loan interest',
    })
  })

  it('drops a loan payment with no interest: none of it is an expense', () => {
    expect(projectedPnlShare({ ...base, kind: 'loan_payment', interest_amount: 0 })).toBeNull()
    expect(projectedPnlShare({ ...base, kind: 'loan_payment', interest_amount: null })).toBeNull()
  })
})

describe('loanScheduleProblem', () => {
  it('accepts a valid split', () => {
    expect(loanScheduleProblem(200000, 120000, 'cat-interest')).toBeNull()
    expect(loanScheduleProblem(200000, null, '')).toBeNull()
  })

  it('names what is wrong', () => {
    expect(loanScheduleProblem(null, 0, '')).toBe('loans.recurring.totalRequired')
    expect(loanScheduleProblem(200000, -1, '')).toBe('loans.recurring.interestNegative')
    expect(loanScheduleProblem(100000, 120000, 'cat-interest')).toBe('loans.payment.interestTooHigh')
    expect(loanScheduleProblem(200000, 120000, '')).toBe('loans.recurring.interestCategoryRequired')
  })
})
