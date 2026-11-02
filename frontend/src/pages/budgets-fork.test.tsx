import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import BudgetsPage from '@/pages/budgets'
import { createTestQueryClient, renderWithProviders, t } from '@/test/utils'
import type { Budget, BudgetGroupVsActual, BudgetVsActual, Category } from '@/types'

// Fork: recurring budgets with an end month, budget groups drawn like
// budgets, and "Include in forecast". Made-up names and figures.

const api = vi.hoisted(() => ({
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  budgets: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), comparison: vi.fn() },
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

const spending = (values: Partial<BudgetVsActual>): BudgetVsActual => ({
  category_id: 'cat-food', category_name: 'Groceries', category_icon: 'tag', category_color: '#123456',
  group_id: null, group_name: null, budget_amount: 400, actual_amount: 100, projected_amount: 300,
  prev_month_amount: 0, projected_prev_month_amount: 0, percentage_used: 75, is_recurring: true, ...values,
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
  it('shows "until" and sends the end month of a new recurring budget', async () => {
    api.budgets.list.mockResolvedValue([budget({ end_month: `${YEAR + 1}-03-01` })])
    const { user } = renderWithProviders(<BudgetsPage />)

    expect(await screen.findByTestId('budget-until')).toHaveTextContent(`until March ${YEAR + 1}`)

    await user.click(screen.getByRole('button', { name: t('budgets.add') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText(t('budgets.untilOptional'))).not.toBeInTheDocument()
    await waitFor(() => expect(within(dialog).getByRole('option', { name: 'Eating out' })).toBeInTheDocument())
    await user.selectOptions(within(dialog).getByLabelText(t('budgets.category')), 'cat-out')
    await user.click(within(dialog).getByRole('checkbox'))
    await pickMonth(user, within(dialog).getByLabelText(t('budgets.untilOptional')), 'Dec')
    await user.type(within(dialog).getByLabelText(t('budgets.amount')), '250')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgets.create).toHaveBeenCalledWith(expect.objectContaining({
      category_id: 'cat-out', amount: 250, is_recurring: true, end_month: `${YEAR}-12-01`,
    })))
  })

  it('names the end month in the form (no raw yyyy-MM text) and clears it only on Clear', async () => {
    api.budgets.list.mockResolvedValue([budget({ end_month: `${YEAR + 1}-03-01` })])
    const { user } = renderWithProviders(<BudgetsPage />)

    await user.click(within(await screen.findByTestId('budget-row')).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    const until = within(dialog).getByLabelText(t('budgets.untilOptional'))
    expect(until).toHaveTextContent(`March ${YEAR + 1}`)
    expect(until.tagName).toBe('BUTTON')
    await user.click(within(dialog).getByRole('button', { name: t('budgets.clearEnd') }))
    expect(within(dialog).getByLabelText(t('budgets.untilOptional'))).toHaveTextContent(t('budgets.noEnd'))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgets.update).toHaveBeenCalledWith('b-1', { amount: 400, end_month: null }))
  })

  it('a new amount from a later month starts there, keeping the earlier months', async () => {
    // A recurring budget that started last month, edited while viewing this one.
    api.budgets.list.mockResolvedValue([budget({ month: lastMonth() })])
    const { user } = renderWithProviders(<BudgetsPage />)

    await user.click(within(await screen.findByTestId('budget-row')).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    await pickMonth(user, within(dialog).getByLabelText(t('budgets.untilOptional')), 'Dec')
    await user.clear(within(dialog).getByLabelText(t('budgets.amount')))
    await user.type(within(dialog).getByLabelText(t('budgets.amount')), '500')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgets.update).toHaveBeenCalledWith('b-1', {
      amount: 500, end_month: `${YEAR}-12-01`, effective_month: `${MONTH}-01`,
    }))
  })

  it('an end month set on the Budgets page refreshes the forecast', async () => {
    api.budgets.list.mockResolvedValue([budget({})])
    const queryClient = createTestQueryClient()
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    const { user } = renderWithProviders(<BudgetsPage />, { queryClient })

    await user.click(within(await screen.findByTestId('budget-row')).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    await pickMonth(user, within(dialog).getByLabelText(t('budgets.untilOptional')), 'Dec')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgets.update).toHaveBeenCalled())
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['forecast'] }))
  })
})

describe('budget rows', () => {
  it('shows each budget\'s spending against its limit and its forecast toggle', async () => {
    api.budgets.list.mockResolvedValue([budget({})])
    api.budgets.comparison.mockResolvedValue([spending({})])
    const { user } = renderWithProviders(<BudgetsPage />)

    const row = await screen.findByTestId('budget-row')
    await waitFor(() => expect(row).toHaveTextContent('$300.00 of $400.00'))
    expect(within(row).getAllByRole('progressbar')[0]).toHaveAttribute('aria-valuenow', '75')
    const toggle = within(row).getByRole('switch', { name: t('budgets.includeInForecastFor', { name: 'Groceries' }) })
    await waitFor(() => expect(toggle).toBeEnabled())
    expect(toggle).toHaveAttribute('aria-checked', 'false')
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
