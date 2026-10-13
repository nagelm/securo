import { describe, expect, it } from 'vitest'

import i18next from 'i18next'

import en from '@/locales/en.json'
import {
  formToLoanDetails,
  isRevolvingLoan,
  loanDetailRows,
  loanDetailsToForm,
  revolvingLimit,
  type LoanDetailFormatters,
} from '@/lib/loan-details'

// The real English strings; a node suite can't load the app's i18n (it
// touches the document).
const i18n = i18next.createInstance()
await i18n.init({ lng: 'en', resources: { en: { translation: en } }, interpolation: { escapeValue: false } })
const t = (key: string, options?: Record<string, unknown>) => i18n.t(key, options) as string

// Made-up loan figures.
const formatters: LoanDetailFormatters = {
  t,
  money: (value) => `$${value.toFixed(2)}`,
  date: (value) => `<${value}>`,
  locale: 'en-US',
}

const bankDetails = {
  purpose: 'HOME',
  type: 'TABLE',
  interest: { rate: 5.25, type: 'FIXED', expires_at: '2027-06-01T00:00:00Z' },
  is_interest_only: false,
  term: { years: 25, months: 1 },
  matures_at: '2049-01-01T00:00:00Z',
  initial_principal: 350000,
  repayment: { frequency: 'MONTHLY', next_date: '2026-11-01T00:00:00Z', next_amount: 2000 },
}

describe('loanDetailRows', () => {
  it('labels and formats every known detail, in a fixed order', () => {
    const rows = loanDetailRows(bankDetails, formatters).map((r) => [r.label, r.value])
    expect(rows).toEqual([
      ['Purpose', 'Home'],
      ['Repayment type', 'Table (equal repayments)'],
      ['Interest rate', '5.25%'],
      ['Rate type', 'Fixed'],
      ['Rate fixed until', '<2027-06-01>'],
      ['Interest only', 'No'],
      ['Original loan amount', '$350000.00'],
      ['Term', '25 years, 1 month'],
      ['Ends on', '<2049-01-01>'],
      ['Repayment amount', '$2000.00'],
      ['Repayment frequency', 'Monthly'],
      ['Next repayment', '<2026-11-01>'],
    ])
  })

  it('shows unknown values as sent, and other keys with a humanised label', () => {
    const rows = loanDetailRows(
      { purpose: 'FARM', term: '30Y', interest: { rate: 4, margin: 1.5 }, lender_note: 'n/a', nested: { a: {} } },
      formatters,
    ).map((r) => [r.label, r.value])
    expect(rows).toEqual([
      ['Purpose', 'FARM'],
      ['Interest rate', '4%'],
      ['Term', '30Y'],
      ['Interest margin', '1.5'],
      ['Lender note', 'n/a'],
    ])
  })

  it('is empty without details', () => {
    expect(loanDetailRows(null, formatters)).toEqual([])
    expect(loanDetailRows({}, formatters)).toEqual([])
  })
})

describe('loan details form', () => {
  it('prefills from bank details and saves them back in the same shape, dates as days', () => {
    const form = loanDetailsToForm(bankDetails, 'en-US')
    expect(form.rate).toBe('5.25')
    expect(form.rateExpiresAt).toBe('2027-06-01')
    expect(form.termYears).toBe('25')

    const { details, errors } = formToLoanDetails(form, 'en-US')
    expect(errors).toEqual([])
    expect(details).toEqual({
      purpose: 'HOME',
      type: 'TABLE',
      interest: { rate: 5.25, type: 'FIXED', expires_at: '2027-06-01' },
      term: { years: 25, months: 1 },
      matures_at: '2049-01-01',
      initial_principal: 350000,
      repayment: { frequency: 'MONTHLY', next_date: '2026-11-01', next_amount: 2000 },
    })
  })

  it('reads numbers in the display locale', () => {
    const form = { ...loanDetailsToForm(null, 'de-DE'), rate: '4,75', initialPrincipal: '120.000,50' }
    expect(formToLoanDetails(form, 'de-DE').details).toEqual({
      interest: { rate: 4.75 },
      initial_principal: 120000.5,
    })
  })

  it('keeps the interest-only end date only while interest only is on', () => {
    const base = { ...loanDetailsToForm(null, 'en-US'), interestOnlyExpiresAt: '2027-01-01' }
    expect(formToLoanDetails(base, 'en-US').details).toBeNull()
    expect(formToLoanDetails({ ...base, isInterestOnly: true }, 'en-US').details).toEqual({
      is_interest_only: true,
      interest_only_expires_at: '2027-01-01',
    })
  })

  it('an empty form clears the details', () => {
    expect(formToLoanDetails(loanDetailsToForm(null, 'en-US'), 'en-US')).toEqual({ details: null, errors: [] })
  })

  it('flags values that do not parse', () => {
    const form = {
      ...loanDetailsToForm(null, 'en-US'),
      rate: '120',
      termYears: '2.5',
      termMonths: 'six',
      initialPrincipal: '-5',
      repaymentAmount: 'abc',
    }
    expect(formToLoanDetails(form, 'en-US').errors).toEqual([
      'rate',
      'termYears',
      'termMonths',
      'initialPrincipal',
      'repaymentAmount',
    ])
  })

  it('leaves bank values the form cannot hold blank', () => {
    const form = loanDetailsToForm({ purpose: 'FARM', term: '30Y', repayment: { frequency: 'DAILY' } }, 'en-US')
    expect(form.purpose).toBe('')
    expect(form.termYears).toBe('')
    expect(form.repaymentFrequency).toBe('')
  })
})

describe('revolving loans', () => {
  // Made-up figures: a 10,000 line with 4,000 drawn.
  const line = {
    type: 'loan',
    loan_details: { type: 'revolving' },
    credit_limit: 10000,
    available_credit: 6000,
    current_balance: -4000,
  }

  it('is revolving only for a loan whose type is REVOLVING', () => {
    expect(isRevolvingLoan(line)).toBe(true)
    expect(isRevolvingLoan({ ...line, loan_details: { type: 'TABLE' } })).toBe(false)
    expect(isRevolvingLoan({ ...line, loan_details: null })).toBe(false)
    expect(isRevolvingLoan({ ...line, type: 'credit_card' })).toBe(false)
  })

  it('reads limit, owed and available', () => {
    expect(revolvingLimit(line)).toEqual({ limit: 10000, owed: 4000, available: 6000 })
  })

  it('owes nothing when the loan is in credit, and never shows negative available', () => {
    expect(revolvingLimit({ ...line, current_balance: 250, available_credit: null })).toEqual({
      limit: 10000,
      owed: 0,
      available: 10000,
    })
    expect(revolvingLimit({ ...line, current_balance: -12000, available_credit: null })?.available).toBe(0)
  })

  it('has no limit to show without one, or when the loan is not revolving', () => {
    expect(revolvingLimit({ ...line, credit_limit: null })).toBeNull()
    expect(revolvingLimit({ ...line, loan_details: { type: 'REDUCING' } })).toBeNull()
  })
})
