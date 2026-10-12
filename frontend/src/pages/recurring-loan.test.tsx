import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import RecurringPage from '@/pages/recurring'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Category, RecurringTransaction } from '@/types'

const api = vi.hoisted(() => ({
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  recurring: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), generate: vi.fn() },
  accounts: { list: vi.fn() },
  currencies: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', async () => {
  const { createContext } = await import('react')
  return {
    useWorkspace: () => ({ canWrite: true }),
    WorkspaceContext: createContext(null),
  }
})

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

const accounts = [
  account({ id: 'chk-1', name: 'Everyday' }),
  account({ id: 'loan-1', name: 'Test mortgage', type: 'loan', balance: -300000 }),
  account({ id: 'loan-sync', name: 'Synced mortgage', type: 'loan', connection_id: 'conn-1' }),
  account({ id: 'cc-1', name: 'Card', type: 'credit_card' }),
  account({ id: 'eur-1', name: 'Euro account', currency: 'EUR' }),
]

const categories = [
  category('cat-interest', 'Loan interest'),
  category('cat-transfers', 'Transfers', { treat_as_transfer: true }),
]

beforeEach(() => {
  vi.clearAllMocks()
  api.recurring.list.mockResolvedValue([])
  api.recurring.create.mockResolvedValue({})
  api.categories.list.mockResolvedValue(categories)
  api.categories.listIncludingHidden.mockResolvedValue(categories)
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue(accounts)
  api.currencies.list.mockResolvedValue([{ code: 'USD', symbol: '$', name: 'US Dollar', flag: '' }])
})

async function openLoanPaymentForm() {
  const utils = renderWithProviders(<RecurringPage />)
  await waitFor(() => expect(api.accounts.list).toHaveBeenCalled())
  await utils.user.click(await screen.findByRole('button', { name: t('recurring.add') }))
  const dialog = await screen.findByRole('dialog')
  const kind = await within(dialog).findByLabelText(t('loans.recurring.kind'))
  await utils.user.selectOptions(kind, 'loan_payment')
  return { ...utils, dialog }
}

describe('recurring loan payments', () => {
  it('sends the loan, the paying account and the interest split, never a category', async () => {
    const { user, dialog } = await openLoanPaymentForm()

    await user.selectOptions(within(dialog).getByLabelText(t('loans.recurring.loan')), 'loan-1')
    await user.type(within(dialog).getByRole('textbox'), 'Mortgage payment')
    await user.type(within(dialog).getByLabelText(t('loans.payment.total')), '2000')
    await user.type(within(dialog).getByLabelText(t('loans.payment.interest')), '1200')
    expect(within(dialog).getByTestId('recurring-principal')).toHaveTextContent('$800.00')

    await user.click(within(dialog).getByRole('button', { name: t('loans.payment.interestCategoryPlaceholder') }))
    expect(screen.queryByText('Transfers')).not.toBeInTheDocument()
    await user.click(await screen.findByText('Loan interest'))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.recurring.create).toHaveBeenCalledTimes(1))
    expect(api.recurring.create.mock.calls[0][0]).toMatchObject({
      kind: 'loan_payment',
      description: 'Mortgage payment',
      amount: 2000,
      currency: 'USD',
      type: 'debit',
      category_id: null,
      account_id: 'chk-1',
      transfer_account_id: 'loan-1',
      interest_amount: '1200.00',
      interest_category_id: 'cat-interest',
      auto_generate: true,
    })
  })

  it('offers only accounts that can pay the loan', async () => {
    const { dialog } = await openLoanPaymentForm()

    const paying = within(dialog).getByLabelText(t('loans.payment.payingAccount'))
    expect(within(paying).getAllByRole('option').map((o) => o.textContent)).toEqual(['Everyday'])
  })

  it('refuses interest above the total', async () => {
    const { user, dialog } = await openLoanPaymentForm()

    await user.type(within(dialog).getByRole('textbox'), 'Mortgage payment')
    await user.type(within(dialog).getByLabelText(t('loans.payment.total')), '1000')
    await user.type(within(dialog).getByLabelText(t('loans.payment.interest')), '1200')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    expect(within(dialog).getByRole('alert')).toHaveTextContent(t('loans.payment.interestTooHigh'))
    expect(api.recurring.create).not.toHaveBeenCalled()
  })

  it('makes a schedule on a bank-connected loan projection only', async () => {
    const { user, dialog } = await openLoanPaymentForm()

    await user.selectOptions(within(dialog).getByLabelText(t('loans.recurring.loan')), 'loan-sync')
    const autoGenerate = within(dialog).getByRole('checkbox')
    expect(autoGenerate).toBeDisabled()
    expect(autoGenerate).not.toBeChecked()
    expect(within(dialog).getByText(t('loans.recurring.projectionOnly'))).toBeInTheDocument()

    await user.type(within(dialog).getByRole('textbox'), 'Mortgage payment')
    await user.type(within(dialog).getByLabelText(t('loans.payment.total')), '2000')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.recurring.create).toHaveBeenCalledTimes(1))
    expect(api.recurring.create.mock.calls[0][0]).toMatchObject({
      transfer_account_id: 'loan-sync',
      interest_amount: '0.00',
      interest_category_id: null,
      auto_generate: false,
    })
  })

  it('names the loan and the interest on the schedule row', async () => {
    const schedule: RecurringTransaction = {
      id: 'rec-1',
      user_id: 'user-1',
      account_id: 'chk-1',
      category_id: null,
      description: 'Mortgage payment',
      amount: 2000,
      currency: 'USD',
      type: 'debit',
      frequency: 'monthly',
      weekend_adjustment: 'none',
      day_of_month: null,
      start_date: '2026-03-01',
      end_date: null,
      is_active: true,
      auto_generate: true,
      next_occurrence: '2026-04-01',
      amount_primary: null,
      fx_rate_used: null,
      kind: 'loan_payment',
      transfer_account_id: 'loan-1',
      interest_amount: '1200.00',
      interest_category_id: 'cat-interest',
    }
    api.recurring.list.mockResolvedValue([schedule])
    renderWithProviders(<RecurringPage />)

    expect(
      await screen.findByText(t('loans.recurring.rowNote', { loan: 'Test mortgage', interest: '$1,200.00' })),
    ).toBeInTheDocument()
  })
})
