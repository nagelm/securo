import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import BudgetsPage from '@/pages/budgets'
import { createTestQueryClient, renderWithProviders, t } from '@/test/utils'
import type { Budget, BudgetGroupVsActual, Category } from '@/types'

// Fork: recurring budgets with an end month, budget groups drawn like
// budgets, and "Include in forecast". Made-up names and figures.

const api = vi.hoisted(() => ({
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn(), listIncludingHidden: vi.fn() },
  budgets: {
    list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), comparison: vi.fn(),
    suggestions: vi.fn(), createBatch: vi.fn(),
  },
  dashboard: { spendingByCategory: vi.fn(), projectedTransactions: vi.fn() },
  transactions: { list: vi.fn() },
  admin: { accountingMode: vi.fn() },
  budgetGroups: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), comparison: vi.fn() },
  forecast: { budgetChoices: { get: vi.fn(), set: vi.fn() } },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => 'UTC' }))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null }),
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

const category = (id: string, name: string, flags: Partial<Category> = {}) =>
  ({
    id, name, icon: 'tag', color: '#123456', group_id: null, is_hidden: false, treat_as_transfer: false,
    is_ignored: false, ...flags,
  }) as Category

const categories = [
  category('cat-food', 'Groceries'),
  category('cat-out', 'Eating out'),
  category('cat-save', 'Savings', { treat_as_transfer: true }),
]

const now = new Date()
const YEAR = now.getFullYear()
const MONTH = `${YEAR}-${String(now.getMonth() + 1).padStart(2, '0')}`
const lastMonth = () => {
  const d = new Date(YEAR, now.getMonth() - 1, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-01`
}

const budget = (values: Partial<Budget>): Budget => ({
  id: 'b-1', user_id: 'u', category_id: 'cat-food', amount: 400, month: `${MONTH}-01`, is_recurring: true,
  end_month: null, ...values,
})

const group: BudgetGroupVsActual = {
  id: 'g-1', name: 'Everyday', amount: '600.00', month: `${MONTH}-01`, is_recurring: true,
  end_month: `${YEAR + 1}-03-01`, category_ids: ['cat-food', 'cat-out'], actual_amount: '150.00',
  projected_amount: '720.00', percentage_used: 120,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categories.list.mockResolvedValue(categories)
  api.categories.listIncludingHidden.mockResolvedValue(categories)
  api.categoryGroups.list.mockResolvedValue([])
  api.categoryGroups.listIncludingHidden.mockResolvedValue([])
  api.dashboard.spendingByCategory.mockResolvedValue([])
  api.dashboard.projectedTransactions.mockResolvedValue([])
  api.transactions.list.mockResolvedValue({ items: [], total: 0 })
  api.admin.accountingMode.mockResolvedValue({ mode: 'cash' })
  api.budgets.suggestions.mockResolvedValue({ month: `${MONTH}-01`, months_requested: 3, months_analyzed: 3, suggestions: [] })
  api.budgets.list.mockResolvedValue([])
  api.budgets.comparison.mockResolvedValue([])
  api.budgets.create.mockResolvedValue({})
  api.budgets.update.mockResolvedValue({})
  api.budgetGroups.comparison.mockResolvedValue([])
  api.budgetGroups.create.mockResolvedValue({})
  api.budgetGroups.update.mockResolvedValue({})
  api.budgetGroups.delete.mockResolvedValue(undefined)
  api.forecast.budgetChoices.get.mockResolvedValue({ category_ids: [], budget_group_ids: [] })
  api.forecast.budgetChoices.set.mockImplementation(async (choices) => choices)
})

/** Picks a month of this year in the open "until" picker. */
async function pickMonth(user: ReturnType<typeof renderWithProviders>['user'], trigger: HTMLElement, name: string) {
  await user.click(trigger)
  await user.click(await screen.findByRole('button', { name }))
}

describe('budget end month', () => {
  it('shows the end month on a repeating budget row and saves a month picked there', async () => {
    api.budgets.list.mockResolvedValue([budget({ end_month: `${YEAR + 1}-03-01` })])
    const { user } = renderWithProviders(<BudgetsPage />)

    const chip = await screen.findByTestId('budget-until')
    expect(chip).toHaveTextContent(`until March ${YEAR + 1}`)
    await user.click(chip)
    await pickMonth(user, await screen.findByLabelText(t('budgets.untilOptional')), 'Dec')

    // The picker opens on the year of the current end month.
    await waitFor(() => expect(api.budgets.update).toHaveBeenCalledWith('b-1', { end_month: `${YEAR + 1}-12-01` }))
  })

  it('names the end month (no raw yyyy-MM text) and clears it only on Clear', async () => {
    api.budgets.list.mockResolvedValue([budget({ end_month: `${YEAR + 1}-03-01` })])
    const { user } = renderWithProviders(<BudgetsPage />)

    await user.click(await screen.findByTestId('budget-until'))
    const until = await screen.findByLabelText(t('budgets.untilOptional'))
    expect(until).toHaveTextContent(`March ${YEAR + 1}`)
    expect(until.tagName).toBe('BUTTON')
    await user.click(screen.getByRole('button', { name: t('budgets.clearEnd') }))

    await waitFor(() => expect(api.budgets.update).toHaveBeenCalledWith('b-1', { end_month: null }))
  })

  it('an end month set on the Budgets page refreshes the forecast', async () => {
    api.budgets.list.mockResolvedValue([budget({})])
    const queryClient = createTestQueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { user } = renderWithProviders(<BudgetsPage />, { queryClient })

    await user.click(await screen.findByTestId('budget-until'))
    await pickMonth(user, await screen.findByLabelText(t('budgets.untilOptional')), 'Dec')

    await waitFor(() => expect(api.budgets.update).toHaveBeenCalled())
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['forecast'] }))
  })

  it('offers no end month on a single-month budget', async () => {
    api.budgets.list.mockResolvedValue([budget({ is_recurring: false })])
    renderWithProviders(<BudgetsPage />)

    await screen.findByRole('switch', { name: t('budgets.includeInForecastFor', { name: 'Groceries' }) })
    expect(screen.queryByTestId('budget-until')).not.toBeInTheDocument()
  })
})

describe('budget rows', () => {
  it('includes a saved budget in the forecast from its row', async () => {
    api.budgets.list.mockResolvedValue([budget({})])
    const { user } = renderWithProviders(<BudgetsPage />)

    const toggle = await screen.findByRole('switch', { name: t('budgets.includeInForecastFor', { name: 'Groceries' }) })
    await waitFor(() => expect(toggle).toBeEnabled())
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    // A category with no budget has no switch.
    expect(screen.queryByRole('switch', { name: t('budgets.includeInForecastFor', { name: 'Eating out' }) })).not.toBeInTheDocument()
    await user.click(toggle)

    await waitFor(() => expect(api.forecast.budgetChoices.set).toHaveBeenCalledWith({
      category_ids: ['cat-food'], budget_group_ids: [],
    }))
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'))
  })
})

describe('budget groups', () => {
  it('draws a group like a budget row: a count chip, spending over the cap in red, the toggle', async () => {
    api.budgetGroups.comparison.mockResolvedValue([group])
    api.forecast.budgetChoices.get.mockResolvedValue({ category_ids: ['cat-food'], budget_group_ids: ['g-1'] })
    const { user } = renderWithProviders(<BudgetsPage />)

    const row = await screen.findByTestId('budget-group')
    expect(row).toHaveTextContent('Everyday')
    // No comma-separated member list in the row: a count that opens the list.
    expect(row).not.toHaveTextContent('Groceries, Eating out')
    expect(within(row).getByTestId('budget-until')).toHaveTextContent(`until March ${YEAR + 1}`)
    const chip = within(row).getByTestId('group-member-count')
    expect(chip).toHaveTextContent(t('budgets.categoryCount', { count: 2 }))
    await user.click(chip)
    const members = await screen.findByTestId('group-members')
    expect(within(members).getByText('Groceries')).toBeInTheDocument()
    expect(within(members).getByText('Eating out')).toBeInTheDocument()

    const spent = within(row).getAllByText('$720.00 of $600.00')[0]
    expect(spent).toHaveClass('text-rose-500')
    expect(within(row).getAllByRole('progressbar')[0]).toHaveAttribute('aria-valuenow', '120')
    const toggle = within(row).getByRole('switch', { name: t('budgets.includeInForecastFor', { name: 'Everyday' }) })
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'))
    await user.click(toggle)
    await waitFor(() => expect(api.forecast.budgetChoices.set).toHaveBeenCalledWith({
      category_ids: ['cat-food'], budget_group_ids: [],
    }))
  })

  it('creates a group from expense categories with a cap and an end month', async () => {
    const { user } = renderWithProviders(<BudgetsPage />)

    await user.click(await screen.findByRole('button', { name: t('budgets.addGroup') }))
    const dialog = await screen.findByRole('dialog')
    const picker = within(dialog).getByTestId('group-categories')
    await waitFor(() => expect(within(picker).getByRole('checkbox', { name: 'Groceries' })).toBeInTheDocument())
    // Transfer-like categories are not spending.
    expect(within(picker).queryByRole('checkbox', { name: 'Savings' })).not.toBeInTheDocument()
    await user.type(within(dialog).getByLabelText(t('budgets.groupName')), 'Everyday')
    await user.type(within(dialog).getByLabelText(t('budgets.groupCap')), '600')
    await user.click(within(picker).getByRole('checkbox', { name: 'Groceries' }))
    await user.click(within(picker).getByRole('checkbox', { name: 'Eating out' }))
    await pickMonth(user, within(dialog).getByLabelText(t('budgets.untilOptional')), 'Dec')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgetGroups.create).toHaveBeenCalledWith({
      name: 'Everyday', amount: 600, month: `${MONTH}-01`, is_recurring: true,
      end_month: `${YEAR}-12-01`, category_ids: ['cat-food', 'cat-out'],
    }))
  })

  it('edits and deletes a group', async () => {
    api.budgetGroups.comparison.mockResolvedValue([group])
    const { user } = renderWithProviders(<BudgetsPage />)

    const row = await screen.findByTestId('budget-group')
    await user.click(within(row).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(within(dialog).getByTestId('group-categories')).getByRole('checkbox', { name: 'Eating out' }))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.budgetGroups.update).toHaveBeenCalledWith('g-1', expect.objectContaining({
      category_ids: ['cat-food'], end_month: `${YEAR + 1}-03-01`, month: `${MONTH}-01`,
    })))

    await user.click(within(await screen.findByTestId('budget-group')).getByRole('button', { name: t('common.delete') }))
    await user.click(await screen.findByRole('button', { name: t('common.delete') }))
    await waitFor(() => expect(api.budgetGroups.delete).toHaveBeenCalledWith('g-1'))
  })

  it('a new group cap from a later month starts there, keeping the earlier months', async () => {
    api.budgetGroups.comparison.mockResolvedValue([{ ...group, month: lastMonth() }])
    const { user } = renderWithProviders(<BudgetsPage />)

    await user.click(within(await screen.findByTestId('budget-group')).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    await user.clear(within(dialog).getByLabelText(t('budgets.groupCap')))
    await user.type(within(dialog).getByLabelText(t('budgets.groupCap')), '500')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgetGroups.update).toHaveBeenCalledWith('g-1', expect.objectContaining({
      amount: 500, month: lastMonth(), effective_month: `${MONTH}-01`,
    })))
  })

  it('a group edited without a new cap leaves its caps alone', async () => {
    api.budgetGroups.comparison.mockResolvedValue([{ ...group, month: lastMonth() }])
    const { user } = renderWithProviders(<BudgetsPage />)

    await user.click(within(await screen.findByTestId('budget-group')).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(within(dialog).getByTestId('group-categories')).getByRole('checkbox', { name: 'Eating out' }))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgetGroups.update).toHaveBeenCalled())
    expect(api.budgetGroups.update.mock.calls[0][1]).not.toHaveProperty('effective_month')
    // The cap shown is this month's; unchanged, it is not sent at all.
    expect(api.budgetGroups.update.mock.calls[0][1]).not.toHaveProperty('amount')
  })

  it('a recurring group made a single month while viewing a later month becomes that month\'s', async () => {
    api.budgetGroups.comparison.mockResolvedValue([{ ...group, month: lastMonth() }])
    const { user } = renderWithProviders(<BudgetsPage />)

    await user.click(within(await screen.findByTestId('budget-group')).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('checkbox', { name: t('budgets.repeatEveryMonth') }))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgetGroups.update).toHaveBeenCalledWith('g-1', expect.objectContaining({
      month: `${MONTH}-01`, is_recurring: false, end_month: null,
    })))
  })
})
