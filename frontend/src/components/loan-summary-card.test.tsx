import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { LoanSummaryCard } from '@/components/loan-summary-card'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Asset, LoanPayment, LoanSummary } from '@/types'

const api = vi.hoisted(() => ({
  accounts: { loanSummary: vi.fn() },
  assets: { get: vi.fn() },
  categories: { list: vi.fn(), create: vi.fn() },
  categoryGroups: { list: vi.fn() },
  loanPayments: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  transactions: { list: vi.fn() },
}))
vi.mock('@/lib/api', () => api)
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

// Made-up figures: house 500,000, mortgage owing 300,000, one 2,000 payment
// of 1,200 interest + 800 principal.
function account(overrides: Partial<Account>): Account {
  return {
    id: 'acc',
    user_id: 'user-1',
    connection_id: null,
    external_id: null,
    name: 'Account',
    display_name: null,
    masked_number: null,
    institution_name: null,
    institution_logo_url: null,
    type: 'checking',
    balance: 0,
    current_balance: 0,
    previous_balance: null,
    balance_primary: null,
    currency: 'USD',
    credit_limit: null,
    available_credit: null,
    statement_close_day: null,
    payment_due_day: null,
    next_close_date: null,
    next_due_date: null,
    minimum_payment: null,
    card_brand: null,
    card_level: null,
    shared_balance_group: null,
    loan_subtype: null,
    secured_asset_id: null,
    loan_details: null,
    is_closed: false,
    closed_at: null,
    ...overrides,
  }
}

const loan = account({
  id: 'loan-1',
  name: 'Test mortgage',
  type: 'loan',
  balance: -299200,
  loan_subtype: 'mortgage',
  secured_asset_id: 'asset-1',
})
const checking = account({ id: 'chk-1', name: 'Everyday', balance: 8000 })

function summary(overrides: Partial<LoanSummary> = {}): LoanSummary {
  // April 2025 .. March 2026, activity only in the last month.
  const months = Array.from({ length: 12 }, (_, i) => {
    const d = new Date(2025, 3 + i, 1)
    return {
      month: `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`,
      paid: 0,
      interest: 0,
      principal_reduction: 0,
    }
  })
  months[11] = { month: '2026-03', paid: 2000, interest: 1200, principal_reduction: 800 }
  return {
    account_id: 'loan-1',
    currency: 'USD',
    manual_payments_allowed: true,
    months,
    total_paid: 2000,
    total_interest: 1200,
    total_principal_reduction: 800,
    ...overrides,
  }
}

const payment: LoanPayment = {
  id: 'pay-1',
  loan_account_id: 'loan-1',
  cash_account_id: 'chk-1',
  cash_transaction_id: 'tx-cash',
  loan_transaction_id: 'tx-loan',
  interest_transaction_id: 'tx-int',
  interest_category_id: 'cat-interest',
  cash_leg_created: true,
  recurring_transaction_id: null,
  date: '2026-03-15',
  description: 'March payment',
  currency: 'USD',
  total: 2000,
  interest: 1200,
  principal: 800,
  created_at: '2026-03-15T00:00:00Z',
}

const house = {
  id: 'asset-1',
  name: 'Test house',
  currency: 'USD',
  current_value: 500000,
  linked_loans: [{ id: 'loan-1', name: 'Test mortgage', currency: 'USD', balance: -299200, debt: 299200 }],
  linked_loan_ids: ['loan-1'],
  linked_debt: 299200,
  equity: 200800,
} as unknown as Asset

function renderCard(acc: Account = loan, canWrite = true) {
  return renderWithProviders(
    <LoanSummaryCard account={acc} accounts={[acc, checking]} locale="en-US" canWrite={canWrite} />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-03-20T12:00:00'))
  api.accounts.loanSummary.mockResolvedValue(summary())
  api.loanPayments.list.mockResolvedValue([payment])
  api.assets.get.mockResolvedValue(house)
  api.categories.list.mockResolvedValue([
    {
      id: 'cat-interest', user_id: 'user-1', group_id: null, name: 'Loan interest', icon: 'circle-help',
      color: '#6366f1', is_system: false, is_hidden: false, treat_as_transfer: false, is_ignored: false,
    },
  ])
  api.categoryGroups.list.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
})

describe('LoanSummaryCard', () => {
  it('shows paid, interest and principal for months with activity, with totals', async () => {
    renderCard()

    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row')
    // Header, one active month, totals.
    expect(rows).toHaveLength(3)
    expect(rows[1]).toHaveTextContent(/Mar 2026/i)
    expect(rows[1]).toHaveTextContent('$2,000.00')
    expect(rows[1]).toHaveTextContent('$1,200.00')
    expect(rows[1]).toHaveTextContent('$800.00')
    expect(rows[2]).toHaveTextContent(t('loans.summary.total'))
  })

  it('names the subtype and the asset the loan is secured against, with its equity', async () => {
    renderCard()

    expect(screen.getByText(t('accounts.loan.subtypes.mortgage'))).toBeInTheDocument()
    const secured = await screen.findByTestId('loan-secured-asset')
    expect(secured).toHaveTextContent(t('loans.summary.securedAgainst'))
    expect(within(secured).getByRole('link', { name: 'Test house' })).toHaveAttribute('href', '/assets')
    expect(secured).toHaveTextContent(`${t('loans.summary.equity')} $200,800.00`)
    expect(api.assets.get).toHaveBeenCalledWith('asset-1')
  })

  it('lists recorded payments and offers "Record payment" on a manual loan', async () => {
    renderCard()

    const row = await screen.findByTestId('loan-payment-row')
    expect(row).toHaveTextContent('March payment')
    expect(row).toHaveTextContent('$2,000.00')
    expect(await screen.findByRole('button', { name: t('loans.payment.record') })).toBeInTheDocument()
  })

  it('never offers "Record payment" on a synced loan, and says why', async () => {
    api.accounts.loanSummary.mockResolvedValue(summary({ manual_payments_allowed: false }))
    api.loanPayments.list.mockResolvedValue([])
    renderCard(account({ ...loan, connection_id: 'conn-1' }))

    expect(await screen.findByText(t('loans.summary.syncedNote'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('loans.payment.record') })).not.toBeInTheDocument()
  })

  it('hides the write actions from a viewer', async () => {
    renderCard(loan, false)

    await screen.findByTestId('loan-payment-row')
    expect(screen.queryByRole('button', { name: t('loans.payment.record') })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('loans.payment.delete') })).not.toBeInTheDocument()
  })

  it('shows the provider loan facts', async () => {
    renderCard(account({ ...loan, loan_details: { interest_rate: 5.25, next_repayment: { amount: 2000, date: '2026-04-15' } } }))

    const details = screen.getByLabelText(t('loans.summary.providerDetails'))
    expect(details).toHaveTextContent('Interest rate5.25')
    expect(details).toHaveTextContent('Next repayment amount2000')
    expect(details).toHaveTextContent('Next repayment date2026-04-15')
  })

  it('records a payment and refreshes the summary', async () => {
    api.loanPayments.create.mockResolvedValue(payment)
    const { user } = renderCard()

    await user.click(await screen.findByRole('button', { name: t('loans.payment.record') }))
    await user.type(screen.getByLabelText(new RegExp(t('loans.payment.total'))), '2000')
    await user.type(screen.getByLabelText(t('loans.payment.interest')), '1200')
    // The category last used on this loan is preselected.
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() =>
      expect(api.loanPayments.create).toHaveBeenCalledWith(
        expect.objectContaining({
          loan_account_id: 'loan-1',
          cash_account_id: 'chk-1',
          total_amount: '2000.00',
          interest_amount: '1200.00',
          interest_category_id: 'cat-interest',
        }),
      ),
    )
    await waitFor(() => expect(api.accounts.loanSummary).toHaveBeenCalledTimes(2))
    expect(api.loanPayments.list).toHaveBeenCalledTimes(2)
  })

  it('edits a payment', async () => {
    api.loanPayments.update.mockResolvedValue({ ...payment, interest: 1100, principal: 900 })
    const { user } = renderCard()

    await user.click(await screen.findByRole('button', { name: t('loans.payment.edit') }))
    const interest = screen.getByLabelText(t('loans.payment.interest'))
    await user.clear(interest)
    await user.type(interest, '1100')
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() =>
      expect(api.loanPayments.update).toHaveBeenCalledWith('pay-1', {
        date: '2026-03-15',
        total_amount: '2000.00',
        interest_amount: '1100.00',
        interest_category_id: 'cat-interest',
        description: 'March payment',
      }),
    )
  })

  it('deletes a payment after confirmation', async () => {
    api.loanPayments.delete.mockResolvedValue(undefined)
    const { user } = renderCard()

    await user.click(await screen.findByRole('button', { name: t('loans.payment.delete') }))
    expect(screen.getByText(t('loans.payment.deleteDescription'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('common.delete') }))

    await waitFor(() => expect(api.loanPayments.delete).toHaveBeenCalledWith('pay-1'))
    await waitFor(() => expect(api.accounts.loanSummary).toHaveBeenCalledTimes(2))
  })
  it('labels and formats the loan details: rate as a percent, amounts as money, dates as dates', async () => {
    renderCard(account({
      ...loan,
      loan_details: {
        interest: { rate: 5.25, type: 'FIXED', expires_at: '2027-06-01T00:00:00Z' },
        initial_principal: 350000,
        term: { years: 25 },
        repayment: { frequency: 'FORTNIGHTLY', next_date: '2026-04-15', next_amount: 950.5 },
      },
    }))

    const details = screen.getByLabelText(t('loans.summary.providerDetails'))
    expect(details).toHaveTextContent(`${t('loans.details.interestRate')}5.25%`)
    expect(details).toHaveTextContent(`${t('loans.details.interestType')}${t('loans.details.interestTypes.FIXED')}`)
    expect(details).toHaveTextContent(`${t('loans.details.rateExpiresAt')}6/1/2027`)
    expect(details).toHaveTextContent(`${t('loans.details.initialPrincipal')}$350,000.00`)
    expect(details).toHaveTextContent(`${t('loans.details.term')}25 years`)
    expect(details).toHaveTextContent(`${t('loans.details.repaymentAmount')}$950.50`)
    expect(details).toHaveTextContent(
      `${t('loans.details.repaymentFrequency')}${t('loans.details.frequencies.FORTNIGHTLY')}`,
    )
    expect(details).toHaveTextContent(`${t('loans.details.nextRepaymentDate')}4/15/2026`)
  })
})
