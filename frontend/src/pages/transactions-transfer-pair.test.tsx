/**
 * Both legs of a linked transfer on one page of the transactions list draw as
 * one row: the from → to pill, the amount out over the amount in, and one
 * checkbox that selects both legs. Counts and the selection stay per
 * transaction; a lone leg keeps its own row.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import TransactionsPage from '@/pages/transactions'
import { renderWithProviders } from '@/test/utils'
import type { Account, Transaction } from '@/types'

const state = vi.hoisted(() => ({ mobile: false }))

const api = vi.hoisted(() => ({
  transactions: { list: vi.fn(), calendar: vi.fn(), export: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  accounts: { list: vi.fn() },
  recurring: { list: vi.fn() },
  payees: { list: vi.fn() },
  admin: { accountingMode: vi.fn() },
  groups: { list: vi.fn(), get: vi.fn() },
  rules: { create: vi.fn(), list: vi.fn() },
  reconciliation: { suggestions: vi.fn() },
}))

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  ...api,
}))

vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => state.mobile }))

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

vi.mock('@/contexts/workspace-context', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/contexts/workspace-context')>()),
  useWorkspace: () => ({ canWrite: true, hasModule: () => false }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

const everyday = { id: 'acc-everyday', name: 'Everyday', display_name: null, type: 'checking', currency: 'USD', institution_logo_url: null } as Account
const savings = { id: 'acc-savings', name: 'Savings', display_name: null, type: 'savings', currency: 'USD', institution_logo_url: null } as Account
const card = { id: 'acc-card', name: 'Card', display_name: null, type: 'credit_card', currency: 'USD', institution_logo_url: null } as Account

const tx = (overrides: Partial<Transaction>): Transaction => ({
  id: 'tx',
  account_id: everyday.id,
  description: 'Row',
  amount: 10,
  currency: 'USD',
  type: 'debit',
  date: '2026-03-10',
  status: 'posted',
  transfer_pair_id: null,
  amount_primary: null,
  is_ignored: false,
  is_shared: false,
  splits: [],
  notes: null,
  category: null,
  ...overrides,
} as unknown as Transaction)

const coffee = tx({ id: 'tx-coffee', description: 'Coffee shop', amount: 4.5, date: '2026-03-11' })
const outLeg = tx({
  id: 'tx-out', description: 'To savings', amount: 250, type: 'debit',
  transfer_pair_id: 'pair-1', transfer_counterpart_account_id: savings.id,
})
const salary = tx({ id: 'tx-salary', description: 'Salary', amount: 1000, type: 'credit', date: '2026-03-10' })
const inLeg = tx({
  id: 'tx-in', account_id: savings.id, description: 'From everyday', amount: 250, type: 'credit',
  transfer_pair_id: 'pair-1', transfer_counterpart_account_id: everyday.id,
})
const loneLeg = tx({
  id: 'tx-lone', description: 'Card payment', amount: 80, type: 'debit', date: '2026-03-09',
  transfer_pair_id: 'pair-2', transfer_counterpart_account_id: card.id,
})

const items = [coffee, outLeg, salary, inLeg, loneLeg]

beforeEach(() => {
  vi.clearAllMocks()
  state.mobile = false
  api.transactions.list.mockResolvedValue({
    items,
    total: items.length,
    page: 1,
    limit: 20,
    summary: { income: 1000, expense: 84.5, net: 915.5, excluded: 0, currency: 'USD' },
  })
  api.transactions.calendar.mockResolvedValue(null)
  api.transactions.export.mockResolvedValue(undefined)
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([everyday, savings, card])
  api.recurring.list.mockResolvedValue([])
  api.payees.list.mockResolvedValue([])
  api.admin.accountingMode.mockResolvedValue({ mode: 'cash' })
  api.groups.list.mockResolvedValue([])
  api.rules.list.mockResolvedValue([])
  api.reconciliation.suggestions.mockResolvedValue([])
})

const PAIR_LABEL = 'Transfer from Everyday to Savings'
const LONE_LABEL = 'Transfer from Everyday to Card'

describe('transactions list, desktop', () => {
  it('draws a transfer with both legs loaded as one row, where the outgoing leg sat', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions' })

    const pairCheckbox = await screen.findByRole('checkbox', { name: 'Select both sides of this transfer' })
    const pairRow = pairCheckbox.closest('tr')!
    expect(within(pairRow).getByText(PAIR_LABEL)).toBeInTheDocument()
    expect(within(pairRow).getByTitle('Out of Everyday')).toHaveTextContent('−$250.00')
    expect(within(pairRow).getByTitle('Into Savings')).toHaveTextContent('+$250.00')

    // Header + coffee + pair + salary + lone leg: the credit leg has no row.
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(5)
    expect(rows[1]).toHaveTextContent('Coffee shop')
    expect(rows[2]).toBe(pairRow)
    expect(rows[3]).toHaveTextContent('Salary')
    expect(screen.queryByText('From everyday')).toBeNull()
  }, 30_000)

  it('keeps a lone leg as a single-amount row', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions' })

    const lone = (await screen.findByText('Card payment')).closest('tr')!
    expect(within(lone).getByText(LONE_LABEL)).toBeInTheDocument()
    expect(within(lone).getByText('−$80.00')).toBeInTheDocument()
    expect(within(lone).queryByText(/^\+/)).toBeNull()
    expect(within(lone).getByRole('checkbox')).not.toHaveAccessibleName()
  }, 30_000)

  it('selects both legs from the one checkbox and still counts transactions', async () => {
    const { user } = renderWithProviders(<TransactionsPage />, { route: '/transactions' })

    await user.click(await screen.findByRole('checkbox', { name: 'Select both sides of this transfer' }))
    expect(screen.getByRole('checkbox', { name: 'Select both sides of this transfer' })).toBeChecked()

    // The page-level count is still transactions, not rows.
    expect(screen.getAllByText('5 transactions').length).toBeGreaterThan(0)

    await user.click(screen.getByRole('button', { name: /Export Selected \(2\)/ }))
    await waitFor(() => expect(api.transactions.export).toHaveBeenCalled())
    const [{ transaction_ids }] = api.transactions.export.mock.calls[0] as [{ transaction_ids: string[] }]
    expect([...transaction_ids].sort()).toEqual(['tx-in', 'tx-out'])
  }, 30_000)
})

describe('transactions list, mobile', () => {
  it('draws the pair as one card with both amounts', async () => {
    state.mobile = true
    renderWithProviders(<TransactionsPage />, { route: '/transactions' })

    expect(await screen.findByRole('checkbox', { name: 'Select both sides of this transfer' })).toBeInTheDocument()
    expect(screen.getAllByText(PAIR_LABEL)).toHaveLength(1)
    expect(screen.getByText(LONE_LABEL)).toBeInTheDocument()
    expect(screen.getByTitle('Out of Everyday')).toHaveTextContent('−$250.00')
    expect(screen.getByTitle('Into Savings')).toHaveTextContent('+$250.00')
    expect(screen.queryByText('From everyday')).toBeNull()
  }, 30_000)
})

describe('date range on arrival (fork)', () => {
  it('a plain visit opens on the current month', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions', path: '/transactions' })
    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())
    const args = api.transactions.list.mock.calls.at(-1)![0]
    expect(args.from).toBeTruthy()
    expect(args.to).toBeTruthy()
  })

  it('a link that narrows by category opens without a date range', async () => {
    renderWithProviders(<TransactionsPage />, { route: '/transactions?category_id=cat-1', path: '/transactions' })
    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())
    const args = api.transactions.list.mock.calls.at(-1)![0]
    expect(args.category_ids).toEqual(['cat-1'])
    expect(args.from).toBeUndefined()
    expect(args.to).toBeUndefined()
  })

  it('a link that brings its own dates keeps them', async () => {
    renderWithProviders(<TransactionsPage />, {
      route: '/transactions?category_id=cat-1&from=2025-01-01&to=2025-12-31', path: '/transactions',
    })
    await waitFor(() => expect(api.transactions.list).toHaveBeenCalled())
    const args = api.transactions.list.mock.calls.at(-1)![0]
    expect([args.from, args.to]).toEqual(['2025-01-01', '2025-12-31'])
  })
})
