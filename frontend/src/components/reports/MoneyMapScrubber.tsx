import { useRef } from 'react'
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { MONEY_MAP_MAX_MONTHS_BACK, moneyMapSpan } from './money-map-window'

const MONTH_MS = (365.25 / 12) * 24 * 60 * 60 * 1000

/**
 * Fork (owner 2026-10-15): a timeline scrubber for the Money Map. A neutral
 * track with year marks runs from the furthest reachable window up to today;
 * the selected window is a band of its real length that is dragged (or moved
 * with the arrows / keyboard) along it. Nothing fills up, so it doesn't read
 * as a progress bar.
 */
export function MoneyMapScrubber({
  monthsBack,
  onChange,
  range,
  locale,
  today = new Date(),
}: {
  monthsBack: number
  onChange: (monthsBack: number) => void
  range: { months: number; period?: 'ytd'; days?: number }
  locale: string
  today?: Date
}) {
  const { t } = useTranslation()
  const trackRef = useRef<HTMLDivElement>(null)
  const grabRef = useRef<number | null>(null)

  const axisStart = moneyMapSpan(MONEY_MAP_MAX_MONTHS_BACK, range, today).start.getTime()
  const axisEnd = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()
  const axisSpan = Math.max(1, axisEnd - axisStart)
  const pct = (ms: number) => Math.min(100, Math.max(0, ((ms - axisStart) / axisSpan) * 100))

  const span = moneyMapSpan(monthsBack, range, today)
  const left = pct(span.start.getTime())
  const right = pct(span.end.getTime())

  const set = (n: number) => {
    const next = Math.min(MONEY_MAP_MAX_MONTHS_BACK, Math.max(0, Math.round(n)))
    if (next !== monthsBack) onChange(next)
  }

  // Months back from today for a window ending at this fraction of the track.
  const backForEnd = (fraction: number) => (axisEnd - (axisStart + fraction * axisSpan)) / MONTH_MS

  const fractionAt = (clientX: number) => {
    const box = trackRef.current?.getBoundingClientRect()
    if (!box || box.width === 0) return null
    return (clientX - box.left) / box.width
  }

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    const fraction = fractionAt(e.clientX)
    if (fraction === null) return
    const startFraction = left / 100
    const endFraction = right / 100
    // Grabbing the band keeps the pointer's place in it; a click elsewhere
    // centres the band on the click.
    grabRef.current = fraction >= startFraction && fraction <= endFraction
      ? endFraction - fraction
      : (endFraction - startFraction) / 2
    e.currentTarget.setPointerCapture?.(e.pointerId)
    set(backForEnd(fraction + grabRef.current))
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (grabRef.current === null) return
    const fraction = fractionAt(e.clientX)
    if (fraction !== null) set(backForEnd(fraction + grabRef.current))
  }
  const onPointerUp = () => {
    grabRef.current = null
  }

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const target: Record<string, number> = {
      ArrowLeft: monthsBack + 1,
      ArrowDown: monthsBack + 1,
      ArrowRight: monthsBack - 1,
      ArrowUp: monthsBack - 1,
      PageUp: monthsBack + 12,
      PageDown: monthsBack - 12,
      Home: MONEY_MAP_MAX_MONTHS_BACK,
      End: 0,
    }
    if (e.key in target) {
      e.preventDefault()
      set(target[e.key])
    }
  }

  // Year marks on 1 January, thinned so the labels don't collide.
  const years: number[] = []
  for (let y = new Date(axisStart).getFullYear() + 1; y <= today.getFullYear(); y++) years.push(y)
  const every = years.length > 12 ? 2 : 1

  const fmt = (d: Date) => d.toLocaleDateString(locale, { month: 'short', year: 'numeric' })
  const label = t('reports.moneyMapWindow', { from: fmt(span.start), to: fmt(span.end) })
  const navButton =
    'p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-30 disabled:hover:bg-transparent'

  return (
    <div className="px-5 pt-1 pb-2" data-testid="money-map-slider">
      <div className="flex items-center justify-between gap-3 mb-1.5">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => set(monthsBack + 1)}
            disabled={monthsBack >= MONEY_MAP_MAX_MONTHS_BACK}
            aria-label={t('reports.moneyMapEarlier')}
            className={navButton}
          >
            <ChevronLeft size={16} />
          </button>
          <span className="text-sm font-medium text-foreground tabular-nums min-w-[11rem] text-center">
            {label}
          </span>
          <button
            type="button"
            onClick={() => set(monthsBack - 1)}
            disabled={monthsBack <= 0}
            aria-label={t('reports.moneyMapLater')}
            className={navButton}
          >
            <ChevronRight size={16} />
          </button>
        </div>
        <button
          type="button"
          onClick={() => set(0)}
          disabled={monthsBack === 0}
          className="text-xs px-2 py-0.5 rounded-md border border-border text-muted-foreground hover:text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-default disabled:hover:bg-transparent"
        >
          {t('reports.moneyMapNow')}
        </button>
      </div>

      <div
        ref={trackRef}
        role="slider"
        tabIndex={0}
        aria-label={t('reports.moneyMapSlider')}
        aria-valuemin={-MONEY_MAP_MAX_MONTHS_BACK}
        aria-valuemax={0}
        aria-valuenow={-monthsBack}
        aria-valuetext={label}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        className="relative h-8 mb-4 select-none touch-none cursor-pointer rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {/* The timeline: a thin neutral line with a tick on each 1 January. */}
        <div className="absolute left-0 right-0 top-1/2 h-px bg-border" />
        {years.map((y, i) => (
          <div
            key={y}
            className="absolute top-1/2 -translate-y-1/2 h-3 w-px bg-border"
            style={{ left: `${pct(new Date(y, 0, 1).getTime())}%` }}
          >
            {i % every === 0 && (
              <span className="absolute top-4 -translate-x-1/2 text-[10px] text-muted-foreground tabular-nums">
                {y}
              </span>
            )}
          </div>
        ))}
        {/* Today. */}
        <div className="absolute right-0 top-1 bottom-1 w-0.5 rounded bg-muted-foreground/60" />
        {/* The selected window, as long as the range it covers. */}
        <div
          data-testid="money-map-window-band"
          className="absolute top-1 bottom-1 rounded-md border-2 border-primary bg-primary/15 cursor-grab active:cursor-grabbing"
          style={{ left: `${left}%`, width: `max(${right - left}%, 8px)` }}
        >
          <div className="absolute left-0.5 top-1/2 -translate-y-1/2 h-3 w-0.5 rounded bg-primary" />
          <div className="absolute right-0.5 top-1/2 -translate-y-1/2 h-3 w-0.5 rounded bg-primary" />
        </div>
      </div>
    </div>
  )
}
