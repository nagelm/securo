import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import { LoanPaymentDialog } from '@/components/loan-payment-dialog'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Category, LoanPayment, Transaction } from '@/types'

const api = vi.hoisted(() => ({
  transactions: { list: vi.fn() },
  categories: { create: vi.fn() },
}))
vi.mock('@/lib/api', () => ({ transactions: api.transactions, categories: api.categories }))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

// Made-up figures throughout: a 2,000 payment = 1,200 interest + 800 principal.
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

const loan = account({ id: 'loan-1', name: 'Test mortgage', type: 'loan', balance: -300000, loan_subtype: 'mortgage' })
const checking = account({ id: 'chk-1', name: 'Everyday', balance: 10000 })
const bank = account({ id: 'bank-1', name: 'Synced cheque', connection_id: 'conn-1' })
const accounts = [
  loan,
  checking,
  bank,
  account({ id: 'cc-1', name: 'Card', type: 'credit_card' }),
  account({ id: 'loan-2', name: 'Car loan', type: 'loan' }),
  account({ id: 'eur-1', name: 'Euro account', currency: 'EUR' }),
  account({ id: 'old-1', name: 'Closed account', is_closed: true }),
]

function category(id: string, name: string, overrides: Partial<Category> = {}): Category {
  return {
    id,
    user_id: 'user-1',
    group_id: null,
    name,
    icon: 'circle-help',
    color: '#6366f1',
    is_system: false,
    is_hidden: false,
    treat_as_transfer: false,
    is_ignored: false,
    ...overrides,
  }
}

const categories = [
  category('cat-interest', 'Loan interest'),
  category('cat-transfers', 'Transfers', { treat_as_transfer: true }),
  category('cat-ignored', 'Hidden from reports', { is_ignored: true }),
]

function debit(id: string, amount: number, overrides: Partial<Transaction> = {}): Transaction {
  return {
    id,
    account_id: 'bank-1',
    description: `Bank row ${id}`,
    amount,
    currency: 'USD',
    date: '2026-03-14',
    type: 'debit',
    status: 'posted',
    source: 'sync',
    transfer_pair_id: null,
    ...overrides,
  } as Transaction
}

function renderDialog(props: Partial<Parameters<typeof LoanPaymentDialog>[0]> = {}) {
  const onCreate = vi.fn()
  const onUpdate = vi.fn()
  const utils = renderWithProviders(
    <LoanPaymentDialog
      open
      onClose={vi.fn()}
      loan={loan}
      accounts={accounts}
      categories={categories}
      groups={[]}
      locale="en-US"
      onCreate={onCreate}
      onUpdate={onUpdate}
      loading={false}
      {...props}
    />,
  )
  return { ...utils, onCreate, onUpdate }
}

const totalInput = () => screen.getByLabelText(new RegExp(t('loans.payment.total')))
const interestInput = () => screen.getByLabelText(t('loans.payment.interest'))
const saveButton = () => screen.getByRole('button', { name: t('common.save') })

beforeEach(() => {
  vi.clearAllMocks()
  vi.setSystemTime(new Date('2026-03-15T12:00:00'))
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
})

describe('LoanPaymentDialog', () => {
  it('shows the principal as total minus interest', async () => {
    const { user } = renderDialog()

    await user.type(totalInput(), '2000')
    await user.type(interestInput(), '1200')

    expect(screen.getByTestId('loan-payment-principal')).toHaveTextContent('$800.00')
  })

  it('blocks interest above the total', async () => {
    const { user } = renderDialog()

    await user.type(totalInput(), '1000')
    await user.type(interestInput(), '1200')

    expect(screen.getByRole('alert')).toHaveTextContent(t('loans.payment.interestTooHigh'))
    expect(saveButton()).toBeDisabled()
  })

  it('offers only open accounts in the loan currency that are not cards or loans', () => {
    renderDialog()

    const select = screen.getByLabelText(t('loans.payment.payingAccount'))
    const offered = within(select).getAllByRole('option').map((o) => o.textContent)
    expect(offered).toEqual([t('transactions.account'), 'Everyday (USD)', 'Synced cheque (USD)'])
  })

  it('does not offer transfer-like or ignored categories for the interest', async () => {
    const { user } = renderDialog()

    await user.type(totalInput(), '2000')
    await user.type(interestInput(), '1200')
    await user.click(screen.getByRole('button', { name: t('loans.payment.interestCategoryPlaceholder') }))

    expect(await screen.findByText('Loan interest')).toBeInTheDocument()
    expect(screen.queryByText('Transfers')).not.toBeInTheDocument()
    expect(screen.queryByText('Hidden from reports')).not.toBeInTheDocument()
  })

  it('requires an interest category when there is interest', async () => {
    const { user } = renderDialog()

    await user.type(totalInput(), '2000')
    await user.type(interestInput(), '1200')

    expect(saveButton()).toBeDisabled()
  })

  it('sends a new cash leg with two-decimal amounts', async () => {
    const { user, onCreate } = renderDialog({ defaultInterestCategoryId: 'cat-interest' })

    await user.type(totalInput(), '2000')
    await user.type(interestInput(), '1200')
    await user.type(screen.getByLabelText(t('loans.payment.description')), 'March')
    await user.click(saveButton())

    expect(onCreate).toHaveBeenCalledWith({
      loan_account_id: 'loan-1',
      cash_account_id: 'chk-1',
      date: '2026-03-15',
      total_amount: '2000.00',
      interest_amount: '1200.00',
      interest_category_id: 'cat-interest',
      description: 'March',
    })
  })

  it('needs no category when the payment is all principal', async () => {
    const { user, onCreate } = renderDialog()

    await user.type(totalInput(), '500')
    await user.click(saveButton())

    expect(onCreate).toHaveBeenCalledWith(
      expect.objectContaining({ total_amount: '500.00', interest_amount: '0.00', interest_category_id: null }),
    )
  })

  it('pairs an existing bank debit of the same amount instead of writing a cash leg', async () => {
    api.transactions.list.mockResolvedValue({
      items: [
        debit('tx-match', 2000),
        debit('tx-other-amount', 1999.99),
        debit('tx-paired', 2000, { transfer_pair_id: 'pair-1' }),
      ],
      total: 3,
    })
    const { user, onCreate } = renderDialog({ defaultInterestCategoryId: 'cat-interest' })

    await user.selectOptions(screen.getByLabelText(t('loans.payment.payingAccount')), 'bank-1')
    await user.click(screen.getByRole('radio', { name: t('loans.payment.modeExisting') }))
    await user.type(totalInput(), '2000')
    await user.type(interestInput(), '1200')

    const match = await screen.findByRole('radio', { name: /Bank row tx-match/ })
    expect(screen.queryByText('Bank row tx-other-amount')).not.toBeInTheDocument()
    expect(screen.queryByText('Bank row tx-paired')).not.toBeInTheDocument()
    expect(api.transactions.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        account_id: 'bank-1',
        type: 'debit',
        status: 'posted',
        exclude_transfers: true,
        from: '2026-03-10',
        to: '2026-03-20',
      }),
    )

    expect(saveButton()).toBeDisabled()
    await user.click(match)
    await user.click(saveButton())

    expect(onCreate).toHaveBeenCalledWith({
      loan_account_id: 'loan-1',
      existing_transaction_id: 'tx-match',
      total_amount: '2000.00',
      interest_amount: '1200.00',
      interest_category_id: 'cat-interest',
    })
  })

  it('starts in match mode when the first paying account syncs', () => {
    renderDialog({ accounts: [loan, bank] })

    expect(screen.getByRole('radio', { name: t('loans.payment.modeExisting') })).toHaveAttribute('aria-checked', 'true')
  })

  it('keeps the bank date and total when editing a paired payment', async () => {
    const payment: LoanPayment = {
      id: 'pay-1',
      loan_account_id: 'loan-1',
      cash_account_id: 'bank-1',
      cash_transaction_id: 'tx-match',
      loan_transaction_id: 'tx-loan',
      interest_transaction_id: 'tx-int',
      interest_category_id: 'cat-interest',
      cash_leg_created: false,
      recurring_transaction_id: null,
      date: '2026-03-14',
      description: 'Loan payment',
      currency: 'USD',
      total: 2000,
      interest: 1200,
      principal: 800,
      created_at: '2026-03-14T00:00:00Z',
    }
    const { user, onUpdate } = renderDialog({ payment })

    expect(screen.getByText(t('loans.payment.bankFixedHint'))).toBeInTheDocument()
    expect(totalInput()).toBeDisabled()
    expect(totalInput()).toHaveValue(2000)

    await user.clear(interestInput())
    await user.type(interestInput(), '1100')
    expect(screen.getByTestId('loan-payment-principal')).toHaveTextContent('$900.00')
    await user.click(saveButton())

    await waitFor(() =>
      expect(onUpdate).toHaveBeenCalledWith('pay-1', {
        interest_amount: '1100.00',
        interest_category_id: 'cat-interest',
        description: 'Loan payment',
      }),
    )
  })
})
