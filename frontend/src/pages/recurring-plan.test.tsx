import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import RecurringPage from '@/pages/recurring'
import { renderWithProviders } from '@/test/utils'
import type { Account, Category, Payee, RecurringTransaction } from '@/types'

// Fork: the Recurring page lists only genuinely regular payments; the
// Forecast plan's items (plan: true) live on the Forecast page.

const api = vi.hoisted(() => ({
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  recurring: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), generate: vi.fn() },
  accounts: { list: vi.fn() },
  currencies: { list: vi.fn() },
  payees: { list: vi.fn(), create: vi.fn() },
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
    WorkspaceContext: createContext({ canWrite: true }),
  }
})

const account = {
  id: 'chk-1',
  name: 'Everyday',
  display_name: null,
  type: 'checking',
  currency: 'USD',
  position: 0,
  connection_id: null,
  is_closed: false,
} as unknown as Account

const groceries = {
  id: 'cat-groceries',
  name: 'Groceries',
  icon: 'shopping-cart',
  color: '#22c55e',
} as unknown as Category

const payees = [
  { id: 'payee-stream', name: 'Streaming Co' },
  { id: 'payee-dairy', name: 'Corner Dairy' },
] as Payee[]

function recurring(overrides: Partial<RecurringTransaction>): RecurringTransaction {
  return {
    id: 'rec',
    user_id: 'user-1',
    account_id: 'chk-1',
    category_id: 'cat-groceries',
    description: 'Item',
    amount: 20,
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
    kind: 'standard',
    payee_id: null,
    payee_name: null,
    payee_logo_url: null,
    payee_icon: null,
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.recurring.list.mockResolvedValue([])
  api.recurring.create.mockResolvedValue({})
  api.recurring.update.mockResolvedValue({})
  api.categories.list.mockResolvedValue([groceries])
  api.categories.listIncludingHidden.mockResolvedValue([groceries])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([account])
  api.currencies.list.mockResolvedValue([{ code: 'USD', symbol: '$', name: 'US Dollar', flag: '' }])
  api.payees.list.mockResolvedValue(payees)
})

describe('recurring page and plan items', () => {
  it('leaves the Forecast plan items out', async () => {
    api.recurring.list.mockResolvedValue([
      recurring({ id: 'r1', description: 'Rent' }),
      recurring({ id: 'r2', description: 'Allowance (plan)', plan: true }),
    ])
    renderWithProviders(<RecurringPage />)

    expect(await screen.findByText('Rent')).toBeInTheDocument()
    expect(screen.queryByText('Allowance (plan)')).not.toBeInTheDocument()
  })
})
