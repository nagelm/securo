import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import ForecastPage from '@/pages/forecast'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, ForecastResponse, ForecastScenario, LoanForecast, RecurringTransaction } from '@/types'

const api = vi.hoisted(() => ({
  forecast: {
    get: vi.fn(),
    items: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
    scenarios: { list: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  },
  recurring: { list: vi.fn() },
  accounts: { list: vi.fn() },
  categories: { list: vi.fn(), listIncludingHidden: vi.fn() },
  categoryGroups: { list: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

// The Recurring tab has its own tests; here it only has to show up.
vi.mock('@/pages/recurring', () => ({ RecurringTab: () => <div data-testid="recurring-tab" /> }))

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

// Made-up figures: one weekly table loan with a "repay more" scenario, and a
// revolving loan with no details.
const scenario: ForecastScenario = { id: 'sc-1', name: 'Repay more', color: '#10B981', is_enabled: true, position: 0 }

const homeLoan: LoanForecast = {
  account_id: 'loan-1',
  name: 'Home loan',
  currency: 'USD',
  balance: 100000,
  rate: 5.15,
  frequency: 'WEEKLY',
  missing_details: false,
  base: {
    points: [
      { date: '2026-10-14', balance: 100000, interest: 0, principal: 0 },
      { date: '2026-10-31', balance: 99500, interest: 290, principal: 500 },
      { date: '2026-11-30', balance: 98600, interest: 480, principal: 900 },
    ],
    payoff_date: '2033-03-30',
    total_interest: 17229.88,
    repayment: 352.40,
  },
  scenarios: [{
    scenario_id: scenario.id,
    name: scenario.name,
    color: scenario.color,
    points: [
      { date: '2026-10-14', balance: 100000, interest: 0, principal: 0 },
      { date: '2026-10-31', balance: 99000, interest: 290, principal: 1000 },
      { date: '2026-11-30', balance: 97000, interest: 470, principal: 2000 },
    ],
    payoff_date: '2030-04-17',
    total_interest: 9217.4,
    repayment: 600,
    interest_saved: 8012.48,
    payoff_change_days: -1078,
  }],
}

const revolving: LoanForecast = {
  account_id: 'loan-2',
  name: 'Revolving credit',
  currency: 'USD',
  balance: 5000,
  rate: null,
  frequency: null,
  missing_details: true,
  base: null,
  scenarios: [],
}

function response(): ForecastResponse {
  return {
    currency: 'USD',
    interval: 'monthly',
    start_date: '2026-10-14',
    end_date: '2026-11-30',
    starting_balance: 1000,
    include_spending: true,
    base: [
      { date: '2026-10-14', value: 1000 },
      { date: '2026-11-30', value: 1200 },
    ],
    summary: { end_balance: 1200, lowest_balance: 1000, lowest_date: '2026-10-14' },
    scenarios: [],
    spending_estimates: [],
    loans: [homeLoan, revolving],
  }
}

const recurring = [
  { id: 'r-1', description: 'Salary', is_active: true, amount: 5000, currency: 'USD', type: 'credit' },
  { id: 'r-2', description: 'Rent', is_active: true, amount: 1500, currency: 'USD', type: 'debit' },
  { id: 'r-3', description: 'Old gym', is_active: false, amount: 50, currency: 'USD', type: 'debit' },
] as unknown as RecurringTransaction[]

const accounts = [
  { id: 'acc-1', name: 'Everyday', display_name: null, type: 'checking', currency: 'USD', position: 0, is_closed: false },
  { id: 'loan-1', name: 'Home loan', display_name: null, type: 'loan', currency: 'USD', position: 1, is_closed: false },
] as unknown as Account[]

beforeEach(() => {
  vi.clearAllMocks()
  api.forecast.get.mockResolvedValue(response())
  api.forecast.items.list.mockResolvedValue([])
  api.forecast.items.create.mockResolvedValue({})
  api.forecast.scenarios.list.mockResolvedValue([scenario])
  api.recurring.list.mockResolvedValue(recurring)
  api.accounts.list.mockResolvedValue(accounts)
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

describe('Forecast page: loans', () => {
  it('shows each loan with its payoff, interest and what each scenario changes', async () => {
    renderWithProviders(<ForecastPage />)

    const rows = await screen.findAllByTestId('loan-row')
    expect(rows).toHaveLength(2)
    const home = rows[0]
    expect(within(home).getByRole('link', { name: 'Home loan' })).toHaveAttribute('href', '/accounts/loan-1')
    expect(within(home).getByText(/\$100,000\.00 owed/)).toBeInTheDocument()
    expect(within(home).getByText(/5\.15% a year/)).toBeInTheDocument()
    expect(within(home).getByText(/\$352\.40 weekly/)).toBeInTheDocument()
    expect(within(home).getByText('Mar 2033')).toBeInTheDocument()
    expect(within(home).getByText('$17,229.88')).toBeInTheDocument()

    const change = within(home).getByTestId('loan-scenario')
    expect(change).toHaveTextContent('Repay more:')
    expect(change).toHaveTextContent('pays off 35 months earlier (Apr 2030)')
    expect(change).toHaveTextContent('saves $8,012.48 interest')
    expect(change).toHaveTextContent('repayment $600.00')

    // Balance lines (base + scenario) and the interest/principal bars.
    await waitFor(() => expect(home.querySelectorAll('.recharts-line')).toHaveLength(2))
    const strokes = [...home.querySelectorAll('.recharts-line-curve')].map((p) => p.getAttribute('stroke'))
    expect(strokes).toEqual(['#6366F1', '#10B981'])
    expect(home.querySelectorAll('.recharts-bar').length).toBe(2)
  })

  it('a loan without enough details links to the loan to fill them in', async () => {
    renderWithProviders(<ForecastPage />)

    const rows = await screen.findAllByTestId('loan-row')
    const note = within(rows[1]).getByTestId('loan-missing-details')
    expect(note).toHaveTextContent(t('forecast.loans.missingDetails'))
    expect(within(note).getByRole('link', { name: t('forecast.loans.addDetails') })).toHaveAttribute('href', '/accounts/loan-2')
    expect(rows[1].querySelector('.recharts-wrapper')).toBeNull()
  })

  it('a loan change picks a projectable loan, a new rate and keeps the repayment', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    await screen.findAllByTestId('loan-row')
    await user.click(await screen.findByRole('button', { name: t('forecast.addChange') }))
    const dialog = await screen.findByRole('dialog')

    await user.selectOptions(within(dialog).getByLabelText(t('forecast.changeKind')), 'loan_change')
    const picker = within(dialog).getByLabelText(t('forecast.loans.loan'))
    expect(picker).toHaveValue('loan-1')
    expect(within(picker).getByRole('option', { name: 'Revolving credit (needs loan details)' })).toBeDisabled()
    expect(within(dialog).getByTestId('loan-current')).toHaveTextContent('Now 5.15% a year, $352.40 weekly')
    expect(within(dialog).getByLabelText(t('forecast.changeMode'))).toHaveValue('rate')

    await user.type(within(dialog).getByLabelText(t('forecast.loans.newRate')), '6.5')
    expect(within(dialog).getByRole('button', { name: t('forecast.loans.recalculate') })).toHaveAttribute('aria-pressed', 'true')
    await user.click(within(dialog).getByRole('button', { name: t('forecast.loans.keep') }))
    expect(within(dialog).getByText(t('forecast.loans.keepHint'))).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.forecast.items.create).toHaveBeenCalledWith(expect.objectContaining({
      scenario_id: 'sc-1', kind: 'loan_change', account_id: 'loan-1', change_mode: 'rate', percent: '6.5',
      amount: null, recalculate_repayment: false, end_date: null,
    })))
  })

  it('a rate change can set the repayment by hand (fork)', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    await screen.findAllByTestId('loan-row')
    await user.click(await screen.findByRole('button', { name: t('forecast.addChange') }))
    const dialog = await screen.findByRole('dialog')

    await user.selectOptions(within(dialog).getByLabelText(t('forecast.changeKind')), 'loan_change')
    await user.type(within(dialog).getByLabelText(t('forecast.loans.newRate')), '4.9')
    expect(within(dialog).queryByLabelText(t('forecast.loans.newRepaymentAmount'))).not.toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: t('forecast.loans.set') }))
    expect(within(dialog).getByText(t('forecast.loans.setHint'))).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText(t('forecast.loans.newRepaymentAmount')), '600')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.forecast.items.create).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'loan_change', account_id: 'loan-1', change_mode: 'rate', percent: '4.9',
      amount: '600', recalculate_repayment: false,
    })))
  })

  it('a rate change with a set repayment reads as one line', async () => {
    api.forecast.items.list.mockResolvedValue([{
      id: 'it-2', scenario_id: 'sc-1', kind: 'loan_change', description: '', amount: '600', currency: 'USD',
      type: null, date: null, start_date: '2027-02-01', end_date: null, frequency: null, account_id: 'loan-1',
      category_id: null, recurring_transaction_id: null, change_mode: 'rate', percent: '4.9',
      recalculate_repayment: false, matched_transaction_id: null,
    }])
    renderWithProviders(<ForecastPage />)

    expect(await screen.findByText(/Home loan rate 4\.9% and repayment \$600\.00 from 2\/1\/2027/)).toBeInTheDocument()
  })

  it('a lump sum sends the amount and its date', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    await screen.findAllByTestId('loan-row')
    await user.click(await screen.findByRole('button', { name: t('forecast.addChange') }))
    const dialog = await screen.findByRole('dialog')

    await user.selectOptions(within(dialog).getByLabelText(t('forecast.changeKind')), 'loan_change')
    await user.selectOptions(within(dialog).getByLabelText(t('forecast.changeMode')), 'lump_sum')
    expect(within(dialog).queryByRole('button', { name: t('forecast.loans.keep') })).not.toBeInTheDocument()
    await user.type(within(dialog).getByLabelText(t('forecast.loans.extraAmount')), '10000')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.forecast.items.create).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'loan_change', account_id: 'loan-1', change_mode: 'lump_sum', amount: '10000', percent: null,
      recalculate_repayment: null,
    })))
  })

  it('a loan change in a scenario reads as one line', async () => {
    api.forecast.items.list.mockResolvedValue([{
      id: 'it-1', scenario_id: 'sc-1', kind: 'loan_change', description: '', amount: null, currency: 'USD',
      type: null, date: null, start_date: '2027-02-01', end_date: null, frequency: null, account_id: 'loan-1',
      category_id: null, recurring_transaction_id: null, change_mode: 'rate', percent: '6.25',
      recalculate_repayment: true, matched_transaction_id: null,
    }])
    renderWithProviders(<ForecastPage />)

    expect(await screen.findByText(/Home loan rate 6\.25% from 2\/1\/2027, repayment recalculated/)).toBeInTheDocument()
  })
})

describe('Forecast page: recurring', () => {
  it('shows one line counting the active recurring payments, and Manage links to the Recurring page', async () => {
    renderWithProviders(<ForecastPage />)

    const line = await screen.findByTestId('recurring-line')
    await waitFor(() => expect(line).toHaveTextContent('2 recurring payments feed this forecast'))
    expect(screen.queryByText('Salary')).not.toBeInTheDocument()

    expect(within(line).getByRole('link', { name: t('forecast.manageRecurring') })).toHaveAttribute('href', '/recurring')
  })
})

describe('Forecast page: history', () => {
  it('draws the real balance up to today, then the dashed projection', async () => {
    api.forecast.get.mockResolvedValue({
      ...response(),
      loans: [{
        ...homeLoan,
        scenarios: [],
        actuals: [
          { date: '2026-08-14', value: 101000 },
          { date: '2026-09-30', value: 100500 },
          { date: '2026-10-14', value: 100000 },
        ],
      }],
      actuals: [
        { date: '2026-08-14', value: 700 },
        { date: '2026-08-31', value: 760 },
        { date: '2026-09-30', value: 880 },
        { date: '2026-10-14', value: 1000 },
      ],
    })
    const { container } = renderWithProviders(<ForecastPage />)

    const loanRow = await screen.findByTestId('loan-row')
    const chart = container.querySelector('.recharts-wrapper') as HTMLElement
    await waitFor(() => expect(chart.querySelectorAll('.recharts-line')).toHaveLength(2))
    const curves = [...chart.querySelectorAll('.recharts-line-curve')]
    expect(curves.map((c) => c.getAttribute('stroke'))).toEqual(['#64748B', '#6366F1'])
    expect(curves[0].getAttribute('stroke-dasharray')).toBeNull()
    expect(curves[1].getAttribute('stroke-dasharray')).toBe('6 3')
    expect(within(chart).getByText(t('reports.today'))).toBeInTheDocument()
    expect(screen.getAllByText(t('forecast.actual')).length).toBeGreaterThan(0)
    // Summary cards stay future-only.
    expect(screen.getByText('$1,200.00')).toBeInTheDocument()

    // The loan's own history, then its projection.
    await waitFor(() => expect(loanRow.querySelectorAll('.recharts-line')).toHaveLength(2))
    const loanCurves = [...loanRow.querySelectorAll('.recharts-line-curve')]
    expect(loanCurves.map((c) => c.getAttribute('stroke'))).toEqual(['#64748B', '#6366F1'])
  })
})
