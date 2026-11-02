import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'
import { MoneyMapScrubber } from './MoneyMapScrubber'
import { moneyMapSpan } from './money-map-window'

const today = new Date(2026, 8, 12)

describe('MoneyMapScrubber', () => {
  it('draws the window as a band of its own length ending today', () => {
    render(<MoneyMapScrubber monthsBack={0} onChange={() => {}} range={{ months: 12 }} locale="en" today={today} />)
    const band = screen.getByTestId('money-map-window-band')
    // Axis: Oct 2015 .. 12 Sep 2026; a 12-month window is about 1/11 of it.
    const width = parseFloat(band.style.width.replace('max(', ''))
    expect(width).toBeGreaterThan(8)
    expect(width).toBeLessThan(10)
    expect(parseFloat(band.style.left) + width).toBeCloseTo(100, 0)
  })

  it('moves with the keyboard and stops at both ends', () => {
    const onChange = vi.fn()
    const { rerender } = render(
      <MoneyMapScrubber monthsBack={0} onChange={onChange} range={{ months: 3 }} locale="en" today={today} />,
    )
    const slider = screen.getByRole('slider')
    fireEvent.keyDown(slider, { key: 'ArrowRight' })
    expect(onChange).not.toHaveBeenCalled()
    fireEvent.keyDown(slider, { key: 'PageUp' })
    expect(onChange).toHaveBeenLastCalledWith(12)
    fireEvent.keyDown(slider, { key: 'Home' })
    expect(onChange).toHaveBeenLastCalledWith(120)
    rerender(<MoneyMapScrubber monthsBack={120} onChange={onChange} range={{ months: 3 }} locale="en" today={today} />)
    fireEvent.keyDown(slider, { key: 'End' })
    expect(onChange).toHaveBeenLastCalledWith(0)
  })
})

describe('moneyMapSpan', () => {
  it('covers the current window up to today', () => {
    const span = moneyMapSpan(0, { months: 3 }, today)
    expect(span.start).toEqual(new Date(2026, 6, 1))
    expect(span.end).toEqual(new Date(2026, 8, 12))
  })
})
