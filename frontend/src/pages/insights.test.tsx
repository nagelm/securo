import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import InsightsPage from '@/pages/insights'
import { renderWithProviders, t } from '@/test/utils'
import type { InsightsBriefing } from '@/types'

const api = vi.hoisted(() => ({
  insights: { briefing: vi.fn() },
}))

vi.mock('@/lib/api', () => api)

vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (value: string) => value, privacyMode: false, MASK: '••••' }),
}))

// Made-up figures.
function briefing(overrides: Partial<InsightsBriefing> = {}): InsightsBriefing {
  return {
    month: '2026-09',
    currency: 'USD',
    first_month: '2024-01',
    last_month: '2026-09',
    everyday_spend: 960,
    usual_spend: 1000,
    income: 3000,
    savings_rate: 12,
    savings_rate_before: 30,
    one_off_count: 0,
    one_off_total: 0,
    one_off_threshold: 1000,
    categories: [
      { category_id: 'cat-food', name: 'Groceries', amount: 400, usual: 480, diff: -80 },
      { category_id: 'cat-eat', name: 'Eating out', amount: 300, usual: 150, diff: 150 },
    ],
    excluded: [{ category_id: 'cat-int', name: 'Interest', reason: 'more_payments' }],
    better: [{
      kind: 'trend_cooling', direction: 'better', params: { category: 'Takeaways', total: 2600, peak: 3600, peak_month: '2025-10' },
      series: [1, 2, 3, 2], category_id: 'cat-take', payee_id: null, link_from: '2025-10-01', link_to: '2026-09-30',
    }],
    worse: [
      {
        kind: 'savings_rate', direction: 'worse', params: { rate: 12, before: 30, change: -18 },
        series: [30, 25, 20, 12], category_id: null, payee_id: null, link_from: null, link_to: null,
      },
      {
        kind: 'price_up', direction: 'worse', params: { payee: 'StreamCo', before: 25, after: 29, since: '2026-08-06', cycle_days: 30 },
        series: [25, 25, 29, 29], category_id: null, payee_id: 'pay-1', link_from: '2025-10-01', link_to: '2026-09-30',
      },
    ],
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.insights.briefing.mockResolvedValue(briefing())
})

describe('InsightsPage', () => {
  it('leads with the savings rate and sorts signals into better and worse', async () => {
    renderWithProviders(<InsightsPage />)
    expect(await screen.findByText(t('insights.headline.slide', { before: 30, rate: 12 }))).toBeInTheDocument()
    expect(screen.getByText(t('insights.cards.trend_cooling.title', { category: 'Takeaways' }))).toBeInTheDocument()
    expect(screen.getByText(t('insights.cards.price_up.title', { payee: 'StreamCo' }))).toBeInTheDocument()
    // A payee card links to that payee's transactions in its window.
    const link = screen.getByText(t('insights.cards.price_up.title', { payee: 'StreamCo' })).closest('a')
    expect(link?.getAttribute('href')).toBe('/transactions?payee_id=pay-1&from=2025-10-01&to=2026-09-30')
  })

  it('draws each category against its usual month and says what it left out', async () => {
    renderWithProviders(<InsightsPage />)
    const groceries = await screen.findByText('Groceries')
    expect(groceries.closest('a')?.getAttribute('href')).toBe('/transactions?category_id=cat-food&from=2026-09-01&to=2026-09-30')
    expect(screen.getByText(/Interest/)).toBeInTheDocument()
  })

  it('without a savings-rate signal, the headline compares everyday spending with a usual month', async () => {
    api.insights.briefing.mockResolvedValue(briefing({ worse: [], everyday_spend: 800 }))
    renderWithProviders(<InsightsPage />)
    expect(await screen.findByText(t('insights.headline.under', { pct: 20 }))).toBeInTheDocument()
    expect(screen.getByText(t('insights.noneWorse'))).toBeInTheDocument()
  })

  it('steps back a month', async () => {
    renderWithProviders(<InsightsPage />)
    await screen.findByText('Groceries')
    expect(api.insights.briefing).toHaveBeenLastCalledWith(undefined)
    fireEvent.click(screen.getByRole('button', { name: t('insights.previousMonth') }))
    await waitFor(() => expect(api.insights.briefing).toHaveBeenLastCalledWith('2026-08'))
    expect(screen.getByRole('button', { name: t('insights.nextMonth') })).toBeDisabled()
  })
})
