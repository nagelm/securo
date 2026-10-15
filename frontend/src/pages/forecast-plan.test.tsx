import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import ForecastPage from '@/pages/forecast'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Category, ForecastItem, ForecastResponse, RecurringTransaction } from '@/types'

// Fork: the Forecast page as a household plan. Expected transfers, plan
// recurring items, chosen budgets and the account view. Made-up names and figures.

const api = vi.hoisted(() => ({
  forecast: {
    get: vi.fn(),
    items: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
    scenarios: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
    budgetChoices: { get: vi.fn(), set: vi.fn() },
  },
  recurring: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  budgets: { list: vi.fn() },
  budgetGroups: { list: vi.fn() },
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
    recalculate_repayment: null, matched_transaction_id: null,
    ...values,
  }
}

function plan(values: Partial<RecurringTransaction>): RecurringTransaction {
  return {
    id: 'rec-1', user_id: 'u', account_id: 'acc-1', category_id: null, description: 'Allowance', amount: 100,
    currency: 'USD', type: 'debit', frequency: 'weekly', weekend_adjustment: 'none', day_of_month: null,
    start_date: '2026-10-01', end_date: '2026-10-22', is_active: true, auto_generate: false,
    next_occurrence: '2026-10-15', amount_primary: null, fx_rate_used: null, kind: 'standard',
    transfer_account_id: null, plan: true, arrived_count: 2, occurrence_count: 4,
    ...values,
  }
}

const eatingOut = { id: 'cat-1', name: 'Eating out', group_id: null, icon: 'tag', color: '#123456' } as Category

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  api.forecast.get.mockResolvedValue(response())
  api.forecast.items.list.mockResolvedValue([])
  api.forecast.items.create.mockResolvedValue({})
  api.forecast.scenarios.list.mockResolvedValue([])
  api.recurring.list.mockResolvedValue([])
  api.recurring.create.mockResolvedValue({})
  api.recurring.update.mockResolvedValue({})
  api.recurring.delete.mockResolvedValue(undefined)
  api.budgets.list.mockResolvedValue([])
  api.budgetGroups.list.mockResolvedValue([])
  api.forecast.budgetChoices.get.mockResolvedValue({ category_ids: [], budget_group_ids: [] })
  api.accounts.list.mockResolvedValue(accounts)
  api.categories.list.mockResolvedValue([eatingOut])
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

  it('a repeating transaction is saved as a plan recurring item', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    const dialog = await openExpectedForm(user)

    await user.selectOptions(within(dialog).getByLabelText(t('forecast.itemKind')), 'repeating')
    await user.selectOptions(within(dialog).getByLabelText(t('recurring.frequency')), 'biweekly')
    await user.selectOptions(within(dialog).getByLabelText(t('recurring.type')), 'transfer')
    await user.selectOptions(within(dialog).getByLabelText(t('forecast.transferTo')), 'acc-2')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.recurring.create).toHaveBeenCalledTimes(1))
    expect(api.forecast.items.create).not.toHaveBeenCalled()
    const payload = api.recurring.create.mock.calls[0][0]
    expect(payload).toEqual(expect.objectContaining({
      plan: true, description: 'Plan line', amount: 450, type: 'debit', frequency: 'biweekly', end_date: null,
      account_id: 'acc-1', transfer_account_id: 'acc-2', currency: 'USD',
    }))
    expect(payload.start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('edits and deletes a plan item through the recurring API', async () => {
    api.recurring.list.mockResolvedValue([plan({})])
    const { user } = renderWithProviders(<ForecastPage />)

    const row = await screen.findByTestId('expected-plan')
    await user.click(within(row).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByLabelText(t('forecast.itemKind'))).toHaveValue('repeating')
    await user.clear(within(dialog).getByLabelText(t('recurring.amount')))
    await user.type(within(dialog).getByLabelText(t('recurring.amount')), '120')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.recurring.update).toHaveBeenCalledWith('rec-1', expect.objectContaining({ amount: 120 })))

    await user.click(within(await screen.findByTestId('expected-plan')).getByRole('button', { name: t('common.delete') }))
    await user.click(await screen.findByRole('button', { name: t('common.delete') }))
    await waitFor(() => expect(api.recurring.delete).toHaveBeenCalledWith('rec-1'))
  })

  it('lists a transfer as from → to and a plan item with its arrived count', async () => {
    api.forecast.items.list.mockResolvedValue([
      item({ id: 'it-1', description: 'To savings', date: '2026-10-20', transfer_account_id: 'acc-2' }),
    ])
    api.recurring.list.mockResolvedValue([
      plan({}),
      // A genuinely regular payment is not a plan item.
      plan({ id: 'rec-2', description: 'Rent', plan: false }),
    ])
    renderWithProviders(<ForecastPage />)

    expect(await screen.findByText('Joint → Savings')).toBeInTheDocument()
    expect(await screen.findByTestId('series-status')).toHaveTextContent('2/4 arrived')
    expect(screen.getAllByTestId('expected-plan')).toHaveLength(1)
    const details = screen.getAllByTestId('expected-detail').map((d) => d.textContent)
    expect(details).toContain('Weekly, 10/1/2026 – 10/22/2026')
    // The recurring line counts the regular payment only.
    expect(screen.getByTestId('recurring-line')).toHaveTextContent(t('forecast.recurringLine', { count: 1 }))
  })
})

describe('Forecast page: budgets (fork)', () => {
  it('lists the budgets, saves the chosen ones and shows what each month already has', async () => {
    api.budgets.list.mockResolvedValue([
      { id: 'b-1', user_id: 'u', category_id: 'cat-1', amount: 300, month: '2026-10-01', is_recurring: true },
      { id: 'b-2', user_id: 'u', category_id: 'cat-1', amount: 350, month: '2027-01-01', is_recurring: true },
    ])
    api.budgetGroups.list.mockResolvedValue([
      { id: 'g-1', user_id: 'u', name: 'Everyday', amount: 500, month: '2026-10-01', is_recurring: true,
        end_month: null, category_ids: ['cat-2'] },
    ])
    api.forecast.budgetChoices.get.mockResolvedValue({ category_ids: [], budget_group_ids: ['g-1'] })
    api.forecast.budgetChoices.set.mockImplementation(async (choices) => choices)
    api.forecast.get.mockResolvedValue({
      ...response(),
      budget_uses: [{
        category_id: null, budget_group_id: 'g-1', name: 'Everyday', month: '2026-11-01',
        amount: 500, committed: 120, remainder: 380,
      }],
    })
    const { user } = renderWithProviders(<ForecastPage />)

    await user.click(await screen.findByRole('tab', { name: t('forecast.tabs.budgets') }))
    const eating = await screen.findByRole('checkbox', { name: 'Eating out' })
    expect(eating).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'Everyday' })).toBeChecked()
    const uses = screen.getByTestId('budget-uses')
    expect(within(uses).getByText('$120.00')).toBeInTheDocument()
    expect(within(uses).getByText('$380.00')).toBeInTheDocument()

    await user.click(eating)
    await waitFor(() => expect(api.forecast.budgetChoices.set).toHaveBeenCalledWith({
      category_ids: ['cat-1'], budget_group_ids: ['g-1'],
    }))
  })
})
