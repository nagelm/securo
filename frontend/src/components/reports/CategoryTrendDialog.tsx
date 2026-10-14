import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { CategoryTrendItem } from '@/types'

// Fork: the detail view behind a Category Trends card (owner 2026-10-14).
// The card's sparkline is too small to read; this shows each period as a bar
// with the average, the highest and lowest periods, and a way to the rows.

interface Props {
  item: CategoryTrendItem | null
  label: string
  onClose: () => void
  formatMoney: (value: number) => string
  formatAxis: (value: number) => string
  tooltipStyle: React.CSSProperties
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function CategoryTrendDialog({ item, label, onClose, formatMoney, formatAxis, tooltipStyle }: Props) {
  const { t } = useTranslation()
  const series = item?.series ?? []
  const values = series.map((s) => s.value)
  const average = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0
  const highest = series.reduce<(typeof series)[number] | null>((m, s) => (m === null || s.value > m.value ? s : m), null)
  const lowest = series.reduce<(typeof series)[number] | null>((m, s) => (m === null || s.value < m.value ? s : m), null)
  const data = series.map((s) => ({ date: s.date, v: s.value }))
  const linkable = item && UUID.test(item.key)

  return (
    <Dialog open={item !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: item?.color }} />
            {label}
          </DialogTitle>
          <DialogDescription>
            {item?.group === 'income' ? t('reports.trendDetailIncome') : t('reports.trendDetailExpenses')}
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { k: 'total', title: t('reports.trendTotal'), value: formatMoney(item?.total ?? 0), sub: null },
            { k: 'avg', title: t('reports.trendAverage'), value: formatMoney(average), sub: null },
            { k: 'max', title: t('reports.trendHighest'), value: formatMoney(highest?.value ?? 0), sub: highest?.date },
            { k: 'min', title: t('reports.trendLowest'), value: formatMoney(lowest?.value ?? 0), sub: lowest?.date },
          ].map((stat) => (
            <div key={stat.k} className="rounded-lg border border-border/50 bg-muted/20 px-3 py-2">
              <p className="text-[11px] text-muted-foreground">{stat.title}</p>
              <p className="text-sm font-bold tabular-nums text-foreground">{stat.value}</p>
              {stat.sub && <p className="text-[11px] text-muted-foreground truncate">{stat.sub}</p>}
            </div>
          ))}
        </div>

        <div style={{ height: 320 }} data-testid="category-trend-detail-chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="var(--border)" />
              <XAxis dataKey="date" tick={{ fontSize: 11 }} tickLine={false} axisLine={false} minTickGap={8} />
              <YAxis tick={{ fontSize: 11 }} tickLine={false} axisLine={false} width={64} tickFormatter={formatAxis} />
              <Tooltip
                formatter={(value?: number) => [formatMoney(value ?? 0), label]}
                labelFormatter={(l) => l}
                contentStyle={tooltipStyle}
                cursor={{ fill: 'var(--muted)', opacity: 0.4 }}
              />
              <ReferenceLine
                y={average}
                stroke="var(--muted-foreground)"
                strokeDasharray="4 4"
                label={{ value: t('reports.trendAverage'), position: 'insideTopRight', fontSize: 11, fill: 'var(--muted-foreground)' }}
              />
              <Bar dataKey="v" fill={item?.color} radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {linkable && (
          <div className="flex justify-end">
            <Link
              to={`/transactions?category_id=${item.key}`}
              className="text-sm font-semibold text-primary hover:underline"
              onClick={onClose}
            >
              {t('reports.trendViewTransactions')}
            </Link>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
