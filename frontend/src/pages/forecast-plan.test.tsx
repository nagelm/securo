import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import ForecastPage from '@/pages/forecast'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, ForecastItem, ForecastResponse } from '@/types'

// Fork: the Forecast page as a household plan. Expected transfers, repeating
// expected payments, budgets and the account view. Made-up names and figures.

const api = vi.hoisted(() => ({
  forecast: {
    get: vi.fn(),
    items: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
    scenarios: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  },
  recurring: { list: vi.fn() },
  accounts: { list: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
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
    useWorkspace: () => ({ canWrite: true, current: { id: 'ws-1' } }),
    WorkspaceContext: createContext(null),
  }
})

const VIEW_KEY = 'securo.forecast.accounts.ws-1'

function response(): ForecastResponse {
  return {
    currency: 'USD',
    interval: 'monthly',
    start_date: '2026-10-14',
    end_date: '2027-01-14',
    starting_balance: 1000,
    include_spending: true,
    account_ids: ['acc-1', 'acc-2'],
    base: [
      { date: '2026-10-14', value: 1000 },
      { date: '2027-01-14', value: 1200 },
    ],
    summary: { end_balance: 1200, lowest_balance: 1000, lowest_date: '2026-10-14' },
    scenarios: [],
    spending_estimates: [],
  }
}

const account = (id: string, name: string, type: string, position: number) =>
  ({ id, name, display_name: null, type, currency: 'USD', position, is_closed: false }) as unknown as Account

const accounts = [
  account('acc-1', 'Joint', 'checking', 0),
  account('acc-2', 'Savings', 'savings', 1),
  account('acc-3', 'Home loan', 'loan', 2),
]

function item(values: Partial<ForecastItem>): ForecastItem {
  return {
    id: 'it-1', scenario_id: null, kind: 'one_off', description: 'Item', amount: '100', currency: 'USD',
    type: 'debit', date: null, start_date: null, end_date: null, frequency: null, account_id: 'acc-1',
    transfer_account_id: null, category_id: null, recurring_transaction_id: null, change_mode: null, percent: null,
    recalculate_repayment: null, matched_transaction_id: null, matches: [], occurrence_count: null,
    ...values,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.forecast.get.mockResolvedValue(response())
  api.forecast.items.list.mockResolvedValue([])
  api.forecast.items.create.mockResolvedValue({})
  api.forecast.scenarios.list.mockResolvedValue([])
  api.recurring.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue(accounts)
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

async function openExpectedForm(user: ReturnType<typeof renderWithProviders>['user']) {
  await waitFor(() => expect(api.accounts.list).toHaveBeenCalled())
  await user.click(await screen.findByRole('button', { name: t('forecast.addExpected') }))
  const dialog = await screen.findByRole('dialog')
  await waitFor(() => expect(within(dialog).getByLabelText(t('recurring.account'))).toHaveValue('acc-1'))
  await user.type(within(dialog).getByLabelText(t('recurring.description')), 'Plan line')
  await user.type(within(dialog).getByLabelText(t('recurring.amount')), '450')
  return dialog
}

describe('Forecast page: account view (fork)', () => {
  it('picks accounts, asks for that view and remembers it', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    await waitFor(() => expect(api.forecast.get).toHaveBeenCalledWith(12, 'monthly', true))

    await user.click(await screen.findByRole('button', { name: t('forecast.accountView') }))
    // Cash and card accounts only: no loan.
    expect(screen.queryByRole('checkbox', { name: 'Home loan' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Joint' }))

    await waitFor(() => expect(api.forecast.get).toHaveBeenLastCalledWith(12, 'monthly', true, ['acc-2']))
    expect(JSON.parse(localStorage.getItem(VIEW_KEY) ?? 'null')).toEqual(['acc-2'])

    await user.click(screen.getByRole('button', { name: t('forecast.allCashAccounts') }))
    await waitFor(() => expect(api.forecast.get).toHaveBeenLastCalledWith(12, 'monthly', true))
    expect(localStorage.getItem(VIEW_KEY)).toBeNull()
  })

  it('opens on the remembered view, dropping accounts that are gone', async () => {
    localStorage.setItem(VIEW_KEY, JSON.stringify(['acc-2', 'acc-gone']))
    renderWithProviders(<ForecastPage />)

    await waitFor(() => expect(api.forecast.get).toHaveBeenLastCalledWith(12, 'monthly', true, ['acc-2']))
    expect(await screen.findByRole('button', { name: t('forecast.accountView') })).toHaveTextContent('Savings')
  })
})

describe('Forecast page: plan items (fork)', () => {
  it('an expected transfer sends the to-account and goes out of the from-account', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    const dialog = await openExpectedForm(user)

    await user.selectOptions(within(dialog).getByLabelText(t('recurring.type')), 'transfer')
    expect(within(dialog).getByLabelText(t('forecast.fromAccount'))).toHaveValue('acc-1')
    await user.selectOptions(within(dialog).getByLabelText(t('forecast.transferTo')), 'acc-2')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.forecast.items.create).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'one_off', type: 'debit', account_id: 'acc-1', transfer_account_id: 'acc-2', amount: '450',
    })))
  })

  it('a repeating payment sends its frequency and dates', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    const dialog = await openExpectedForm(user)

    await user.selectOptions(within(dialog).getByLabelText(t('forecast.itemKind')), 'repeating')
    await user.selectOptions(within(dialog).getByLabelText(t('recurring.frequency')), 'biweekly')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.forecast.items.create).toHaveBeenCalledTimes(1))
    const payload = api.forecast.items.create.mock.calls[0][0]
    expect(payload).toEqual(expect.objectContaining({
      kind: 'repeating', type: 'debit', frequency: 'biweekly', end_date: null, transfer_account_id: null,
    }))
    expect(payload.start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(payload).not.toHaveProperty('date')
  })

  it('a budget sends its period and no direction', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    const dialog = await openExpectedForm(user)

    await user.selectOptions(within(dialog).getByLabelText(t('forecast.itemKind')), 'budget')
    expect(within(dialog).queryByLabelText(t('recurring.type'))).not.toBeInTheDocument()
    const frequency = within(dialog).getByLabelText(t('recurring.frequency'))
    expect(within(frequency).getAllByRole('option').map((o) => o.getAttribute('value')))
      .toEqual(['weekly', 'biweekly', 'monthly'])
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.forecast.items.create).toHaveBeenCalledTimes(1))
    const payload = api.forecast.items.create.mock.calls[0][0]
    expect(payload).toEqual(expect.objectContaining({ kind: 'budget', frequency: 'weekly', amount: '450' }))
    expect(payload).not.toHaveProperty('type')
  })

  it('lists a transfer as from → to, a series with its arrived count and a budget by its period', async () => {
    api.forecast.items.list.mockResolvedValue([
      item({ id: 'it-1', description: 'To savings', date: '2026-10-20', transfer_account_id: 'acc-2' }),
      item({
        id: 'it-2', kind: 'repeating', description: 'Allowance', frequency: 'weekly', start_date: '2026-10-01',
        end_date: '2026-10-22', occurrence_count: 4,
        matches: [{ occurrence_date: '2026-10-01', transaction_id: 'tx-1' }, { occurrence_date: '2026-10-08', transaction_id: 'tx-2' }],
      }),
      item({
        id: 'it-3', kind: 'budget', description: 'Everyday spending', amount: '450', frequency: 'weekly',
        start_date: '2026-10-19', end_date: '2027-01-24', account_id: null,
      }),
    ])
    renderWithProviders(<ForecastPage />)

    expect(await screen.findByText('Joint → Savings')).toBeInTheDocument()
    expect(screen.getByTestId('series-status')).toHaveTextContent('2/4 arrived')
    const details = screen.getAllByTestId('expected-detail').map((d) => d.textContent)
    expect(details).toContain('Weekly, 10/1/2026 – 10/22/2026')
    expect(details).toContain('Budget: $450.00 a week, 10/19/2026 – 1/24/2027')
    expect(screen.getByText(t('forecast.budgetBadge'))).toBeInTheDocument()
  })
})
