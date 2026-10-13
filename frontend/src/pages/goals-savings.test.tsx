import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

const privacy = vi.hoisted(() => ({ on: false }))
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({
    mask: (value: string) => (privacy.on ? '••••' : value),
    privacyMode: privacy.on,
    MASK: '••••',
  }),
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
    position: 0,
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

// Ten finished Monday-to-Sunday weeks and the current one, 16-22 March 2026.
const CONTRIBUTED = [20, 50, 50, 80, 50, 50, 0, 50, 50, 50, 30]
const STATUSES = ['short', 'met', 'met', 'over', 'met', 'met', 'short', 'met', 'met', 'met', 'pending'] as const

function isoDate(day: Date): string {
  return day.toISOString().slice(0, 10)
}

function progress(overrides: Partial<ContributionProgress> = {}): ContributionProgress {
  const firstMonday = Date.UTC(2026, 0, 5)
  const history = CONTRIBUTED.map((contributed, i) => {
    const start = new Date(firstMonday + i * 7 * 86_400_000)
    const end = new Date(start.getTime() + 6 * 86_400_000)
    return {
      start: isoDate(start),
      end: isoDate(end),
      contributed,
      target: 50,
      status: STATUSES[i],
      current: i === 10,
    }
  })
  return {
    period: 'weekly',
    target: 50,
    current: history[10],
    history,
    streak: 3,
    since: '2026-03-09',
    saved_since: 140,
    planned_since: 100,
    ahead_by: 40,
    ...overrides,
  }
}

function holidayGoal(overrides: Partial<ContributionProgress> = {}): Goal {
  return goal({
    name: 'Holiday',
    target_amount: null,
    percentage: 0,
    tracking_type: 'account',
    account_id: holidayFund.id,
    account_name: 'Holiday fund',
    contribution_amount: 50,
    contribution_period: 'weekly',
    contribution_progress: progress(overrides),
  })
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
  beforeEach(() => {
    // Thursday evening of the current week: four days left, 4/7 of it gone.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 2, 19, 21, 30))
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  afterEach(() => {
    privacy.on = false
  })

  it('shows this period as a ring, the record as dots, and how far ahead of the plan', async () => {
    api.goals.list.mockResolvedValue([holidayGoal()])
    renderWithProviders(<GoalsPage />)

    expect(await screen.findByText('$50.00 / week')).toBeInTheDocument()
    // This week: 30 of 50 is 60%, and four days are left.
    expect(within(screen.getByTestId('period-ring')).getByText('60%')).toBeInTheDocument()
    expect(screen.getByText('This week $30.00 of $50.00')).toBeInTheDocument()
    expect(screen.getByText('4 days left')).toBeInTheDocument()
    // Finished weeks only: the current one has not reached its target yet.
    expect(screen.getByText('8 of last 10 · Streak 3')).toBeInTheDocument()
    // An ongoing goal shows its balance, not a total, and has no total bar.
    expect(screen.getByText(/Balance \$250\.00/)).toBeInTheDocument()
    expect(screen.getByText(/Ongoing/)).toBeInTheDocument()
    expect(screen.queryByText(/\/ \$1,000\.00/)).not.toBeInTheDocument()
    expect(screen.queryByText('0%')).not.toBeInTheDocument()

    const dots = within(screen.getByRole('list', { name: 'Contributions in recent periods' })).getAllByRole('listitem')
    expect(dots).toHaveLength(10)
    expect(dots[0]).toHaveAccessibleName('Jan 5 to Jan 11: $20.00 of $50.00 (Short)')
    expect(dots[3]).toHaveAccessibleName(/\$80\.00 of \$50\.00 \(Over\)/)
    expect(dots[9]).toHaveAccessibleName('Mar 9 to Mar 15: $50.00 of $50.00 (Met)')
    expect(dots.map((d) => d.dataset.status)).toEqual([...STATUSES.slice(0, 10)])

    // $40 ahead of a $50-a-week plan: 40 of the 200 that fill the right half.
    const scale = screen.getByRole('img', { name: 'Ahead by $40 since Mar 9' })
    expect(scale.dataset.state).toBe('ahead')
    expect(within(scale).getByText('$40 ahead')).toBeInTheDocument()
    const bar = within(scale).getByTestId('plan-bar')
    expect(parseFloat(bar.style.left)).toBe(50)
    expect(parseFloat(bar.style.width)).toBe(10)
    expect(within(scale).getByText('behind')).toBeInTheDocument()
    expect(within(scale).getByText('plan')).toBeInTheDocument()
    expect(within(scale).getByText('ahead')).toBeInTheDocument()
  })

  it('reads amounts the API sends as strings, and shows a deficit growing left', async () => {
    api.goals.list.mockResolvedValue([
      holidayGoal({ ahead_by: '-950.00' as unknown as number, target: '50.00' as unknown as number }),
    ])
    renderWithProviders(<GoalsPage />)
    const scale = await screen.findByRole('img', { name: 'Behind by $950 since Mar 9' })
    expect(within(scale).getByText('$950 behind')).toBeInTheDocument()
    const bar = within(scale).getByTestId('plan-bar')
    // Far more than four weeks behind: the left half is full.
    expect([parseFloat(bar.style.left), parseFloat(bar.style.width)]).toEqual([0, 50])
  })

  it('says on plan, and shows cents below ten', async () => {
    api.goals.list.mockResolvedValue([holidayGoal({ ahead_by: 0 })])
    const { unmount } = renderWithProviders(<GoalsPage />)
    const scale = await screen.findByRole('img', { name: 'On plan since Mar 9' })
    expect(within(scale).getByText('On plan')).toBeInTheDocument()
    unmount()

    api.goals.list.mockResolvedValue([holidayGoal({ ahead_by: 2.5 })])
    renderWithProviders(<GoalsPage />)
    expect(await screen.findByRole('img', { name: 'Ahead by $2.50 since Mar 9' })).toBeInTheDocument()
  })

  it('shows a check mark once this period is met', async () => {
    const met = progress()
    met.current = { ...met.current, contributed: 50, status: 'met' }
    met.history = [...met.history.slice(0, 10), met.current]
    api.goals.list.mockResolvedValue([goal({ ...holidayGoal(), contribution_progress: met })])
    renderWithProviders(<GoalsPage />)
    const ring = await screen.findByTestId('period-ring')
    expect(within(ring).getByText('Met')).toHaveClass('sr-only')
    expect(within(ring).queryByText(/%$/)).not.toBeInTheDocument()
  })

  it('leaves the scale out while nothing has gone in or out', async () => {
    api.goals.list.mockResolvedValue([
      holidayGoal({ since: null, saved_since: null, planned_since: null, ahead_by: null }),
    ])
    renderWithProviders(<GoalsPage />)
    expect(await screen.findByTestId('period-ring')).toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('masks every amount in privacy mode', async () => {
    privacy.on = true
    api.goals.list.mockResolvedValue([holidayGoal()])
    renderWithProviders(<GoalsPage />)
    const scale = await screen.findByRole('img', { name: 'Ahead by •••• since Mar 9' })
    expect(within(scale).getByText('•••• ahead')).toBeInTheDocument()
    expect(screen.queryByText(/\$/)).not.toBeInTheDocument()
    const dots = within(screen.getByRole('list', { name: 'Contributions in recent periods' })).getAllByRole('listitem')
    expect(dots[0]).toHaveAccessibleName('Jan 5 to Jan 11: •••• of •••• (Short)')
  })

  it('keeps the total bar above the periodic row for a goal with both targets', async () => {
    api.goals.list.mockResolvedValue([
      goal({
        name: 'Car',
        tracking_type: 'account',
        account_id: carFund.id,
        contribution_amount: 50,
        contribution_period: 'weekly',
        contribution_progress: progress(),
      }),
    ])
    renderWithProviders(<GoalsPage />)
    expect(await screen.findByText('$250.00 / $1,000.00')).toBeInTheDocument()
    const total = screen.getByText('25%')
    const ring = screen.getByTestId('period-ring')
    expect(total.compareDocumentPosition(ring) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(
      within(screen.getByRole('list', { name: 'Contributions in recent periods' })).getAllByRole('listitem'),
    ).toHaveLength(10)
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
