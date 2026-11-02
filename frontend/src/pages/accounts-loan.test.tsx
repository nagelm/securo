import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import AccountsPage from '@/pages/accounts'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Asset } from '@/types'

const api = vi.hoisted(() => ({
  accounts: {
    list: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    close: vi.fn(),
    reopen: vi.fn(),
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

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: true }),
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
    secured_asset_id: null,
    loan_details: null,
    is_closed: false,
    closed_at: null,
    ...overrides,
  }
}

function asset(overrides: Partial<Asset>): Asset {
  return { id: 'asset', name: 'Asset', type: 'other', is_archived: false, ...overrides } as Asset
}

const assetsList = [
  asset({ id: 'house', name: 'House', type: 'real_estate' }),
  asset({ id: 'car', name: 'Car', type: 'vehicle' }),
  asset({ id: 'old-boat', name: 'Old boat', type: 'vehicle', is_archived: true }),
]

beforeEach(() => {
  vi.clearAllMocks()
  api.accounts.list.mockResolvedValue([])
  api.accounts.create.mockImplementation(async (data) => account({ id: 'new', ...data }))
  api.accounts.update.mockImplementation(async (id, data) => account({ id, ...data }))
  api.assets.list.mockResolvedValue(assetsList)
  api.connections.list.mockResolvedValue([])
  api.connections.getProviders.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([{ code: 'USD', symbol: '$', name: 'US Dollar', flag: '' }])
})

async function openEditDialog(user: ReturnType<typeof renderWithProviders>['user'], name: string) {
  await screen.findByText(name)
  await user.click(screen.getAllByRole('button', { name: t('common.edit') })[0])
  return screen.findByRole('dialog')
}

describe('Account dialog: loan accounts', () => {
  it('sends no loan fields for other account types', async () => {
    const { user } = renderWithProviders(<AccountsPage />)

    await user.click(await screen.findByRole('button', { name: t('accounts.addManual') }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getAllByRole('textbox')[0], 'Everyday')

    expect(within(dialog).queryByLabelText(t('accounts.loan.subtype'))).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.accounts.create).toHaveBeenCalledTimes(1))
    const payload = api.accounts.create.mock.calls[0][0]
    expect(payload.type).toBe('checking')
    expect(payload).not.toHaveProperty('loan_subtype')
    expect(payload).not.toHaveProperty('secured_asset_id')
  })

})

describe('Account list: revolving loans', () => {
  // Made-up figures: a 10,000 line with 4,000 drawn.
  const line = account({
    id: 'line',
    name: 'Test credit line',
    type: 'loan',
    balance: 4000,
    current_balance: -4000,
    loan_details: { type: 'REVOLVING' },
    credit_limit: 10000,
    available_credit: 6000,
  })

  it('shows available credit under a revolving loan, as under a card', async () => {
    api.accounts.list.mockResolvedValue([line])
    renderWithProviders(<AccountsPage />)

    expect(await screen.findByText(`${t('accounts.availableCredit')}: $6,000.00`)).toBeInTheDocument()
  })

  it('shows none under a loan that is not revolving', async () => {
    api.accounts.list.mockResolvedValue([account({ ...line, loan_details: { type: 'TABLE' } })])
    renderWithProviders(<AccountsPage />)

    await screen.findByText('Test credit line')
    expect(screen.queryByText(new RegExp(t('accounts.availableCredit')))).not.toBeInTheDocument()
  })

  it('never sends the limit from the account dialog, so editing a revolving loan keeps it', async () => {
    api.accounts.list.mockResolvedValue([line])
    const { user } = renderWithProviders(<AccountsPage />)

    const dialog = await openEditDialog(user, 'Test credit line')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.accounts.update).toHaveBeenCalledTimes(1))
    expect(api.accounts.update.mock.calls[0][1]).not.toHaveProperty('credit_limit')
  })
})
