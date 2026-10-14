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
      { category_id: 'cat-food', name: 'Groceries', amount: 400, usual: 480, diff: -80, usual_since: null, drivers: [] },
      {
        category_id: 'cat-soft', name: 'Software', amount: 300, usual: 150, diff: 150, usual_since: '2026-04',
        drivers: [{ payee_id: 'pay-ai', payee: 'NewAI', diff: 150 }],
      },
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
    // A category that changed level says where its usual starts, and who moved it.
    // Kept to one line; the detail is a hover note.
    expect(screen.getByRole('link', { name: 'Software' })).toHaveAttribute('title', 'usual since April 2026 · mostly NewAI +$150')
  })

  it('without a savings-rate signal, the headline compares everyday spending with a usual month', async () => {
    api.insights.briefing.mockResolvedValue(briefing({ worse: [], everyday_spend: 800 }))
    renderWithProviders(<InsightsPage />)
    expect(await screen.findByText(t('insights.headline.under', { pct: 20 }))).toBeInTheDocument()
    expect(screen.getByText(t('insights.noneWorse'))).toBeInTheDocument()
  })

  it('tells leave and a split as one story each', async () => {
    api.insights.briefing.mockResolvedValue(briefing({
      worse: [
        {
          kind: 'savings_rate', direction: 'worse', series: [30, 12], category_id: null, payee_id: null, link_from: null, link_to: null,
          params: {
            rate: 12, before: 30, change: -18, income_change: -20000, spend_change: 5000, leave: true, leave_payer: 'IRD',
            income_movers: [{ name: 'Employer', diff: -20000 }], spend_movers: [{ name: 'Groceries', diff: 3000 }],
          },
        },
        {
          kind: 'payee_switch', direction: 'worse', series: [1, 2], category_id: 'cat-rates', payee_id: null,
          link_from: '2026-07-01', link_to: '2026-09-30',
          params: { from_payee: 'Council', to_payee: 'Water', category: 'Rates', before: 478.00, after: 521.00, change_pct: 9, since: '2026-08-21' },
        },
      ],
    }))
    renderWithProviders(<InsightsPage />)
    expect(await screen.findByText(t('insights.headline.leave', { before: 30, rate: 12 }))).toBeInTheDocument()
    expect(screen.getByText(t('insights.cards.savings_rate.title_leave'))).toBeInTheDocument()
    expect(screen.getByText('12% kept, from 30% · income −$20,000')).toBeInTheDocument()
    const card = screen.getByText(t('insights.cards.savings_rate.title_leave')).closest('[title]')
    expect(card?.getAttribute('title')).toContain('Income −$20,000 (Employer −$20,000)')
    expect(screen.getByText(t('insights.cards.payee_switch.title', { from: 'Council', to: 'Water' }))).toBeInTheDocument()
    expect(screen.getByText(/Rates: \$478 → \$521 a month together \(\+9%\)/)).toBeInTheDocument()
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
