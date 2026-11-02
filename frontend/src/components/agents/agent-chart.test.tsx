import { describe, expect, it, vi } from 'vitest'
import { screen, waitFor } from '@testing-library/react'
import { createElement, type ComponentProps } from 'react'

import { AgentChart } from '@/components/agents/agent-chart'
import { renderWithProviders } from '@/test/utils'

// Pie slices animate in from zero; draw them at once. The responsive
// container keeps the global setup's test viewport.
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

describe('AgentChart legend (fork)', () => {
  it('a pie legend click takes the slice out and a second click puts it back', async () => {
    const { container, user } = renderWithProviders(
      <AgentChart spec={{ type: 'pie', data: [{ name: 'One', value: 1 }, { name: 'Two', value: 2 }, { name: 'Three', value: 3 }] }} />,
    )
    await waitFor(() => expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(3))

    const two = screen.getByRole('button', { name: 'Two' })
    await user.click(two)

    await waitFor(() => expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(2))
    expect(two).toHaveAttribute('aria-pressed', 'false')

    await user.click(two)
    await waitFor(() => expect(container.querySelectorAll('.recharts-pie-sector')).toHaveLength(3))
  })

  it('a line legend click hides that line', async () => {
    const { container, user } = renderWithProviders(
      <AgentChart
        spec={{
          type: 'line',
          data: [{ x: 'Jan', a: 1, b: 5 }, { x: 'Feb', a: 2, b: 6 }],
          series: [{ key: 'a', name: 'Alpha' }, { key: 'b', name: 'Beta' }],
        }}
      />,
    )
    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(2))

    await user.click(screen.getByRole('button', { name: 'Beta' }))
    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(1))

    await user.click(screen.getByRole('button', { name: 'Beta' }))
    await waitFor(() => expect(container.querySelectorAll('.recharts-line')).toHaveLength(2))
  })
})
