import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { formatCurrency } from '@/lib/format'
import { useSeriesToggle, type SeriesToggle } from '@/hooks/use-series-toggle'
import { ToggleLegend } from '@/components/chart-legend-toggle'

/**
 * In-chat chart, rendered when an assistant message contains a fenced
 * code block tagged `securo-chart`. The body is JSON in this shape:
 *
 *   { type: "line"|"bar"|"area"|"pie", title?, currency?, data, series? }
 *
 * Multi-series example:
 *   {
 *     "type": "line",
 *     "title": "Income vs expenses",
 *     "currency": "BRL",
 *     "data": [{"x":"Jan","income":3000,"expense":1800}, ...],
 *     "series": [
 *       {"key":"income","name":"Income","color":"#10B981"},
 *       {"key":"expense","name":"Expense","color":"#F43F5E"}
 *     ]
 *   }
 *
 * Single-series shorthand (no `series` array, default key is `y`):
 *   { "type":"bar", "data":[{"x":"Food","y":500},{"x":"Rent","y":1200}] }
 *
 * Pie:
 *   { "type":"pie", "data":[{"name":"Food","value":500},{"name":"Rent","value":1200}] }
 */

// Same palette as reports.tsx so chats look like the rest of the app.
const PALETTE = [
  '#6366F1', // indigo
  '#10B981', // emerald
  '#F59E0B', // amber
  '#F43F5E', // rose
  '#8B5CF6', // violet
  '#06B6D4', // cyan
  '#EC4899', // pink
  '#84CC16', // lime
]

type ChartType = 'line' | 'bar' | 'area' | 'pie'

interface SeriesSpec {
  key: string
  name?: string
  color?: string
}

export interface ChartSpec {
  type: ChartType
  title?: string
  subtitle?: string
  currency?: string
  x_label?: string
  y_label?: string
  data: Record<string, unknown>[]
  series?: SeriesSpec[]
}

interface Props {
  spec: ChartSpec
}

const tooltipStyle: React.CSSProperties = {
  backgroundColor: 'var(--popover)',
  color: 'var(--popover-foreground)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  fontSize: 12,
}

function formatY(v: number | string | undefined | null, currency?: string): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (currency) return formatCurrency(v, currency, undefined)
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(v)
}

/**
 * Compact axis labels — "R$100K", "R$1.2M", "12K" — so big numbers
 * actually fit in the axis gutter. Tooltips still get the full
 * precision via formatY().
 */
function formatYAxis(v: number | string | undefined | null, currency?: string): string {
  if (v == null) return ''
  if (typeof v === 'string') return v
  if (Math.abs(v) < 1000) {
    if (currency) {
      try {
        return new Intl.NumberFormat(undefined, {
          style: 'currency',
          currency,
          minimumFractionDigits: 0,
          maximumFractionDigits: 0,
        }).format(v)
      } catch {
        return `${currency} ${v.toFixed(0)}`
      }
    }
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(v)
  }
  if (currency) {
    try {
      return new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency,
        notation: 'compact',
        maximumFractionDigits: 1,
      }).format(v)
    } catch {
      return `${currency} ${v}`
    }
  }
  return new Intl.NumberFormat(undefined, {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(v)
}

function AgentChartImpl({ spec }: Props) {
  const { t } = useTranslation()
  const hasData = !!spec && Array.isArray(spec.data) && spec.data.length > 0

  const series: SeriesSpec[] =
    spec?.series && spec.series.length > 0
      ? spec.series
      : [{ key: 'y' }]

  const seriesWithColor = series.map((s, i) => ({ ...s, color: s.color || PALETTE[i % PALETTE.length] }))

  // Legend toggles: one per pie slice, or one per series when a legend shows.
  const pieSlices = hasData && spec.type === 'pie' ? toPieSlices(spec.data) : []
  const toggleKeys =
    spec?.type === 'pie'
      ? pieSlices.map((s) => s.key)
      : series.length > 1
        ? seriesWithColor.map((s) => s.key)
        : []
  const toggle = useSeriesToggle(toggleKeys)

  if (!hasData) {
    return (
      <div className="my-3 rounded-md border border-dashed border-border px-3 py-4 text-xs text-muted-foreground text-center">
        {t('agents.chart.noData')}
      </div>
    )
  }

  return (
    <figure className="my-3 rounded-lg border border-border bg-card overflow-hidden">
      {(spec.title || spec.subtitle) && (
        <figcaption className="px-3 py-2 border-b border-border">
          {spec.title && <div className="text-sm font-semibold">{spec.title}</div>}
          {spec.subtitle && <div className="text-xs text-muted-foreground mt-0.5">{spec.subtitle}</div>}
        </figcaption>
      )}
      <div className="h-[260px] px-2 pt-3 pb-2">
        <ResponsiveContainer width="100%" height="100%">
          {renderChart(spec.type, spec.data, seriesWithColor, pieSlices, toggle, spec.currency)}
        </ResponsiveContainer>
      </div>
    </figure>
  )
}

interface PieSlice {
  key: string
  name: string
  value: number
  color: string
}

// Pie expects { name, value } shape. Map a friendly fallback. Each slice keeps
// the colour of its place in the data, so hiding one doesn't recolour the rest.
function toPieSlices(data: Record<string, unknown>[]): PieSlice[] {
  return data.map((d, i) => ({
    key: String(i),
    name: String(d.name ?? d.x ?? d.label ?? `Slice ${i + 1}`),
    value: Number(d.value ?? d.y ?? 0),
    color: PALETTE[i % PALETTE.length],
  }))
}

type ResolvedSeries = { key: string; name?: string; color: string }

function renderChart(
  type: ChartType,
  data: Record<string, unknown>[],
  series: ResolvedSeries[],
  pieSlices: PieSlice[],
  toggle: SeriesToggle,
  currency?: string,
) {
  if (type === 'pie') {
    // Hidden slices leave the pie, so the rest re-proportion.
    const items = pieSlices.filter((s) => !toggle.isHidden(s.key))
    return (
      <PieChart>
        <Pie
          data={items}
          dataKey="value"
          nameKey="name"
          outerRadius={90}
          innerRadius={48}
          paddingAngle={1}
          stroke="var(--background)"
          strokeWidth={1.5}
        >
          {items.map((s) => (
            <Cell key={s.key} fill={s.color} />
          ))}
        </Pie>
        <Tooltip
          contentStyle={tooltipStyle}
          formatter={(v) => formatY(v as number | string | undefined, currency)}
        />
        <Legend
          wrapperStyle={{ fontSize: 11 }}
          content={
            <ToggleLegend
              items={pieSlices.map((s) => ({ key: s.key, label: s.name, color: s.color }))}
              toggle={toggle}
            />
          }
        />
      </PieChart>
    )
  }

  const ChartCmp = type === 'bar' ? BarChart : type === 'area' ? AreaChart : LineChart

  return (
    <ChartCmp data={data} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
      <XAxis
        dataKey="x"
        tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
        axisLine={false}
        tickLine={false}
        interval="preserveStartEnd"
      />
      <YAxis
        tickFormatter={(v) => formatYAxis(v, currency)}
        tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
        axisLine={false}
        tickLine={false}
        width={56}
        tickCount={5}
      />
      <Tooltip
        contentStyle={tooltipStyle}
        formatter={(v, name) => [formatY(v as number | string | undefined, currency), String(name ?? '')]}
      />
      {series.length > 1 && (
        <Legend
          wrapperStyle={{ fontSize: 11 }}
          content={
            <ToggleLegend
              items={series.map((s) => ({
                key: s.key,
                label: s.name || s.key,
                color: s.color,
                icon: type === 'bar' ? 'rect' : 'line',
              }))}
              toggle={toggle}
            />
          }
        />
      )}
      {series.map((s) => {
        const common = {
          key: s.key,
          dataKey: s.key,
          name: s.name || s.key,
          stroke: s.color,
          // A hidden series leaves the chart, the axis and the tooltip.
          hide: toggle.isHidden(s.key),
        }
        if (type === 'bar') {
          return <Bar key={s.key} dataKey={s.key} name={s.name || s.key} fill={s.color} radius={[4, 4, 0, 0]} hide={toggle.isHidden(s.key)} />
        }
        if (type === 'area') {
          return (
            <Area
              {...common}
              type="monotone"
              fill={s.color}
              fillOpacity={0.18}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 3 }}
            />
          )
        }
        // line
        return (
          <Line
            {...common}
            type="monotone"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3 }}
          />
        )
      })}
    </ChartCmp>
  )
}

/** Memoized export. The whole reason this exists: every keystroke in
 *  the chat textarea triggers a parent re-render, and Recharts treats
 *  any new prop reference as new data — reanimating slices and bars.
 *  We compare specs by serialized string (cheap; specs are tiny JSON
 *  blobs the LLM emitted once) so a re-render with the same spec is a
 *  no-op. */
export const AgentChart = memo(
  AgentChartImpl,
  (prev, next) => JSON.stringify(prev.spec) === JSON.stringify(next.spec),
)
