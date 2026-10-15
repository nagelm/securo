import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import ForecastPage from '@/pages/forecast'
import { renderWithProviders, t } from '@/test/utils'
import type { Account, ForecastResponse, ForecastScenario } from '@/types'

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

// Made-up figures: 1,000 today, a holiday scenario costing 400 by the end.
const scenario: ForecastScenario = { id: 'sc-1', name: 'Holiday', color: '#F59E0B', is_enabled: true, position: 0 }

function response(withScenario = true): ForecastResponse {
  return {
    currency: 'USD',
    interval: 'monthly',
    start_date: '2026-10-14',
    end_date: '2027-01-14',
    starting_balance: 1000,
    include_spending: true,
    base: [
      { date: '2026-10-14', value: 1000 },
      { date: '2026-10-31', value: 900 },
      { date: '2026-11-30', value: 1100 },
      { date: '2027-01-14', value: 1200 },
    ],
    summary: { end_balance: 1200, lowest_balance: 900, lowest_date: '2026-10-31' },
    scenarios: withScenario
      ? [{
          id: scenario.id,
          name: scenario.name,
          color: scenario.color,
          points: [
            { date: '2026-10-14', value: 1000 },
            { date: '2026-10-31', value: 500 },
            { date: '2026-11-30', value: 700 },
            { date: '2027-01-14', value: 800 },
          ],
          summary: { end_balance: 800, lowest_balance: 500, lowest_date: '2026-10-31' },
          difference: -400,
        }]
      : [],
    spending_estimates: [{ category_id: 'cat-eat', name: 'Eating out', color: '#123456', monthly_amount: 280 }],
  }
}

const checking = {
  id: 'acc-1', name: 'Everyday', display_name: null, type: 'checking', currency: 'USD', position: 0, is_closed: false,
} as unknown as Account

beforeEach(() => {
  vi.clearAllMocks()
  api.forecast.get.mockResolvedValue(response())
  api.forecast.items.list.mockResolvedValue([])
  api.forecast.items.create.mockResolvedValue({})
  api.forecast.scenarios.list.mockResolvedValue([scenario])
  api.forecast.scenarios.update.mockResolvedValue({ ...scenario, is_enabled: false })
  api.recurring.list.mockResolvedValue([])
  api.accounts.list.mockResolvedValue([checking])
  api.categories.list.mockResolvedValue([])
  api.categories.listIncludingHidden.mockResolvedValue([])
  api.categoryGroups.list.mockResolvedValue([])
})

// Fork (owner 2026-10-15): the cash chart and each loan's chart share one
// carousel; expected payments and scenarios sit on their own tabs.
async function openScenarios(user: ReturnType<typeof renderWithProviders>['user']) {
  await user.click(await screen.findByRole('tab', { name: t('forecast.tabs.scenarios') }))
}

describe('Forecast page', () => {
  it('draws the base line and one line per scenario, with their summaries', async () => {
    const { container } = renderWithProviders(<ForecastPage />)

    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(2))
    const strokes = [...container.querySelectorAll('.recharts-line-curve')].map((p) => p.getAttribute('stroke'))
    expect(strokes).toEqual(['#6366F1', '#F59E0B'])
    expect(api.forecast.get).toHaveBeenCalledWith(12, 'monthly', true)

    expect(screen.getByText('$1,200.00')).toBeInTheDocument()
    const card = screen.getByTestId('scenario-summary')
    expect(within(card).getByText('Holiday')).toBeInTheDocument()
    expect(within(card).getByText('$800.00')).toBeInTheDocument()
    expect(within(card).getByText(/−\$400\.00/)).toBeInTheDocument()
  })

  it('switching everyday spending off asks for a forecast without it', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    await waitFor(() => expect(api.forecast.get).toHaveBeenCalledTimes(1))

    await user.click(screen.getByRole('button', { name: t('forecast.includeSpending') }))

    await waitFor(() => expect(api.forecast.get).toHaveBeenLastCalledWith(12, 'monthly', false))
  })

  it('adding an expected payment sends it to the API', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    await waitFor(() => expect(api.accounts.list).toHaveBeenCalled())

    await user.click(await screen.findByRole('button', { name: t('forecast.addExpected') }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText(t('recurring.description')), 'Tax bill')
    await user.type(within(dialog).getByLabelText(t('recurring.amount')), '250')
    await waitFor(() =>
      expect(within(dialog).getByLabelText(t('recurring.account'))).toHaveValue('acc-1'),
    )
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.forecast.items.create).toHaveBeenCalledTimes(1))
    expect(api.forecast.items.create).toHaveBeenCalledWith(expect.objectContaining({
      kind: 'one_off', description: 'Tax bill', amount: '250', type: 'debit', account_id: 'acc-1',
    }))
    await waitFor(() => expect(api.forecast.get).toHaveBeenCalledTimes(2))
  })

  it('lists recurring payments with the expected payments, by date', async () => {
    api.forecast.get.mockResolvedValue({
      ...response(),
      recurring_payments: [
        { recurring_transaction_id: 'rec-1', date: '2026-10-20', description: 'Rates', amount: 70.00, type: 'debit', currency: 'USD', account_id: 'acc-1' },
        { recurring_transaction_id: 'rec-1', date: '2026-10-27', description: 'Rates', amount: 70.00, type: 'debit', currency: 'USD', account_id: 'acc-1' },
      ],
    })
    api.forecast.items.list.mockResolvedValue([{
      id: 'it-1', scenario_id: null, kind: 'one_off', description: 'Car service', amount: '400', currency: 'USD',
      type: 'debit', date: '2026-10-25', start_date: null, end_date: null, frequency: null, account_id: 'acc-1',
      category_id: null, recurring_transaction_id: null, change_mode: null, percent: null,
      recalculate_repayment: null, matched_transaction_id: null,
    }])
    renderWithProviders(<ForecastPage />)

    const recurringRows = await screen.findAllByTestId('expected-recurring')
    expect(recurringRows).toHaveLength(2)
    expect(within(recurringRows[0]).getByText(t('forecast.recurringBadge'))).toBeInTheDocument()
    // One list by date: Rates (20 Oct), Car service (25 Oct), Rates (27 Oct).
    const rows = screen.getAllByRole('row').slice(1).map((r) => r.textContent ?? '')
    expect(rows.map((r) => (r.includes('Car service') ? 'car' : r.includes('Rates') ? 'rates' : '?')).slice(0, 3))
      .toEqual(['rates', 'car', 'rates'])
  })

  it('turning a scenario off saves it and fetches the forecast again', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    await openScenarios(user)
    const toggle = await screen.findByRole('switch', { name: 'Holiday' })
    await waitFor(() => expect(api.forecast.get).toHaveBeenCalledTimes(1))
    api.forecast.get.mockResolvedValue(response(false))

    await user.click(toggle)

    await waitFor(() => expect(api.forecast.scenarios.update).toHaveBeenCalledWith('sc-1', { is_enabled: false }))
    await waitFor(() => expect(api.forecast.get).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(screen.queryByTestId('scenario-summary')).not.toBeInTheDocument())
  })

  it('a spending change shows each category with its monthly average and what a cut is worth', async () => {
    const { user } = renderWithProviders(<ForecastPage />)
    await openScenarios(user)
    await user.click(await screen.findByRole('button', { name: t('forecast.addChange') }))
    const dialog = await screen.findByRole('dialog')

    await user.selectOptions(within(dialog).getByLabelText(t('forecast.changeKind')), 'spending_change')
    const category = within(dialog).getByLabelText(t('recurring.category'))
    expect(within(category).getByRole('option', { name: 'Eating out — $280.00 a month' })).toBeInTheDocument()
    await user.type(within(dialog).getByLabelText(t('forecast.percent')), '50')
    expect(within(dialog).getByTestId('spending-effect')).toHaveTextContent('$280.00 a month becomes $140.00 a month')

    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))
    await waitFor(() => expect(api.forecast.items.create).toHaveBeenCalledWith(expect.objectContaining({
      scenario_id: 'sc-1', kind: 'spending_change', category_id: 'cat-eat', change_mode: 'percent', percent: '-50',
    })))
  })
})

describe('Forecast page: chart legend (fork)', () => {
  const yTicks = (chart: Element) =>
    [...chart.querySelectorAll('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')].map((n) => n.textContent)

  it('a legend click hides that line and its markers, the axis rescales, and a second click brings it back', async () => {
    const { container, user } = renderWithProviders(<ForecastPage />)
    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(2))
    // Each line carries a lowest-point and an end-balance marker.
    expect(container.querySelectorAll('.recharts-reference-dot')).toHaveLength(4)
    const chart = container.querySelector('.recharts-wrapper')!
    const before = yTicks(chart)
    expect(before.length).toBeGreaterThan(0)

    const base = screen.getByRole('button', { name: t('forecast.base') })
    expect(base).toHaveAttribute('aria-pressed', 'true')
    await user.click(base)

    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(1))
    expect(container.querySelector('.recharts-line-curve')).toHaveAttribute('stroke', '#F59E0B')
    expect(container.querySelectorAll('.recharts-reference-dot')).toHaveLength(2)
    expect(base).toHaveAttribute('aria-pressed', 'false')
    // Without the base line (up to 1,200) the scenario alone tops out at 1,000.
    await waitFor(() => expect(yTicks(chart)).not.toEqual(before))

    await user.click(base)

    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(2))
    expect(container.querySelectorAll('.recharts-reference-dot')).toHaveLength(4)
    expect(base).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(yTicks(chart)).toEqual(before))
  })

  it('works from the keyboard and keeps the last line on', async () => {
    const { container, user } = renderWithProviders(<ForecastPage />)
    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(2))

    const holiday = screen.getByRole('button', { name: 'Holiday' })
    holiday.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(1))
    expect(holiday).toHaveAttribute('aria-pressed', 'false')

    const base = screen.getByRole('button', { name: t('forecast.base') })
    expect(base).toHaveAttribute('aria-disabled', 'true')
    await user.click(base)
    expect(container.querySelectorAll('.recharts-line')).toHaveLength(1)
    expect(base).toHaveAttribute('aria-pressed', 'true')

    holiday.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(2))
  })
})
