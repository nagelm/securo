import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import RecurringPage from '@/pages/recurring'
import { nthOccurrence } from '@/lib/recurring-schedule'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Category, Payee, RecurringTransaction } from '@/types'

// Fork: one kind of recurring item. Every item is listed and edited here,
// transfers between own accounts included, with how it ends (never, on a
// date, after a number of payments). Made-up names and figures.

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

const account = (id: string, name: string, position: number) =>
  ({
    id, name, display_name: null, type: 'checking', currency: 'USD', position, connection_id: null, is_closed: false,
  }) as unknown as Account

const accounts = [account('chk-1', 'Everyday', 0), account('sav-1', 'Rainy day', 1)]

const groceries = {
  id: 'cat-groceries',
  name: 'Groceries',
  icon: 'shopping-cart',
  color: '#22c55e',
} as unknown as Category

const payees = [{ id: 'payee-stream', name: 'Streaming Co' }] as Payee[]

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
  api.accounts.list.mockResolvedValue(accounts)
  api.currencies.list.mockResolvedValue([{ code: 'USD', symbol: '$', name: 'US Dollar', flag: '' }])
  api.payees.list.mockResolvedValue(payees)
})

async function rowOf(description: string) {
  const cell = await screen.findByText(description)
  return cell.closest('tr') as HTMLTableRowElement
}

async function openNew(user: ReturnType<typeof renderWithProviders>['user']) {
  await waitFor(() => expect(api.accounts.list).toHaveBeenCalled())
  await user.click(await screen.findByRole('button', { name: t('recurring.add') }))
  const dialog = await screen.findByRole('dialog')
  await waitFor(() => expect(within(dialog).getByLabelText(t('recurring.account'))).toHaveValue('chk-1'))
  await user.type(within(dialog).getByRole('textbox'), 'Allowance')
  await user.type(within(dialog).getAllByRole('spinbutton')[0], '25')
  return dialog
}

describe('recurring page: one kind of item (fork)', () => {
  it('lists every item, with a transfer\'s accounts and how many payments are left', async () => {
    api.recurring.list.mockResolvedValue([
      recurring({ id: 'r1', description: 'Rent' }),
      recurring({
        id: 'r2', description: 'Allowance', frequency: 'weekly', start_date: '2026-10-16',
        next_occurrence: '2027-01-15', end_date: '2027-01-29', transfer_account_id: 'sav-1', auto_generate: false,
      }),
      recurring({ id: 'r3', description: 'Old plan', end_date: '2026-02-01', is_active: false }),
    ])
    renderWithProviders(<RecurringPage />)

    expect(await rowOf('Rent')).not.toHaveTextContent(/until|left/)
    const allowance = await rowOf('Allowance')
    await waitFor(() =>
      expect(within(allowance).getByTestId('recurring-transfer')).toHaveTextContent('Everyday → Rainy day'),
    )
    expect(within(allowance).getByTestId('recurring-end')).toHaveTextContent('until Jan 29, 2027 · 3 left')
    // A transfer has no sign.
    expect(allowance).toHaveTextContent('$20.00')
    expect(allowance).not.toHaveTextContent('−$20.00')
    expect(within(await rowOf('Old plan')).getByTestId('recurring-end')).toHaveTextContent('ended Feb 1, 2026')
  })

  it('ends a new item after a number of payments', async () => {
    const { user } = renderWithProviders(<RecurringPage />)
    const dialog = await openNew(user)

    await user.selectOptions(within(dialog).getByLabelText(t('recurring.frequency')), 'weekly')
    await user.selectOptions(within(dialog).getByLabelText(t('recurring.ends')), 'after')
    await user.type(within(dialog).getByLabelText(t('recurring.endsAfterCount')), '16')
    expect(within(dialog).getByTestId('recurring-last-payment')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.recurring.create).toHaveBeenCalledTimes(1))
    const payload = api.recurring.create.mock.calls[0][0]
    expect(payload.end_date).toBe(nthOccurrence(payload.start_date, 'weekly', 16))
    expect(payload.transfer_account_id).toBeNull()
  })

  it('makes a transfer to another own account, never generated', async () => {
    const { user } = renderWithProviders(<RecurringPage />)
    const dialog = await openNew(user)

    await user.selectOptions(within(dialog).getByLabelText(t('recurring.type')), 'transfer')
    expect(within(dialog).getByLabelText(t('recurring.fromAccount'))).toHaveValue('chk-1')
    const to = within(dialog).getByLabelText(t('recurring.transferTo'))
    expect(within(to).queryByRole('option', { name: 'Everyday' })).not.toBeInTheDocument()
    await user.selectOptions(to, 'sav-1')
    expect(within(dialog).getByRole('checkbox', { name: new RegExp(t('recurring.autoGenerate')) })).toBeDisabled()
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.recurring.create).toHaveBeenCalledWith(expect.objectContaining({
      type: 'debit', account_id: 'chk-1', transfer_account_id: 'sav-1', auto_generate: false, end_date: null,
    })))
  })

  it('edits a transfer\'s end date and can make it open-ended', async () => {
    api.recurring.list.mockResolvedValue([
      recurring({ id: 'r2', description: 'Allowance', end_date: '2027-01-29', transfer_account_id: 'sav-1' }),
    ])
    const { user } = renderWithProviders(<RecurringPage />)
    await user.click(within(await rowOf('Allowance')).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByLabelText(t('recurring.type'))).toHaveValue('transfer')
    expect(within(dialog).getByLabelText(t('recurring.ends'))).toHaveValue('on_date')
    await user.selectOptions(within(dialog).getByLabelText(t('recurring.ends')), 'never')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.recurring.update).toHaveBeenCalledWith('r2', expect.objectContaining({
      end_date: null, transfer_account_id: 'sav-1', type: 'debit',
    })))
  })
})
