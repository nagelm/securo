import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import GoalsPage from '@/pages/goals'
import { renderWithProviders } from '@/test/utils'
import type { Account, ContributionProgress, Goal } from '@/types'

const api = vi.hoisted(() => ({
  goals: {
    list: vi.fn(),
    create: vi.fn(),
    createMany: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    contributionSuggestion: vi.fn(),
  },
  accounts: { list: vi.fn() },
  assets: { list: vi.fn() },
  assetGroups: { list: vi.fn() },
  currencies: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
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

vi.mock('@/contexts/workspace-context', async () => {
  const { createContext } = await import('react')
  return {
    useWorkspace: () => ({ canWrite: true }),
    WorkspaceContext: createContext(null),
  }
})

// Made-up names and figures throughout.
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
    type: 'savings',
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

function progress(): ContributionProgress {
  const history = Array.from({ length: 12 }, (_, i) => {
    const day = String(i + 1).padStart(2, '0')
    const current = i === 11
    return {
      start: `2026-01-${day}`,
      end: `2026-01-${day}`,
      contributed: current ? 30 : i >= 9 ? 50 : 0,
      target: 50,
      status: (current ? 'pending' : i >= 9 ? 'met' : 'short') as 'pending' | 'met' | 'short',
      current,
    }
  })
  return { period: 'weekly', target: 50, current: history[11], history, streak: 2 }
}

function goal(overrides: Partial<Goal>): Goal {
  return {
    id: 'g1',
    user_id: 'user-1',
    name: 'Goal',
    target_amount: 1000,
    contribution_amount: null,
    contribution_period: null,
    current_amount: 250,
    currency: 'USD',
    target_amount_primary: null,
    current_amount_primary: null,
    target_date: null,
    tracking_type: 'manual',
    account_id: null,
    asset_id: null,
    asset_group_id: null,
    status: 'active',
    icon: null,
    color: null,
    position: 0,
    metadata_json: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    percentage: 25,
    monthly_contribution: null,
    on_track: null,
    contribution_progress: null,
    account_name: null,
    asset_name: null,
    asset_group_name: null,
    ...overrides,
  }
}

const holidayFund = account({ id: 'acc-holiday', name: 'Holiday fund' })
const carFund = account({ id: 'acc-car', name: 'Car fund' })
const everyday = account({ id: 'acc-everyday', name: 'Everyday', type: 'checking' })
const card = account({ id: 'acc-card', name: 'Card', type: 'credit_card' })

beforeEach(() => {
  vi.clearAllMocks()
  api.goals.list.mockResolvedValue([])
  api.goals.create.mockResolvedValue(goal({}))
  api.goals.createMany.mockResolvedValue([])
  api.goals.contributionSuggestion.mockResolvedValue(null)
  api.accounts.list.mockResolvedValue([holidayFund, carFund, everyday, card])
  api.assets.list.mockResolvedValue([])
  api.assetGroups.list.mockResolvedValue([])
  api.currencies.list.mockResolvedValue([{ code: 'USD', name: 'US Dollar', flag: '' }])
})

describe('goal card with a periodic target', () => {
  it('shows the target, this period, the streak and one dot per period', async () => {
    api.goals.list.mockResolvedValue([
      goal({
        name: 'Holiday',
        target_amount: null,
        percentage: 0,
        tracking_type: 'account',
        account_id: holidayFund.id,
        account_name: 'Holiday fund',
        contribution_amount: 50,
        contribution_period: 'weekly',
        contribution_progress: progress(),
      }),
    ])
    renderWithProviders(<GoalsPage />)

    expect(await screen.findByText('$50.00 / week')).toBeInTheDocument()
    expect(screen.getByText('This week $30.00 of $50.00')).toBeInTheDocument()
    expect(screen.getByText('Streak 2')).toBeInTheDocument()
    // An ongoing goal shows its balance, not a total.
    expect(screen.getByText(/Balance \$250\.00/)).toBeInTheDocument()
    expect(screen.getByText(/Ongoing/)).toBeInTheDocument()
    expect(screen.queryByText(/\/ \$1,000\.00/)).not.toBeInTheDocument()
    // The bar follows this period: 30 of 50.
    expect(screen.getByText('60%')).toBeInTheDocument()

    const dots = within(screen.getByRole('list', { name: 'Contributions in recent periods' })).getAllByRole('listitem')
    expect(dots).toHaveLength(12)
    expect(dots[0]).toHaveAccessibleName(/\$0\.00 of \$50\.00 \(Short\)/)
    expect(dots[11]).toHaveAccessibleName(/\(In progress\)/)
  })

  it('keeps the total for a goal that has one', async () => {
    api.goals.list.mockResolvedValue([
      goal({ name: 'Car', contribution_amount: 100, contribution_period: 'monthly' }),
    ])
    renderWithProviders(<GoalsPage />)
    expect(await screen.findByText('$250.00 / $1,000.00')).toBeInTheDocument()
    expect(screen.getByText('$100.00 / month')).toBeInTheDocument()
    expect(screen.getByText('Link an account to see what you contribute each period.')).toBeInTheDocument()
  })
})

describe('goal form', () => {
  it('creates an ongoing goal with only a periodic target', async () => {
    const { user } = renderWithProviders(<GoalsPage />)
    await user.click(await screen.findByRole('button', { name: /Add Goal/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getAllByRole('textbox')[0], 'Pet fund')
    await user.selectOptions(within(dialog).getByLabelText('Every'), 'biweekly')
    await user.type(within(dialog).getByLabelText('Amount'), '25')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(api.goals.create).toHaveBeenCalled())
    expect(api.goals.create.mock.calls[0][0]).toMatchObject({
      name: 'Pet fund',
      target_amount: null,
      contribution_amount: 25,
      contribution_period: 'biweekly',
    })
  })

  it('refuses a goal with neither a total nor a periodic target', async () => {
    const { user } = renderWithProviders(<GoalsPage />)
    await user.click(await screen.findByRole('button', { name: /Add Goal/ }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getAllByRole('textbox')[0], 'Nothing')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith('Enter a total target, a regular contribution, or both.'),
    )
    expect(api.goals.create).not.toHaveBeenCalled()
  })

  it('opens linked to an account from ?new=1&account_id= and offers its regular payment', async () => {
    api.goals.contributionSuggestion.mockResolvedValue({
      amount: 40,
      period: 'weekly',
      currency: 'USD',
      source: 'recurring',
      recurring_transaction_id: 'rt-1',
      description: 'To holiday',
    })
    const { user } = renderWithProviders(<GoalsPage />, { route: `/goals?new=1&account_id=${holidayFund.id}` })

    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByDisplayValue('Holiday fund')).toBeInTheDocument()
    expect(
      await within(dialog).findByText('Matches the recurring transaction "To holiday": $40.00 / week'),
    ).toBeInTheDocument()
    expect(api.goals.contributionSuggestion).toHaveBeenCalledWith(holidayFund.id)

    await user.click(within(dialog).getByRole('button', { name: 'Use' }))
    expect(within(dialog).getByLabelText('Amount')).toHaveValue(40)
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(api.goals.create).toHaveBeenCalled())
    expect(api.goals.create.mock.calls[0][0]).toMatchObject({
      name: 'Holiday fund',
      tracking_type: 'account',
      account_id: holidayFund.id,
      target_amount: null,
      contribution_amount: 40,
      contribution_period: 'weekly',
    })
  })
})

describe('quick setup', () => {
  it('creates one goal per picked account in one request', async () => {
    api.goals.list.mockResolvedValue([
      goal({ id: 'g-car', name: 'Car', tracking_type: 'account', account_id: carFund.id }),
    ])
    api.goals.contributionSuggestion.mockImplementation(async (id: string) =>
      id === holidayFund.id
        ? { amount: 20, period: 'weekly', currency: 'USD', source: 'history', recurring_transaction_id: null, description: null }
        : null,
    )
    const { user } = renderWithProviders(<GoalsPage />)
    await user.click(await screen.findByRole('button', { name: /Set up savings goals/ }))
    const dialog = await screen.findByRole('dialog')

    // Already-linked accounts, cards and loans are not offered.
    const holiday = await within(dialog).findByRole('checkbox', { name: 'Holiday fund' })
    expect(within(dialog).queryByRole('checkbox', { name: 'Car fund' })).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('checkbox', { name: 'Card' })).not.toBeInTheDocument()
    const everydayBox = within(dialog).getByRole('checkbox', { name: 'Everyday' })

    // The savings account with a regular payment is picked and prefilled.
    await waitFor(() => expect(holiday).toBeChecked())
    expect(everydayBox).not.toBeChecked()

    await user.click(within(dialog).getByRole('button', { name: 'Create goals' }))
    await waitFor(() => expect(api.goals.createMany).toHaveBeenCalled())
    expect(api.goals.createMany.mock.calls[0][0]).toEqual([
      expect.objectContaining({
        name: 'Holiday fund',
        tracking_type: 'account',
        account_id: holidayFund.id,
        contribution_amount: 20,
        contribution_period: 'weekly',
        currency: 'USD',
      }),
    ])
  })
})
