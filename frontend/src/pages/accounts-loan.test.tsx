import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import AccountsPage from '@/pages/accounts'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, Asset, BankConnection } from '@/types'

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

function asset(overrides: Partial<Asset>): Asset {
  return { id: 'asset', name: 'Asset', type: 'other', is_archived: false, ...overrides } as Asset
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
  it('creates a manual loan with the amount owed, a subtype and a linked asset', async () => {
    const { user } = renderWithProviders(<AccountsPage />)

    await user.click(await screen.findByRole('button', { name: t('accounts.addManual') }))
    const dialog = await screen.findByRole('dialog')

    await user.type(within(dialog).getAllByRole('textbox')[0], 'Home loan')
    await user.selectOptions(within(dialog).getAllByRole('combobox')[0], 'loan')

    expect(within(dialog).getByText(t('accounts.loan.amountOwed'))).toBeInTheDocument()
    expect(within(dialog).getByText(t('accounts.loan.amountOwedHint'))).toBeInTheDocument()
    const balance = within(dialog).getByRole('spinbutton')
    await user.clear(balance)
    await user.type(balance, '300000')

    await user.selectOptions(within(dialog).getByLabelText(t('accounts.loan.subtype')), 'mortgage')
    const assetSelect = within(dialog).getByLabelText(t('accounts.loan.linkedAsset'))
    await within(assetSelect).findByRole('option', { name: 'House' })
    // Archived assets are not offered.
    expect(within(assetSelect).queryByRole('option', { name: 'Old boat' })).not.toBeInTheDocument()
    await user.selectOptions(assetSelect, 'house')

    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.accounts.create).toHaveBeenCalledTimes(1))
    expect(api.accounts.create.mock.calls[0][0]).toMatchObject({
      name: 'Home loan',
      type: 'loan',
      balance: 300000,
      loan_subtype: 'mortgage',
      secured_asset_id: 'house',
    })
  })

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

  it('flips the entered balance when a manual account becomes a loan, so net worth is unchanged', async () => {
    api.accounts.list.mockResolvedValue([
      account({ id: 'overdrawn', name: 'Overdrawn', balance: -5000, current_balance: -5000 }),
    ])
    const { user } = renderWithProviders(<AccountsPage />)

    const dialog = await openEditDialog(user, 'Overdrawn')
    const balance = within(dialog).getByRole('spinbutton')
    expect(balance).toHaveValue(-5000)

    await user.selectOptions(within(dialog).getAllByRole('combobox')[0], 'loan')
    expect(balance).toHaveValue(5000)
    // Back again restores the signed figure.
    await user.selectOptions(within(dialog).getAllByRole('combobox')[0], 'checking')
    expect(balance).toHaveValue(-5000)
    await user.selectOptions(within(dialog).getAllByRole('combobox')[0], 'loan')

    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.accounts.update).toHaveBeenCalledTimes(1))
    const [id, payload] = api.accounts.update.mock.calls[0]
    expect(id).toBe('overdrawn')
    expect(payload).toMatchObject({
      type: 'loan',
      balance: 5000,
      loan_subtype: null,
      secured_asset_id: null,
    })
  })

  it('keeps the entered amount when a loan becomes a credit card', async () => {
    api.accounts.list.mockResolvedValue([
      account({ id: 'loan', name: 'Personal loan', type: 'loan', balance: 1200, current_balance: -1200 }),
    ])
    const { user } = renderWithProviders(<AccountsPage />)

    const dialog = await openEditDialog(user, 'Personal loan')
    const balance = within(dialog).getByRole('spinbutton')
    expect(balance).toHaveValue(1200)
    await user.selectOptions(within(dialog).getAllByRole('combobox')[0], 'credit_card')
    expect(balance).toHaveValue(1200)
  })

  it('edits a loan, keeping its archived linked asset selectable and clearing the subtype', async () => {
    api.accounts.list.mockResolvedValue([
      account({
        id: 'boat-loan',
        name: 'Boat loan',
        type: 'loan',
        balance: 800,
        current_balance: -800,
        loan_subtype: 'vehicle',
        secured_asset_id: 'old-boat',
      }),
    ])
    const { user } = renderWithProviders(<AccountsPage />)

    const dialog = await openEditDialog(user, 'Boat loan')
    const subtype = within(dialog).getByLabelText(t('accounts.loan.subtype'))
    expect(subtype).toHaveValue('vehicle')
    const assetSelect = within(dialog).getByLabelText(t('accounts.loan.linkedAsset'))
    await within(assetSelect).findByRole('option', { name: 'Old boat' })
    expect(assetSelect).toHaveValue('old-boat')

    await user.selectOptions(subtype, '')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.accounts.update).toHaveBeenCalledTimes(1))
    expect(api.accounts.update.mock.calls[0][1]).toMatchObject({
      type: 'loan',
      balance: 800,
      loan_subtype: null,
      secured_asset_id: 'old-boat',
    })
  })

  it('turns a connected account into a loan linked to any asset type, without touching its balance', async () => {
    api.connections.list.mockResolvedValue([connection])
    api.accounts.list.mockResolvedValue([
      account({
        id: 'synced',
        name: 'Synced mortgage',
        connection_id: 'conn-1',
        balance: -250000,
        current_balance: -250000,
      }),
    ])
    const { user } = renderWithProviders(<AccountsPage />)

    const dialog = await openEditDialog(user, 'Synced mortgage')
    expect(within(dialog).queryByRole('spinbutton')).not.toBeInTheDocument()

    await user.selectOptions(within(dialog).getAllByRole('combobox')[0], 'loan')
    await user.selectOptions(within(dialog).getByLabelText(t('accounts.loan.subtype')), 'vehicle')
    const assetSelect = within(dialog).getByLabelText(t('accounts.loan.linkedAsset'))
    await within(assetSelect).findByRole('option', { name: 'Car' })
    await user.selectOptions(assetSelect, 'car')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.accounts.update).toHaveBeenCalledTimes(1))
    const payload = api.accounts.update.mock.calls[0][1]
    expect(payload).toMatchObject({ type: 'loan', loan_subtype: 'vehicle', secured_asset_id: 'car' })
    expect(payload).not.toHaveProperty('balance')
    expect(payload).not.toHaveProperty('name')
  })
})
