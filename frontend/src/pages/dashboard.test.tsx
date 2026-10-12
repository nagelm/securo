import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, within } from '@testing-library/react'

import DashboardPage from '@/pages/dashboard'
import { TooltipProvider } from '@/components/ui/tooltip'
import { formatCurrency } from '@/lib/format'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, DashboardSummary } from '@/types'

const api = vi.hoisted(() => ({
  dashboard: {
    summary: vi.fn(),
    spendingByCategory: vi.fn(),
    balanceHistory: vi.fn(),
    projectedTransactions: vi.fn(),
  },
  transactions: { list: vi.fn(), calendar: vi.fn() },
  budgets: { comparison: vi.fn() },
  categories: { list: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  goals: { summary: vi.fn() },
  groups: { list: vi.fn() },
  payees: { list: vi.fn() },
  rules: { create: vi.fn() },
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

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

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

// Two cards on one shared credit line both report the line's full -300.
const accounts = [
  account({ id: 'checking', name: 'Checking', current_balance: 1000 }),
  account({ id: 'card-a', name: 'Card A', type: 'credit_card', current_balance: -300, shared_balance_group: 'line-1' }),
  account({ id: 'card-b', name: 'Card B', type: 'credit_card', current_balance: -300, shared_balance_group: 'line-1' }),
  account({ id: 'card-c', name: 'Card C', type: 'credit_card', current_balance: -200 }),
]

const summary: DashboardSummary = {
  total_balance: { USD: 500 },
  total_balance_primary: 500,
  projected_balance: { USD: 500 },
  projected_balance_primary: 500,
  balance_date: '2026-09-12',
  monthly_income: 0,
  monthly_expenses: 0,
  monthly_income_primary: 0,
  monthly_expenses_primary: 0,
  accounts_count: accounts.length,
  pending_categorization: 0,
  pending_categorization_amount: 0,
  assets_value: {},
  assets_value_primary: 0,
  primary_currency: 'USD',
  pending_shares_net: 0,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.dashboard.summary.mockResolvedValue(summary)
  api.dashboard.spendingByCategory.mockResolvedValue([])
  api.dashboard.balanceHistory.mockResolvedValue({ current: [], previous: [] })
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.budgets.comparison.mockResolvedValue([])
  api.categories.list.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue(accounts)
  api.goals.summary.mockResolvedValue([])
  api.groups.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
})

describe('Dashboard net worth breakdown', () => {
  it('counts a shared credit line once in the credit card total', async () => {
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    const cardRow = within(tooltip).getByText(t('dashboard.creditCardBalance')).parentElement!
    expect(cardRow).toHaveTextContent(formatCurrency(-500, 'USD', 'en-US'))
  })

  it('shows loans on their own row and keeps them out of available balance', async () => {
    // Net worth 2,000 = available 1,700 (checking 1,000 + savings 700)
    // - cards 500 - loans 1,200 + assets 2,000.
    api.accounts.list.mockResolvedValue([
      ...accounts,
      account({ id: 'savings', name: 'Savings', type: 'savings', current_balance: 700 }),
      account({ id: 'mortgage', name: 'Mortgage', type: 'loan', loan_subtype: 'mortgage', current_balance: -1000 }),
      account({ id: 'car', name: 'Car loan', type: 'loan', loan_subtype: 'vehicle', current_balance: -200 }),
    ])
    api.dashboard.summary.mockResolvedValue({
      ...summary,
      total_balance: { USD: 2000 },
      total_balance_primary: 2000,
      projected_balance: { USD: 2000 },
      projected_balance_primary: 2000,
      assets_value: { USD: 2000 },
      assets_value_primary: 2000,
    })

    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(formatCurrency(2000, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    const rowValue = (label: string) =>
      within(tooltip).getByText(label).parentElement!.lastElementChild!.textContent
    expect(rowValue(t('dashboard.availableBalance'))).toBe(formatCurrency(1700, 'USD', 'en-US'))
    expect(rowValue(t('dashboard.loanBalance'))).toBe(formatCurrency(-1200, 'USD', 'en-US'))
    expect(rowValue(t('dashboard.creditCardBalance'))).toBe(formatCurrency(-500, 'USD', 'en-US'))
    expect(rowValue(t('dashboard.assetsValue'))).toBe(formatCurrency(2000, 'USD', 'en-US'))
  })

  it('has no loan row when there are no loans', async () => {
    const { user } = renderWithProviders(
      <TooltipProvider delayDuration={0}>
        <DashboardPage />
      </TooltipProvider>,
    )

    await screen.findByText(formatCurrency(500, 'USD', 'en-US'))
    await user.hover(screen.getByRole('button', { name: 'i' }))

    const tooltip = await screen.findByRole('tooltip')
    expect(within(tooltip).queryByText(t('dashboard.loanBalance'))).not.toBeInTheDocument()
  })
})
