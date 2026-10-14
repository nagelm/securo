import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import RecurringPage from '@/pages/recurring'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Category, Payee, RecurringTransaction } from '@/types'

// Fork: recurring items show their payee's icon and pick a payee in the form.

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

const LOGO = 'https://logo.example/streaming.png'

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

async function rowOf(description: string) {
  const cell = await screen.findByText(description)
  return cell.closest('tr') as HTMLTableRowElement
}

describe('recurring payee icons', () => {
  it('draws the payee logo, the picked icon, else the category icon', async () => {
    api.recurring.list.mockResolvedValue([
      recurring({ id: 'r1', description: 'Streaming', payee_id: 'payee-stream', payee_logo_url: LOGO }),
      recurring({ id: 'r2', description: 'Milk', payee_id: 'payee-dairy', payee_icon: 'tv', payee_logo_url: LOGO }),
      recurring({ id: 'r3', description: 'Market' }),
    ])
    renderWithProviders(<RecurringPage />)

    expect((await rowOf('Streaming')).querySelector('img')).toHaveAttribute('src', LOGO)

    const milk = await rowOf('Milk')
    expect(milk.querySelector('img')).toBeNull()
    expect(milk.querySelector('svg')).toHaveClass('lucide-tv')

    await waitFor(async () =>
      expect((await rowOf('Market')).querySelector('svg')).toHaveClass('lucide-shopping-cart'),
    )
    expect((await rowOf('Market')).querySelector('img')).toBeNull()
  })
})

describe('recurring payee field', () => {
  it('sends the picked payee on create', async () => {
    const { user } = renderWithProviders(<RecurringPage />)
    await waitFor(() => expect(api.accounts.list).toHaveBeenCalled())
    await user.click(await screen.findByRole('button', { name: t('recurring.add') }))
    const dialog = await screen.findByRole('dialog')

    expect(within(dialog).getByText(t('payees.payee'))).toBeInTheDocument()
    await user.type(within(dialog).getByRole('textbox'), 'Streaming')
    await user.type(within(dialog).getAllByRole('spinbutton')[0], '20')
    await user.click(within(dialog).getByRole('button', { name: t('payees.noPayee') }))
    await user.click(await screen.findByText('Streaming Co'))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.recurring.create).toHaveBeenCalledTimes(1))
    expect(api.recurring.create.mock.calls[0][0]).toMatchObject({
      description: 'Streaming',
      payee_id: 'payee-stream',
    })
  })

  it('shows the current payee on edit and can clear it', async () => {
    api.recurring.list.mockResolvedValue([
      recurring({ id: 'r1', description: 'Streaming', payee_id: 'payee-stream', payee_name: 'Streaming Co' }),
    ])
    const { user } = renderWithProviders(<RecurringPage />)
    const row = await rowOf('Streaming')
    await user.click(within(row).getByRole('button', { name: t('common.edit') }))
    const dialog = await screen.findByRole('dialog')

    const trigger = await within(dialog).findByRole('button', { name: 'Streaming Co' })
    await user.click(trigger)
    await user.click(await screen.findByRole('option', { name: t('payees.noPayee') }))
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.recurring.update).toHaveBeenCalledTimes(1))
    expect(api.recurring.update.mock.calls[0]).toEqual(['r1', expect.objectContaining({ payee_id: null })])
  })
})
