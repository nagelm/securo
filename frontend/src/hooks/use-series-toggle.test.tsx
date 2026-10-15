import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'

import { useSeriesToggle } from '@/hooks/use-series-toggle'

function setup(initial: string[]) {
  return renderHook(({ keys }) => useSeriesToggle(keys), { initialProps: { keys: initial } })
}

describe('useSeriesToggle', () => {
  it('shows every series to begin with', () => {
    const { result } = setup(['a', 'b', 'c'])

    expect(result.current.isHidden('a')).toBe(false)
    expect(result.current.visibleKeys).toEqual(['a', 'b', 'c'])
    expect(result.current.anyHidden).toBe(false)
  })

  it('hides a series on the first toggle and shows it again on the second', () => {
    const { result } = setup(['a', 'b', 'c'])

    act(() => result.current.toggle('b'))
    expect(result.current.isHidden('b')).toBe(true)
    expect(result.current.visibleKeys).toEqual(['a', 'c'])
    expect(result.current.anyHidden).toBe(true)

    act(() => result.current.toggle('b'))
    expect(result.current.isHidden('b')).toBe(false)
    expect(result.current.visibleKeys).toEqual(['a', 'b', 'c'])
  })

  it('never hides the last series still shown', () => {
    const { result } = setup(['a', 'b'])

    act(() => result.current.toggle('a'))
    expect(result.current.canHide('b')).toBe(false)

    act(() => result.current.toggle('b'))
    expect(result.current.isHidden('b')).toBe(false)
    expect(result.current.visibleKeys).toEqual(['b'])
  })

  it('a single series can never be hidden', () => {
    const { result } = setup(['only'])

    expect(result.current.canHide('only')).toBe(false)
    act(() => result.current.toggle('only'))
    expect(result.current.isHidden('only')).toBe(false)
  })

  it('ignores keys the chart does not have', () => {
    const { result } = setup(['a', 'b'])

    act(() => result.current.toggle('zzz'))
    expect(result.current.anyHidden).toBe(false)
    expect(result.current.isHidden('zzz')).toBe(false)
  })

  it('keeps a hide while its series stays, and drops it when the series goes', () => {
    const { result, rerender } = setup(['a', 'b', 'c'])
    act(() => result.current.toggle('b'))

    // Another series arrives: the hide still applies.
    rerender({ keys: ['a', 'b', 'c', 'd'] })
    expect(result.current.isHidden('b')).toBe(true)
    expect(result.current.visibleKeys).toEqual(['a', 'c', 'd'])

    // The hidden series goes and comes back: it starts shown again.
    rerender({ keys: ['a', 'c', 'd'] })
    expect(result.current.anyHidden).toBe(false)
    rerender({ keys: ['a', 'b', 'c', 'd'] })
    expect(result.current.isHidden('b')).toBe(false)
  })

  it('shows everything again when the remaining series would all be hidden', () => {
    const { result, rerender } = setup(['a', 'b', 'c'])
    act(() => result.current.toggle('a'))
    act(() => result.current.toggle('b'))

    rerender({ keys: ['a', 'b'] })

    expect(result.current.visibleKeys).toEqual(['a', 'b'])
    expect(result.current.anyHidden).toBe(false)
  })
})
