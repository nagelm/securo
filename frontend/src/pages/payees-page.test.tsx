import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import PayeesPage from '@/pages/payees'
import { PAYEE_SORT_STORAGE_KEY } from '@/lib/payee-sorting'
import { renderWithProviders, t } from '@/test/utils'

const api = vi.hoisted(() => ({
  payees: {
    list: vi.fn(),
    summary: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    merge: vi.fn(),
    bulkDelete: vi.fn(),
  },
  transactions: { list: vi.fn() },
  fiscal: { taxIdKinds: vi.fn() },
  settings: { logos: vi.fn() },
}))

const workspace = vi.hoisted(() => ({ canWrite: false }))

vi.mock('@/lib/api', () => ({
  payees: api.payees,
  transactions: api.transactions,
  fiscal: api.fiscal,
  settings: api.settings,
}))

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/workspace-context', () => ({
  useWorkspace: () => ({ canWrite: workspace.canWrite }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value }),
}))

const payees = [
  {
    id: 'alpha', user_id: 'user-1', name: 'Alpha', type: 'person', source: 'manual', is_favorite: false,
    notes: null, email: null, phone: null, address: null, website: null,
    logo_url: null, icon: null, display_logo_url: null, tax_ids: [],
    created_at: '2026-01-01T00:00:00Z', transaction_count: 1,
  },
  {
    id: 'zeta', user_id: 'user-1', name: 'Zeta', type: 'company', source: 'manual', is_favorite: false,
    notes: null, email: null, phone: null, address: null, website: null,
    logo_url: null, icon: null, display_logo_url: null, tax_ids: [],
    created_at: '2026-01-01T00:00:00Z', transaction_count: 3,
  },
]

function firstPayeeRow() {
  return screen.getAllByRole('row')[1]
}

describe('PayeesPage sorting', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workspace.canWrite = false
    api.payees.list.mockResolvedValue(payees)
    api.fiscal.taxIdKinds.mockResolvedValue({ kinds: [], jurisdiction: null })
    api.settings.logos.mockResolvedValue({ logo_size: 128 })
  })

  it('sorts transactions descending then ascending and persists the choice', async () => {
    const { user } = renderWithProviders(<PayeesPage />, { route: '/payees' })
    await screen.findByText('Alpha')

    const transactionsHeader = screen.getByRole('button', { name: t('payees.transactionCount') })
    await user.click(transactionsHeader)

    expect(within(firstPayeeRow()).getByText('Zeta')).toBeInTheDocument()
    expect(transactionsHeader).toHaveAccessibleName(t('payees.transactionCount'))
    expect(transactionsHeader.closest('th')).toHaveAttribute('aria-sort', 'descending')
    await waitFor(() => expect(localStorage.getItem(PAYEE_SORT_STORAGE_KEY)).toBe(
      JSON.stringify({ by: 'transaction_count', direction: 'desc' }),
    ))

    await user.click(transactionsHeader)

    expect(within(firstPayeeRow()).getByText('Alpha')).toBeInTheDocument()
    expect(transactionsHeader.closest('th')).toHaveAttribute('aria-sort', 'ascending')
    await waitFor(() => expect(localStorage.getItem(PAYEE_SORT_STORAGE_KEY)).toBe(
      JSON.stringify({ by: 'transaction_count', direction: 'asc' }),
    ))
  })
})

const FAVICON = 'https://www.google.com/s2/favicons?domain=shop.example&sz=128'

function shop(overrides: Record<string, unknown> = {}) {
  return {
    ...payees[0], id: 'shop', name: 'Shop', ...overrides,
  }
}

async function openShopDialog(payee: ReturnType<typeof shop>, route = '/payees') {
  api.payees.list.mockResolvedValue([payee])
  const rendered = renderWithProviders(<PayeesPage />, { route })
  await screen.findByText('Shop')
  await rendered.user.click(screen.getByTitle(t('common.edit')))
  const dialog = await screen.findByRole('dialog')
  return { ...rendered, dialog }
}

function previewImage(dialog: HTMLElement) {
  return dialog.querySelector('img')
}

describe('PayeesPage logo', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    workspace.canWrite = true
    api.fiscal.taxIdKinds.mockResolvedValue({ kinds: [], jurisdiction: null })
    api.settings.logos.mockResolvedValue({ logo_size: 128 })
    api.payees.update.mockImplementation(async (id: string, data: object) => ({ ...shop(), id, ...data }))
  })

  it('previews the website\'s logo as the website is typed', async () => {
    const { user, dialog } = await openShopDialog(shop())
    expect(within(dialog).getByText(t('payees.logoNone'))).toBeInTheDocument()

    await user.type(within(dialog).getByLabelText(t('payees.website')), 'www.shop.example')

    expect(previewImage(dialog)).toHaveAttribute('src', FAVICON)
    expect(within(dialog).getByText(t('payees.logoFromWebsite'))).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: t('payees.logoUseWebsite') }))
    expect(within(dialog).getByLabelText(t('payees.logo'))).toHaveValue(FAVICON)
  })

  it('shows a pasted logo link over the website and a picked icon, and saves it', async () => {
    const { user, dialog } = await openShopDialog(shop({ website: 'https://shop.example', icon: 'tv' }))
    expect(previewImage(dialog)).toBeNull()
    expect(dialog.querySelector('.lucide-tv')).not.toBeNull()

    await user.type(within(dialog).getByLabelText(t('payees.logo')), 'https://img.example/logo.png')

    expect(previewImage(dialog)).toHaveAttribute('src', 'https://img.example/logo.png')
    expect(within(dialog).getByText(t('payees.logoShowing'))).toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.payees.update).toHaveBeenCalledWith('shop', expect.objectContaining({
      logo_url: 'https://img.example/logo.png',
      icon: null,
    })))
  })

  it('clears a logo, and leaves an untouched one out of the save', async () => {
    const { user, dialog } = await openShopDialog(shop({ logo_url: 'https://img.example/old.png' }))
    expect(previewImage(dialog)).toHaveAttribute('src', 'https://img.example/old.png')

    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.payees.update).toHaveBeenCalledTimes(1))
    expect(api.payees.update.mock.calls[0][1]).not.toHaveProperty('logo_url')

    await user.click(screen.getByTitle(t('common.edit')))
    const reopened = await screen.findByRole('dialog')
    await user.click(within(reopened).getByRole('button', { name: t('payees.logoClear') }))

    expect(within(reopened).getByLabelText(t('payees.logo'))).toHaveValue('')
    expect(previewImage(reopened)).toBeNull()

    await user.click(within(reopened).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.payees.update).toHaveBeenCalledTimes(2))
    expect(api.payees.update.mock.calls[1][1]).toMatchObject({ logo_url: null })
  })

  it('says so when the logo cannot be loaded', async () => {
    const { user, dialog } = await openShopDialog(shop())
    await user.type(within(dialog).getByLabelText(t('payees.logo')), 'https://img.example/missing.png')

    fireEvent.error(previewImage(dialog)!)

    expect(previewImage(dialog)).toBeNull()
    expect(within(dialog).getByText(t('payees.logoLoadError'))).toBeInTheDocument()
  })

  it('refuses a link that is not http(s)', async () => {
    const { user, dialog } = await openShopDialog(shop())
    await user.type(within(dialog).getByLabelText(t('payees.logo')), 'javascript:alert(1)')

    expect(within(dialog).getByText(t('payees.logoUrlInvalid'))).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: t('common.save') })).toBeDisabled()
    expect(previewImage(dialog)).toBeNull()
  })

  it('opens a payee that needs an icon on its logo field', async () => {
    api.payees.list.mockResolvedValue([shop()])
    const { user } = renderWithProviders(<PayeesPage />, { route: '/payees?needs_icon=true' })
    await screen.findByText('Shop')

    await user.click(screen.getByRole('button', { name: t('payees.addLogo') }))

    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(within(dialog).getByLabelText(t('payees.logo'))).toHaveFocus())
  })
})
