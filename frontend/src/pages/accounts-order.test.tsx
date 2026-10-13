import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import AccountsPage from '@/pages/accounts'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, BankConnection } from '@/types'

// The user's own account order on the Accounts page: move up/down per row,
// one group at a time, shown at once and undone if the save fails.
// Every name and figure is made up.

const api = vi.hoisted(() => ({
  accounts: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    close: vi.fn(),
    reopen: vi.fn(),
    reorder: vi.fn(),
  },
  assets: { list: vi.fn() },
  connections: {
    list: vi.fn(),
    getProviders: vi.fn(),
    sync: vi.fn(),
    delete: vi.fn(),
    getReauthUrl: vi.fn(),
  },
  currencies: { list: vi.fn() },
}))
const workspace = vi.hoisted(() => ({ canWrite: true }))
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }))

vi.mock('@/lib/api', () => api)
vi.mock('sonner', () => ({ toast }))

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

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => workspace,
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
    position: 0,
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

const connection = {
  id: 'conn-1',
  provider: 'example',
  institution_name: 'Example Bank',
  status: 'active',
  last_sync_at: null,
  logo_url: null,
  external_id: 'ext-1',
} as unknown as BankConnection

// API order: positions interleave the manual and the bank's accounts.
const listed = [
  account({ id: 'wallet', name: 'Wallet', position: 0 }),
  account({ id: 'everyday', name: 'Everyday', connection_id: 'conn-1', position: 1 }),
  account({ id: 'savings', name: 'Savings jar', position: 2 }),
  account({ id: 'card', name: 'Card', connection_id: 'conn-1', type: 'credit_card', position: 3 }),
  account({ id: 'cash', name: 'Cash box', position: 4 }),
]

function rowNames(section: HTMLElement): string[] {
  return within(section)
    .getAllByRole('link')
    .map((link) => listed.find((a) => link.textContent?.startsWith(a.name))?.name ?? link.textContent ?? '')
}

function section(heading: string): HTMLElement {
  return screen.getByText(heading).closest('.rounded-xl') as HTMLElement
}

function row(name: string): HTMLElement {
  return screen.getByRole('link', { name: new RegExp(`^${name}`) }).parentElement as HTMLElement
}

beforeEach(() => {
  vi.clearAllMocks()
  workspace.canWrite = true
  api.accounts.list.mockImplementation(async () => listed)
  api.accounts.reorder.mockResolvedValue(undefined)
  api.assets.list.mockResolvedValue([])
  api.connections.list.mockResolvedValue([connection])
  api.connections.getProviders.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([{ code: 'USD', symbol: '$', name: 'US Dollar', flag: '' }])
})

describe('Accounts page: account order', () => {
  it('lists each group in the order the API gives', async () => {
    renderWithProviders(<AccountsPage />)
    await screen.findByText('Wallet')

    expect(rowNames(section(t('accounts.manualAccounts')))).toEqual(['Wallet', 'Savings jar', 'Cash box'])
    expect(rowNames(section('Example Bank'))).toEqual(['Everyday', 'Card'])
  })

  it('moves an account down within its group, sends only that group, and shows it at once', async () => {
    let finish: () => void = () => {}
    api.accounts.reorder.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve }))
    const { user } = renderWithProviders(<AccountsPage />)
    await screen.findByText('Wallet')

    await user.click(within(row('Wallet')).getByRole('button', { name: t('accounts.moveDown') }))

    expect(api.accounts.reorder).toHaveBeenCalledWith(['savings', 'wallet', 'cash'])
    // Before the server answers.
    await waitFor(() =>
      expect(rowNames(section(t('accounts.manualAccounts')))).toEqual(['Savings jar', 'Wallet', 'Cash box']),
    )
    expect(rowNames(section('Example Bank'))).toEqual(['Everyday', 'Card'])
    finish()
  })

  it("moves a bank account up and sends just the connection's accounts", async () => {
    const { user } = renderWithProviders(<AccountsPage />)
    await screen.findByText('Card')

    await user.click(within(row('Card')).getByRole('button', { name: t('accounts.moveUp') }))

    expect(api.accounts.reorder).toHaveBeenCalledWith(['card', 'everyday'])
  })

  it('cannot move the first row up or the last row down', async () => {
    renderWithProviders(<AccountsPage />)
    await screen.findByText('Wallet')

    expect(within(row('Wallet')).getByRole('button', { name: t('accounts.moveUp') })).toBeDisabled()
    expect(within(row('Wallet')).getByRole('button', { name: t('accounts.moveDown') })).toBeEnabled()
    expect(within(row('Cash box')).getByRole('button', { name: t('accounts.moveDown') })).toBeDisabled()
  })

  it('puts the old order back when the save fails', async () => {
    api.accounts.reorder.mockRejectedValue(new Error('offline'))
    // Only the first load answers (open and closed lists), so the order
    // shown after the failure is the rollback, not a refetch.
    let loads = 0
    api.accounts.list.mockImplementation(() => (++loads <= 2 ? Promise.resolve(listed) : new Promise(() => {})))
    const { user } = renderWithProviders(<AccountsPage />)
    await screen.findByText('Wallet')

    await user.click(within(row('Savings jar')).getByRole('button', { name: t('accounts.moveUp') }))

    await waitFor(() => expect(toast.error).toHaveBeenCalled())
    await waitFor(() =>
      expect(rowNames(section(t('accounts.manualAccounts')))).toEqual(['Wallet', 'Savings jar', 'Cash box']),
    )
  })

  it('offers a drag handle per row', async () => {
    renderWithProviders(<AccountsPage />)
    await screen.findByText('Wallet')

    expect(
      within(row('Wallet')).getByRole('button', { name: t('accounts.dragToReorder', { name: 'Wallet' }) }),
    ).toBeInTheDocument()
  })

  it('offers no reordering to a read-only member', async () => {
    workspace.canWrite = false
    renderWithProviders(<AccountsPage />)
    await screen.findByText('Wallet')

    expect(screen.queryByRole('button', { name: t('accounts.moveUp') })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: t('accounts.dragToReorder', { name: 'Wallet' }) }),
    ).not.toBeInTheDocument()
  })
})
