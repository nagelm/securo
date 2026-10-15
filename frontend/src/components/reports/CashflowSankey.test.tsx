import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { CashflowSankey } from '@/components/reports/CashflowSankey'
import { renderWithProviders } from '@/test/utils'
import type { ReportCompositionItem } from '@/types'

vi.mock('@/hooks/use-privacy-mode', () => ({
  usePrivacyMode: () => ({ mask: (v: string) => v, privacyMode: false, MASK: '••••' }),
}))

class SizedObserver {
  constructor(private cb: ResizeObserverCallback) {}
  observe() {
    this.cb([{ contentRect: { width: 600 } } as ResizeObserverEntry], this as unknown as ResizeObserver)
  }
  unobserve() {}
  disconnect() {}
}

const original = globalThis.ResizeObserver
beforeEach(() => { globalThis.ResizeObserver = SizedObserver as unknown as typeof ResizeObserver })
afterEach(() => { globalThis.ResizeObserver = original })

const flows: ReportCompositionItem[] = [
  { key: 'pay', label: 'Pay', value: 1000, color: '#10B981', group: 'income' },
  { key: 'food', label: 'Food', value: 400, color: '#F43F5E', group: 'expenses' },
]

describe('CashflowSankey (fork)', () => {
  it('draws once data arrives after a first render with none', () => {
    const { container, rerender } = renderWithProviders(
      <CashflowSankey composition={[]} currency="USD" locale="en-US" />,
    )
    expect(container.querySelector('svg')).toBeNull()

    rerender(<CashflowSankey composition={flows} currency="USD" locale="en-US" />)
    expect(container.querySelector('svg')).not.toBeNull()
  })
})
