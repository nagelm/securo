/**
 * The date range an account page opens on: loans and investments open on the
 * last five years, everything else on the current month, and a range longer
 * than one API page is fetched in full and listed a page at a time.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import AccountDetailPage from '@/pages/account-detail'
import { longRangeDefault } from '@/lib/account-long-range'
import { localDateString } from '@/lib/date-utils'
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
const loan = {
  id: 'acc-1',
  name: 'Test mortgage',
  type: 'loan',
  currency: 'USD',
  balance: -300000,
  connection_id: 'conn-1',
  loan_subtype: 'mortgage',
  secured_asset_id: null,
  loan_details: null,
  is_closed: false,
}

function tx(n: number) {
  const day = new Date(2024, 0, 1 + n)
  return {
    id: `tx-${n}`,
    account_id: 'acc-1',
    date: localDateString(day),
    description: `Row ${n}`,
    amount: 10,
    amount_primary: null,
    currency: 'USD',
    type: 'debit',
    source: 'sync',
    status: 'posted',
    category: null,
    category_id: null,
    transfer_pair_id: null,
    is_ignored: false,
    splits: [],
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.accounts.bills.mockResolvedValue([])
  api.accounts.summary.mockResolvedValue({
    monthly_income: 0, monthly_expenses: 0, projected_income: 0, projected_expenses: 0,
    opening_balance: 0, current_balance: 0,
  })
  api.accounts.loanSummary.mockResolvedValue({
    account_id: 'acc-1', currency: 'USD', manual_payments_allowed: false, months: [],
    total_paid: 0, total_interest: 0, total_principal_reduction: 0,
  })
  api.transactions.list.mockResolvedValue({ items: [], total: 0, page: 1, limit: 500 })
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

function listCalls() {
  return api.transactions.list.mock.calls.map(([args]) => args as Record<string, unknown>)
}

describe('account page default range', () => {
  it.each(['loan', 'investment'])('opens a %s on the last five years', async (type) => {
    renderPage({ ...loan, type })
    const { start, end } = longRangeDefault()
    await waitFor(() =>
      expect(listCalls()).toContainEqual(expect.objectContaining({ from: start, to: end })),
    )
  })

  it('opens other accounts on the current month', async () => {
    renderPage({ ...loan, type: 'checking', balance: 1000, loan_subtype: null })
    const now = new Date()
    const start = localDateString(new Date(now.getFullYear(), now.getMonth(), 1))
    const end = localDateString(new Date(now.getFullYear(), now.getMonth() + 1, 0))
    await waitFor(() => expect(listCalls().length).toBeGreaterThan(0))
    for (const call of listCalls()) {
      expect(call).toMatchObject({ from: start, to: end })
    }
  })
})

describe('account page long lists', () => {
  it('fetches every page and lists them a page at a time', async () => {
    const first = Array.from({ length: 500 }, (_, i) => tx(619 - i))
    const second = Array.from({ length: 120 }, (_, i) => tx(119 - i))
    api.transactions.list.mockImplementation(async (params: { page?: number }) =>
      params.page === 2
        ? { items: second, total: 620, page: 2, limit: 500 }
        : { items: first, total: 620, page: 1, limit: 500 },
    )
    renderPage(loan)

    const more = await screen.findByRole('button', { name: t('accounts.showOlder', { count: 120 }) })
    expect(listCalls()).toContainEqual(expect.objectContaining({ page: 2, limit: 500 }))
    expect(screen.getByText('Row 619')).toBeInTheDocument()
    expect(screen.queryByText('Row 0')).not.toBeInTheDocument()

    fireEvent.click(more)
    expect(await screen.findByText('Row 0')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: t('accounts.showOlder', { count: 120 }) })).not.toBeInTheDocument()
  })
})
