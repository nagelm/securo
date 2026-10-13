/**
 * The loan block on an account page: present for loans only, and without the
 * "Record payment" form when the loan syncs its own payments.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'

import AccountDetailPage from '@/pages/account-detail'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  accounts: {
    get: vi.fn(),
    bills: vi.fn(),
    summary: vi.fn(),
    list: vi.fn(),
    update: vi.fn(),
    loanSummary: vi.fn(),
  },
  transactions: { list: vi.fn(), update: vi.fn(), delete: vi.fn(), create: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
  loanPayments: { list: vi.fn() },
  assets: { get: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '***' }),
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true, hasModule: () => true }),
}))

// Made-up figures.
const mortgage = {
  id: 'acc-1',
  name: 'Test mortgage',
  type: 'loan',
  currency: 'USD',
  balance: -300000,
  connection_id: null,
  loan_subtype: 'mortgage',
  secured_asset_id: null,
  loan_details: null,
  is_closed: false,
}

function loanSummary(manualPaymentsAllowed: boolean) {
  return {
    account_id: 'acc-1',
    currency: 'USD',
    manual_payments_allowed: manualPaymentsAllowed,
    months: [],
    total_paid: 0,
    total_interest: 0,
    total_principal_reduction: 0,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.accounts.bills.mockResolvedValue([])
  api.accounts.summary.mockResolvedValue({
    monthly_income: 0, monthly_expenses: 0, projected_income: 0, projected_expenses: 0,
  })
  api.accounts.loanSummary.mockResolvedValue(loanSummary(true))
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.loanPayments.list.mockResolvedValue([])
})

function renderPage(account: Record<string, unknown>) {
  api.accounts.get.mockResolvedValue(account)
  api.accounts.list.mockResolvedValue([account])
  return renderWithProviders(<AccountDetailPage />, {
    route: '/accounts/acc-1',
    path: '/accounts/:id',
  })
}

describe('account page loan block', () => {
  it('shows the loan summary and "Record payment" on a manual loan', async () => {
    renderPage(mortgage)

    expect(await screen.findByRole('region', { name: t('loans.summary.title') })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: t('loans.payment.record') })).toBeInTheDocument()
    expect(api.accounts.loanSummary).toHaveBeenCalledWith('acc-1')
  })

  it('has no "Record payment" on a synced loan', async () => {
    api.accounts.loanSummary.mockResolvedValue(loanSummary(false))
    renderPage({ ...mortgage, connection_id: 'conn-1' })

    expect(await screen.findByText(t('loans.summary.syncedNote'))).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('loans.payment.record') })).not.toBeInTheDocument()
  })

  it('has no loan block on other account types', async () => {
    renderPage({ ...mortgage, type: 'checking', balance: 1000, loan_subtype: null })

    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())
    expect(screen.queryByRole('region', { name: t('loans.summary.title') })).not.toBeInTheDocument()
    expect(api.accounts.loanSummary).not.toHaveBeenCalled()
  })
})

describe('savings goal button', () => {
  it('opens a new goal linked to a savings account', async () => {
    renderPage({ ...mortgage, type: 'savings', balance: 500, loan_subtype: null })

    const link = await screen.findByRole('link', { name: t('goals.savings.createFromAccount') })
    expect(link).toHaveAttribute('href', '/goals?new=1&account_id=acc-1')
  })

  it('is not offered on a loan', async () => {
    renderPage(mortgage)

    expect(await screen.findByRole('region', { name: t('loans.summary.title') })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: t('goals.savings.createFromAccount') })).not.toBeInTheDocument()
  })
})
