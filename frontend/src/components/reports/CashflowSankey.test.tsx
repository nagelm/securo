import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { CashflowSankey } from '@/components/reports/CashflowSankey'
import { renderWithProviders } from '@/test/utils'
import type { ReportCompositionItem } from '@/types'

function item(group: string, key: string, label: string, value: number): ReportCompositionItem {
  return { group, key, label, value, color: '#000000' }
}

function render(composition: ReportCompositionItem[]) {
  renderWithProviders(<CashflowSankey composition={composition} currency="USD" locale="en-US" />)
}

// The chart only lays out with a measured width, which jsdom never has, so
// these tests read the accessible table the component renders from the same
// nodes.
describe('CashflowSankey investment withdrawals (#1418)', () => {
  it('draws a withdrawal as a source, so spending it is no deficit', () => {
    render([
      item('income', 'salary', 'Salary', 1000),
      item('withdrawals', 'invest', 'Investments', 500),
      item('expenses', 'rent', 'Rent', 1400),
    ])

    expect(screen.getByText('Investment withdrawals')).toBeInTheDocument()
    expect(screen.queryByText('Deficit')).not.toBeInTheDocument()
    expect(screen.getByText('Surplus')).toBeInTheDocument()
  })

  it('folds every withdrawal category into one node', () => {
    render([
      item('income', 'salary', 'Salary', 1000),
      item('withdrawals', 'stocks', 'Stocks', 200),
      item('withdrawals', 'bonds', 'Bonds', 300),
      item('expenses', 'rent', 'Rent', 900),
    ])

    expect(screen.getAllByText('Investment withdrawals')).toHaveLength(1)
    expect(screen.queryByText('Stocks')).not.toBeInTheDocument()
    expect(screen.queryByText('Bonds')).not.toBeInTheDocument()
  })

  it('still shows a deficit the withdrawals do not cover', () => {
    render([
      item('income', 'salary', 'Salary', 1000),
      item('withdrawals', 'invest', 'Investments', 100),
      item('expenses', 'rent', 'Rent', 1400),
    ])

    expect(screen.getByText('Deficit')).toBeInTheDocument()
  })

  it('charts a month with only a withdrawal', () => {
    render([item('withdrawals', 'invest', 'Investments', 100)])

    expect(screen.queryByText('No data available for this period')).not.toBeInTheDocument()
    expect(screen.getByText('Investment withdrawals')).toBeInTheDocument()
  })
})

// Fork: privacy mode off, and the chart measured, for the redraw test below.
vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (v: string) => v, privacyMode: false, MASK: '••••' }),
}))

class SizedObserver {
  private cb: ResizeObserverCallback
  constructor(cb: ResizeObserverCallback) {
    this.cb = cb
  }
  observe() {
    this.cb([{ contentRect: { width: 600 } } as ResizeObserverEntry], this as unknown as ResizeObserver)
  }
  unobserve() {}
  disconnect() {}
}

const flows: ReportCompositionItem[] = [
  { key: 'pay', label: 'Pay', value: 1000, color: '#10B981', group: 'income' },
  { key: 'food', label: 'Food', value: 400, color: '#F43F5E', group: 'expenses' },
]

describe('CashflowSankey (fork)', () => {
  const original = globalThis.ResizeObserver
  beforeEach(() => { globalThis.ResizeObserver = SizedObserver as unknown as typeof ResizeObserver })
  afterEach(() => { globalThis.ResizeObserver = original })

  it('draws once data arrives after a first render with none', () => {
    const { container, rerender } = renderWithProviders(
      <CashflowSankey composition={[]} currency="USD" locale="en-US" />,
    )
    expect(container.querySelector('svg')).toBeNull()

    rerender(<CashflowSankey composition={flows} currency="USD" locale="en-US" />)
    expect(container.querySelector('svg')).not.toBeNull()
  })
})
