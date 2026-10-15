import type { CSSProperties, ReactNode } from 'react'
import type { SeriesToggle } from '@/hooks/use-series-toggle'
import { cn } from '@/lib/utils'

/**
 * One legend entry that switches its series on and off. It keeps whatever
 * layout the caller gives it (`className`), and a switched-off entry stays
 * on screen, dimmed and struck through, so it can be clicked back on.
 */
export function LegendToggle({
  seriesKey,
  toggle,
  className,
  style,
  children,
}: {
  seriesKey: string
  toggle: SeriesToggle
  className?: string
  style?: CSSProperties
  children: ReactNode
}) {
  const hidden = toggle.isHidden(seriesKey)
  const locked = !hidden && !toggle.canHide(seriesKey)
  return (
    <button
      type="button"
      aria-pressed={!hidden}
      aria-disabled={locked || undefined}
      data-series-key={seriesKey}
      onClick={(e) => {
        // A legend drawn inside a clickable chart must not also click the chart.
        e.stopPropagation()
        toggle.toggle(seriesKey)
      }}
      className={cn(
        className,
        'rounded-sm transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50',
        locked ? 'cursor-default' : 'cursor-pointer',
        hidden && 'opacity-40 line-through',
      )}
      style={style}
    >
      {children}
    </button>
  )
}

export type LegendIcon = 'circle' | 'rect' | 'line'

export interface LegendItem {
  key: string
  label: ReactNode
  color: string
  icon?: LegendIcon
}

const SIZE = 32

function Icon({ icon, color }: { icon: LegendIcon; color: string }) {
  if (icon === 'circle') return <circle cx={SIZE / 2} cy={SIZE / 2} r={SIZE / 2} fill={color} />
  if (icon === 'rect') return <path stroke="none" fill={color} d={`M0,${SIZE / 8}h${SIZE}v${(SIZE * 3) / 4}h${-SIZE}z`} />
  const half = SIZE / 2
  const sixth = SIZE / 6
  const third = SIZE / 3
  return (
    <path
      strokeWidth={4}
      fill="none"
      stroke={color}
      d={`M0,${half}h${third}A${sixth},${sixth},0,1,1,${2 * third},${half}H${SIZE}M${2 * third},${half}A${sixth},${sixth},0,1,1,${third},${half}`}
    />
  )
}

/**
 * Content for a Recharts `<Legend>`: the same row of icon + coloured label
 * that Recharts draws by default, with each entry a toggle button.
 */
export function ToggleLegend({
  items,
  toggle,
  iconSize = 14,
  defaultIcon = 'rect',
}: {
  items: LegendItem[]
  toggle: SeriesToggle
  iconSize?: number
  defaultIcon?: LegendIcon
}) {
  if (items.length === 0) return null
  return (
    <ul className="recharts-default-legend" style={{ padding: 0, margin: 0, textAlign: 'center' }}>
      {items.map((item) => (
        <li key={item.key} className="recharts-legend-item" style={{ display: 'inline-block', marginRight: 10 }}>
          <LegendToggle seriesKey={item.key} toggle={toggle}>
            <svg
              width={iconSize}
              height={iconSize}
              viewBox={`0 0 ${SIZE} ${SIZE}`}
              style={{ display: 'inline-block', verticalAlign: 'middle', marginRight: 4 }}
              aria-hidden="true"
            >
              <Icon icon={item.icon ?? defaultIcon} color={item.color} />
            </svg>
            <span className="recharts-legend-item-text" style={{ color: item.color }}>
              {item.label}
            </span>
          </LegendToggle>
        </li>
      ))}
    </ul>
  )
}
