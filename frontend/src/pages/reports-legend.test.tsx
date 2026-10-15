import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import { createElement, type ComponentProps } from 'react'

import ReportsPage from '@/pages/reports'
import { renderWithProviders, t } from '@/test/utils'
import type { ReportResponse } from '@/types'

const api = vi.hoisted(() => ({
  reports: {
    netWorth: vi.fn(),
    incomeExpenses: vi.fn(),
    cashFlow: vi.fn(),
  },
}))

vi.mock('@/lib/api', () => ({ reports: api.reports }))

// The donut's slices animate in from zero, which never finishes under a
// frozen clock; draw them at once. The responsive container keeps the
// global setup's test viewport.
vi.mock('recharts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('recharts')>()
  return {
    ...actual,
    ResponsiveContainer(props: ComponentProps<typeof actual.ResponsiveContainer>) {
      return createElement(actual.ResponsiveContainer, { initialDimension: { width: 800, height: 300 }, ...props })
    },
    Pie(props: ComponentProps<typeof actual.Pie>) {
      return createElement(actual.Pie, { isAnimationActive: false, ...props })
    },
  }
})

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/contexts/collection-filter-context', () => ({
  useCollectionFilter: () => ({ activeAccountIds: null, activeWalletIds: null }),
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

// Made-up figures: accounts 6,000, assets 4,000, liabilities 2,000.
const breakdowns = { accounts: 6000, assets: 4000, liabilities: 2000 }

function netWorthReport(): ReportResponse {
  return {
    summary: {
      primary_value: 8000,
      change_amount: 0,
      change_percent: 0,
      breakdowns: [
        { key: 'accounts', label: 'Accounts', value: 6000, color: '#6366F1' },
        { key: 'assets', label: 'Assets', value: 4000, color: '#10B981' },
        { key: 'liabilities', label: 'Liabilities', value: 2000, color: '#F43F5E' },
      ],
    },
    trend: ['2026-07-01', '2026-08-01', '2026-09-01'].map((date) => ({
      date,
      value: 8000,
      breakdowns,
      change: null,
    })),
    meta: { type: 'net_worth', series_keys: ['accounts', 'assets', 'liabilities'], currency: 'USD', interval: 'monthly' },
    composition: [
      { key: 'acc-a', label: 'Account A', value: 6000, color: '#6366F1', group: 'accounts' },
      { key: 'asset-b', label: 'Asset B', value: 4000, color: '#10B981', group: 'assets' },
      { key: 'loan-c', label: 'Loan C', value: 2000, color: '#F43F5E', group: 'liabilities' },
    ],
    category_trend: [],
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 12, 12))
  vi.clearAllMocks()
  api.reports.netWorth.mockResolvedValue(netWorthReport())
  api.reports.incomeExpenses.mockResolvedValue(netWorthReport())
  api.reports.cashFlow.mockResolvedValue(netWorthReport())
})

afterEach(() => vi.useRealTimers())

/** The legend buttons drawn by Recharts' <Legend> (the evolution chart). */
function evolutionLegend(container: HTMLElement) {
  const wrapper = container.querySelector('.recharts-legend-wrapper') as HTMLElement
  expect(wrapper).not.toBeNull()
  return within(wrapper)
}

function evolutionChart(container: HTMLElement) {
  return container.querySelector('.recharts-legend-wrapper')!.closest('.recharts-wrapper') as HTMLElement
}

function yTicks(chart: HTMLElement) {
  return [...chart.querySelectorAll('.recharts-yAxis-tick-labels .recharts-cartesian-axis-tick-value')].map((n) => n.textContent)
}

describe('Reports legends switch their series on and off (fork)', () => {
  it('the composition donut: a legend click hides that slice, a second click brings it back', async () => {
    const { container, user } = renderWithProviders(<ReportsPage />)

    await waitFor(() => expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(6))
    // The donut's own legend sits outside any Recharts legend wrapper.
    const donutAssets = screen
      .getAllByRole('button', { name: t('reports.assets') })
      .find((b) => !b.closest('.recharts-legend-wrapper'))!
    expect(donutAssets).toHaveAttribute('aria-pressed', 'true')

    await user.click(donutAssets)

    // The assets group leaves both rings: its inner slice and its holding.
    await waitFor(() => expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(4))
    expect(donutAssets).toHaveAttribute('aria-pressed', 'false')
    expect(donutAssets).toHaveClass('line-through')

    await user.click(donutAssets)

    await waitFor(() => expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(6))
    expect(donutAssets).toHaveAttribute('aria-pressed', 'true')
  })

  it('the stacked evolution bars: a hidden series leaves the stack and the axis rescales', async () => {
    const { container, user } = renderWithProviders(<ReportsPage />)

    await waitFor(() => expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(9))
    const chart = evolutionChart(container)
    const before = yTicks(chart)
    expect(before.length).toBeGreaterThan(0)
    const assets = evolutionLegend(container).getByRole('button', { name: t('reports.assets') })

    await user.click(assets)

    await waitFor(() => expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(6))
    // The same button, still focused, so a keyboard user can press it again.
    expect(assets).toBeInTheDocument()
    expect(assets).toHaveFocus()
    expect(assets).toHaveAttribute('aria-pressed', 'false')
    // The positive stack now tops out at 6,000 instead of 10,000.
    await waitFor(() => expect(yTicks(chart)).not.toEqual(before))
    // The net worth line is untouched.
    expect(chart.querySelectorAll('.recharts-line')).toHaveLength(1)

    await user.click(assets)

    await waitFor(() => expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(9))
    await waitFor(() => expect(yTicks(chart)).toEqual(before))
  })

  it('the last series still shown cannot be switched off', async () => {
    const { container, user } = renderWithProviders(<ReportsPage />)

    await waitFor(() => expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(9))
    const legend = evolutionLegend(container)
    for (const key of ['reports.accounts', 'reports.assets', 'reports.liabilities']) {
      await user.click(legend.getByRole('button', { name: t(key) }))
    }
    await waitFor(() => expect(container.querySelectorAll('.recharts-bar-rectangle')).toHaveLength(0))

    const netWorth = legend.getByRole('button', { name: t('reports.netWorth') })
    expect(netWorth).toHaveAttribute('aria-disabled', 'true')
    await user.click(netWorth)

    expect(netWorth).toHaveAttribute('aria-pressed', 'true')
    expect(evolutionChart(container).querySelectorAll('.recharts-line')).toHaveLength(1)
  })
})
