import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import BudgetsPage from '@/pages/budgets'
import { renderWithProviders, t } from '@/test/utils'
import type { Budget, BudgetGroupVsActual, Category } from '@/types'

// Fork: recurring budgets with an end month, and budget groups. Made-up names
// and figures.

const api = vi.hoisted(() => ({
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
  budgets: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  budgetGroups: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), comparison: vi.fn() },
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

const budget = (values: Partial<Budget>): Budget => ({
  id: 'b-1', user_id: 'u', category_id: 'cat-food', amount: 400, month: '2026-10-01', is_recurring: true,
  end_month: null, ...values,
})

const group: BudgetGroupVsActual = {
  id: 'g-1', name: 'Everyday', amount: '600.00', month: '2026-10-01', is_recurring: true, end_month: '2026-12-01',
  category_ids: ['cat-food', 'cat-out'], actual_amount: '150.00', projected_amount: '450.00', percentage_used: 75,
}

beforeEach(() => {
  vi.clearAllMocks()
  api.categories.list.mockResolvedValue(categories)
  api.categories.listIncludingHidden.mockResolvedValue(categories)
  api.categoryGroups.list.mockResolvedValue([])
  api.budgets.list.mockResolvedValue([])
  api.budgets.create.mockResolvedValue({})
  api.budgets.update.mockResolvedValue({})
  api.budgetGroups.comparison.mockResolvedValue([])
  api.budgetGroups.create.mockResolvedValue({})
  api.budgetGroups.update.mockResolvedValue({})
  api.budgetGroups.delete.mockResolvedValue(undefined)
})

describe('budget end month', () => {
  it('shows "until" and sends the end month of a new recurring budget', async () => {
    api.budgets.list.mockResolvedValue([budget({ end_month: '2027-03-01' })])
    const { user } = renderWithProviders(<BudgetsPage />)

    expect(await screen.findByTestId('budget-until')).toHaveTextContent(/until .*2027/i)

    await user.click(screen.getByRole('button', { name: t('budgets.add') }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).queryByLabelText(t('budgets.untilOptional'))).not.toBeInTheDocument()
    await waitFor(() => expect(within(dialog).getByRole('option', { name: 'Eating out' })).toBeInTheDocument())
    await user.selectOptions(within(dialog).getByRole('combobox'), 'cat-out')
    await user.click(within(dialog).getByRole('checkbox'))
    fireEvent.change(within(dialog).getByLabelText(t('budgets.untilOptional')), { target: { value: '2027-06' } })
    await user.type(within(dialog).getByRole('spinbutton'), '250')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgets.create).toHaveBeenCalledWith(expect.objectContaining({
      category_id: 'cat-out', amount: 250, is_recurring: true, end_month: '2027-06-01',
    })))
  })

  it('edits a recurring budget\'s end month, and clears it', async () => {
    api.budgets.list.mockResolvedValue([budget({ end_month: '2027-03-01' })])
    const { user } = renderWithProviders(<BudgetsPage />)

    await user.click(await screen.findByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')
    const until = within(dialog).getByLabelText(t('budgets.untilOptional'))
    expect(until).toHaveValue('2027-03')
    fireEvent.change(until, { target: { value: '' } })
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgets.update).toHaveBeenCalledWith('b-1', { amount: 400, end_month: null }))
  })
})

describe('budget groups', () => {
  it('lists a group with its spending against the cap', async () => {
    api.budgetGroups.comparison.mockResolvedValue([group])
    renderWithProviders(<BudgetsPage />)

    const row = await screen.findByTestId('budget-group')
    expect(row).toHaveTextContent('Everyday')
    expect(row).toHaveTextContent('Groceries, Eating out')
    expect(row).toHaveTextContent('$450.00 of $600.00')
    expect(within(row).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '75')
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
    fireEvent.change(within(dialog).getByLabelText(t('budgets.untilOptional')), { target: { value: '2026-12' } })
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.budgetGroups.create).toHaveBeenCalledWith({
      name: 'Everyday', amount: 600, month: expect.stringMatching(/^\d{4}-\d{2}-01$/), is_recurring: true,
      end_month: '2026-12-01', category_ids: ['cat-food', 'cat-out'],
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
      category_ids: ['cat-food'], end_month: '2026-12-01', month: '2026-10-01',
    })))

    await user.click(within(await screen.findByTestId('budget-group')).getByRole('button', { name: t('common.delete') }))
    await user.click(await screen.findByRole('button', { name: t('common.delete') }))
    await waitFor(() => expect(api.budgetGroups.delete).toHaveBeenCalledWith('g-1'))
  })
})
