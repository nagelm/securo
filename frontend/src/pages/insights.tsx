// Fork: the Insights page, a monthly briefing of patterns in the payment data.
// The backend works the signals out (rules and statistics, no language model);
// this page tells them: a headline, each category against its usual month, and
// the strongest signals sorted into Better and Worse.
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, ChevronLeft, ChevronRight } from 'lucide-react'
import { insights as insightsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { cn } from '@/lib/utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { PageHeader } from '@/components/page-header'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import type { InsightCard, InsightsBriefing } from '@/types'

function wholeCurrency(value: number, currency: string, locale: string): string {
  return new Intl.NumberFormat(locale, { style: 'currency', currency, maximumFractionDigits: 0 }).format(value)
}

function shiftMonth(ym: string, by: number): string {
  const [y, m] = ym.split('-').map(Number)
  const d = new Date(Date.UTC(y, m - 1 + by, 1))
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`
}

function monthLabel(ym: string, locale: string): string {
  const [y, m] = ym.split('-').map(Number)
  return new Date(Date.UTC(y, m - 1, 1)).toLocaleDateString(locale, { month: 'long', year: 'numeric', timeZone: 'UTC' })
}

function dayLabel(iso: string, locale: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString(locale, { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
}

function Sparkline({ values, direction, label }: { values: number[]; direction: 'better' | 'worse'; label: string }) {
  if (values.length < 2) return null
  const w = 150
  const h = 44
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const span = hi - lo || 1
  const pts = values.map((v, i) => [1 + (i * (w - 2)) / (values.length - 1), 4 + (h - 8) * (1 - (v - lo) / span)])
  const [lx, ly] = pts[pts.length - 1]
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-11 w-[150px] shrink-0" role="img" aria-label={label}>
      <polyline
        points={pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}
        fill="none"
        className="stroke-muted-foreground/60"
        strokeWidth={2}
      />
      <circle cx={lx} cy={ly} r={4} className={direction === 'better' ? 'fill-emerald-500' : 'fill-rose-500'} />
    </svg>
  )
}

export default function InsightsPage() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const [month, setMonth] = useState<string | undefined>(undefined)

  const { data, isLoading, isError, error, refetch } = useQuery<InsightsBriefing>({
    queryKey: ['insights', month ?? 'last'],
    queryFn: () => insightsApi.briefing(month),
  })

  const current = data?.month
  const money = (v: number) => mask(wholeCurrency(v, data?.currency ?? 'USD', locale))
  const canBack = !!current && !!data?.first_month && current > data.first_month
  const canForward = !!current && !!data && current < data.last_month

  return (
    <div className="max-w-6xl">
      <PageHeader
        section={t('nav.groupAnalysis')}
        title={t('insights.title')}
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="outline" size="icon" aria-label={t('insights.previousMonth')}
              disabled={!canBack} onClick={() => current && setMonth(shiftMonth(current, -1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span className="min-w-36 text-center text-sm font-semibold">
              {current ? monthLabel(current, locale) : '…'}
            </span>
            <Button
              variant="outline" size="icon" aria-label={t('insights.nextMonth')}
              disabled={!canForward} onClick={() => current && setMonth(shiftMonth(current, 1))}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        }
      />

      {isLoading && (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-72 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      )}

      {isError && (
        <div className="flex items-center gap-3 rounded-xl border border-border bg-card p-4 text-sm">
          <AlertCircle className="h-4 w-4 text-rose-500" />
          <span className="flex-1">{extractApiError(error, t('insights.loadError'))}</span>
          <Button variant="outline" size="sm" onClick={() => refetch()}>{t('common.retry')}</Button>
        </div>
      )}

      {data && <Briefing data={data} money={money} />}
    </div>
  )
}

function Briefing({ data, money }: { data: InsightsBriefing; money: (v: number) => string }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()

  const pct = data.usual_spend > 0 ? Math.round((100 * (data.everyday_spend - data.usual_spend)) / data.usual_spend) : 0
  const rateCard = data.worse.concat(data.better).find((c) => c.kind === 'savings_rate')
  const headline = rateCard
    ? t(
        rateCard.params.leave
          ? 'insights.headline.leave'
          : rateCard.direction === 'worse' ? 'insights.headline.slide' : 'insights.headline.rise',
        { before: rateCard.params.before, rate: rateCard.params.rate },
      )
    : Math.abs(pct) < 3
      ? t('insights.headline.steady')
      : t(pct < 0 ? 'insights.headline.under' : 'insights.headline.over', { pct: Math.abs(pct) })
  const lower = data.categories.filter((c) => c.diff < 0).length
  const higher = data.categories.filter((c) => c.diff > 0).length
  const maxDiff = Math.max(1, ...data.categories.map((c) => Math.abs(c.diff)))

  if (data.categories.length === 0 && data.better.length === 0 && data.worse.length === 0 && data.everyday_spend === 0) {
    return <p className="rounded-xl border border-border bg-card p-6 text-sm text-muted-foreground">{t('insights.empty')}</p>
  }

  return (
    <div className="flex flex-col gap-6">
      <header className="grid gap-6 lg:grid-cols-[1.4fr_1fr] lg:items-end">
        <div className="flex flex-col gap-3">
          <h2 className="text-2xl font-bold leading-tight tracking-tight text-balance">{headline}</h2>
          <p className="text-[15px] leading-relaxed text-muted-foreground">
            {t('insights.summary', {
              spent: money(data.everyday_spend), usual: money(data.usual_spend), lower, higher,
            })}
          </p>
        </div>
        <div className="grid grid-cols-3 gap-3 tabular-nums">
          <Stat
            label={t('insights.everydaySpend')}
            value={money(data.everyday_spend)}
            note={t('insights.vsUsual', { sign: pct < 0 ? '▼' : '▲', pct: Math.abs(pct), usual: money(data.usual_spend) })}
            tone={pct < 0 ? 'good' : pct > 0 ? 'bad' : undefined}
          />
          <Stat
            label={t('insights.savedTwelve')}
            value={data.savings_rate == null ? '—' : `${data.savings_rate}%`}
            note={data.savings_rate_before == null ? '' : t('insights.savedFrom', { before: data.savings_rate_before })}
            tone={
              data.savings_rate == null || data.savings_rate_before == null
                ? undefined
                : data.savings_rate >= data.savings_rate_before ? 'good' : 'bad'
            }
          />
          <Stat
            label={t('insights.oneOffs')}
            value={data.one_off_count === 0 ? t('insights.oneOffsNone') : money(data.one_off_total)}
            note={
              data.one_off_count === 0
                ? t('insights.oneOffsOver', { amount: money(data.one_off_threshold) })
                : t('insights.oneOffsCount', { count: data.one_off_count })
            }
          />
        </div>
      </header>

      {data.categories.length > 0 && (
        <section className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="text-base font-semibold">{t('insights.chartTitle')}</h3>
            <span className="text-xs text-muted-foreground">{t('insights.chartUsual')}</span>
          </div>
          <div className="grid grid-cols-[minmax(0,10rem)_1fr_1fr_4.5rem] items-center gap-x-0 gap-y-2 text-sm tabular-nums">
            <span />
            <span className="pr-2 text-right text-xs text-emerald-600">◀ {t('insights.less')}</span>
            <span className="pl-2 text-xs text-rose-600">{t('insights.more')} ▶</span>
            <span />
            {data.categories.map((c) => {
              const width = `${Math.max(2, (100 * Math.abs(c.diff)) / maxDiff)}%`
              const strong = Math.abs(c.diff) >= Math.max(50, 0.2 * c.usual)
              return (
                <CategoryRow key={c.category_id ?? c.name} c={c} width={width} strong={strong} data={data} money={money} />
              )
            })}
          </div>
          {data.excluded.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {t('insights.excluded', {
                list: (['more_payments', 'fewer_payments', 'one_off'] as const)
                  .map((reason) => {
                    const names = data.excluded.filter((e) => e.reason === reason).map((e) => e.name)
                    return names.length ? `${names.join(', ')} (${t(`insights.reasons.${reason}`)})` : ''
                  })
                  .filter(Boolean)
                  .join('; '),
              })}
            </p>
          )}
        </section>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <Column title={t('insights.better')} tone="good" cards={data.better} empty={t('insights.noneBetter')} money={money} locale={locale} />
        <Column title={t('insights.worse')} tone="bad" cards={data.worse} empty={t('insights.noneWorse')} money={money} locale={locale} />
      </div>
    </div>
  )
}

function Stat({ label, value, note, tone }: { label: string; value: string; note: string; tone?: 'good' | 'bad' }) {
  return (
    <div className="rounded-xl border border-border bg-card px-3.5 py-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="text-xl font-bold">{value}</div>
      <div className={cn('text-xs', tone === 'good' ? 'text-emerald-600' : tone === 'bad' ? 'text-rose-600' : 'text-muted-foreground')}>
        {note}
      </div>
    </div>
  )
}

function CategoryRow({
  c, width, strong, data, money,
}: {
  c: InsightsBriefing['categories'][number]
  width: string
  strong: boolean
  data: InsightsBriefing
  money: (v: number) => string
}) {
  const from = `${data.month}-01`
  const [y, m] = data.month.split('-').map(Number)
  const to = `${data.month}-${String(new Date(Date.UTC(y, m, 0)).getUTCDate()).padStart(2, '0')}`
  const href = c.category_id ? `/transactions?category_id=${c.category_id}&from=${from}&to=${to}` : undefined
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const notes = [
    c.usual_since ? t('insights.usualSince', { month: monthLabel(c.usual_since, locale) }) : '',
    c.drivers.length
      ? t('insights.mostly', {
          list: c.drivers.map((d) => `${d.payee} ${d.diff < 0 ? '−' : '+'}${money(Math.abs(d.diff))}`).join(', '),
        })
      : '',
  ].filter(Boolean)
  const hover = notes.join(' · ') || undefined
  const label = href
    ? <Link to={href} title={hover} className="truncate hover:underline">{c.name}</Link>
    : <span title={hover} className="truncate">{c.name}</span>
  return (
    <>
      {label}
      <div className="flex h-[18px] justify-end border-r border-border">
        {c.diff < 0 && (
          <div style={{ width }} className={cn('h-full rounded-l', strong ? 'bg-emerald-500' : 'bg-emerald-300 dark:bg-emerald-700')} />
        )}
      </div>
      <div className="flex h-[18px]">
        {c.diff > 0 && (
          <div style={{ width }} className={cn('h-full rounded-r', strong ? 'bg-rose-500' : 'bg-rose-300 dark:bg-rose-800')} />
        )}
      </div>
      <span className={cn('text-right', c.diff < 0 ? 'text-emerald-600' : c.diff > 0 ? 'text-rose-600' : 'text-muted-foreground')}>
        {c.diff === 0 ? '—' : `${c.diff < 0 ? '−' : '+'}${money(Math.abs(c.diff))}`}
      </span>
    </>
  )
}

function Column({
  title, tone, cards, empty, money, locale,
}: {
  title: string
  tone: 'good' | 'bad'
  cards: InsightCard[]
  empty: string
  money: (v: number) => string
  locale: string
}) {
  return (
    <section className="flex flex-col gap-3">
      <h3 className={cn('text-xs font-semibold uppercase tracking-wider', tone === 'good' ? 'text-emerald-600' : 'text-rose-600')}>
        {tone === 'good' ? '▼' : '▲'} {title}
      </h3>
      {cards.length === 0 && <p className="text-sm text-muted-foreground">{empty}</p>}
      {cards.map((card, i) => <SignalCard key={`${card.kind}-${i}`} card={card} money={money} locale={locale} />)}
    </section>
  )
}

function SignalCard({ card, money, locale }: { card: InsightCard; money: (v: number) => string; locale: string }) {
  const { t } = useTranslation()
  const text = useMemo(() => describe(card, t, money, locale), [card, t, money, locale])
  const params = new URLSearchParams()
  if (card.category_id) params.set('category_id', card.category_id)
  if (card.payee_id) params.set('payee_id', card.payee_id)
  if (card.link_from) params.set('from', card.link_from)
  if (card.link_to) params.set('to', card.link_to)
  const href = params.toString() && (card.category_id || card.payee_id) ? `/transactions?${params}` : undefined
  const body = (
    <>
      <div className="flex min-w-0 flex-col gap-1">
        <span className="font-semibold">{text.title}</span>
        <span className="truncate text-[13px] text-muted-foreground tabular-nums">{text.detail}</span>
      </div>
      <Sparkline values={card.series} direction={card.direction} label={text.title} />
    </>
  )
  const className = 'grid grid-cols-[1fr_auto] items-center gap-3.5 rounded-xl border border-border bg-card px-4 py-3.5'
  return href ? (
    <Link to={href} title={text.more} className={cn(className, 'transition-colors hover:border-primary/40')}>{body}</Link>
  ) : (
    <div title={text.more} className={className}>{body}</div>
  )
}

type T = ReturnType<typeof useTranslation>['t']

function describe(
  card: InsightCard, t: T, money: (v: number) => string, locale: string,
): { title: string; detail: string; more?: string } {
  const p = card.params as Record<string, string | number>
  const n = (k: string) => Number(p[k] ?? 0)
  switch (card.kind) {
    case 'category_month': {
      const streak = n('streak')
      const title = card.direction === 'worse' && streak >= 2
        ? t('insights.cards.category_month.titleStreak', { category: p.category, count: streak })
        : t(`insights.cards.category_month.title_${card.direction}`, { category: p.category })
      return {
        title,
        detail: t('insights.cards.category_month.detail', {
          amount: money(n('amount')), usual: money(n('usual')), count: n('visits'),
        }),
        more: p.driver
          ? t('insights.mostly', { list: `${p.driver} ${n('driver_diff') < 0 ? '−' : '+'}${money(Math.abs(n('driver_diff')))}` })
          : undefined,
      }
    }
    case 'trend_cooling':
      return {
        title: t('insights.cards.trend_cooling.title', { category: p.category }),
        detail: t('insights.cards.trend_cooling.detail', {
          total: money(n('total')), peak: money(n('peak')), month: monthLabel(String(p.peak_month), locale),
        }),
      }
    case 'trend_rising':
      return {
        title: t('insights.cards.trend_rising.title', { category: p.category }),
        detail: t('insights.cards.trend_rising.detail', { total: money(n('total')), before: money(n('before')), pct: n('change_pct') }),
      }
    case 'savings_rate': {
      const signed = (v: number) => `${v < 0 ? '−' : '+'}${money(Math.abs(v))}`
      const movers = (key: string) =>
        ((card.params[key] as unknown as { name: string; diff: number }[] | undefined) ?? [])
          .map((m) => `${m.name} ${signed(m.diff)}`)
          .join(', ')
      const more = [
        t('insights.cards.savings_rate.income', { change: signed(n('income_change')), movers: movers('income_movers') }),
        t('insights.cards.savings_rate.spending', { change: signed(n('spend_change')), movers: movers('spend_movers') }),
        card.params.leave ? t('insights.cards.savings_rate.leave', { payer: p.leave_payer }) : '',
      ].filter(Boolean).join(' ')
      return {
        title: card.params.leave
          ? t('insights.cards.savings_rate.title_leave')
          : t(`insights.cards.savings_rate.title_${card.direction}`),
        detail: t('insights.cards.savings_rate.short', {
          rate: n('rate'), before: n('before'), income: signed(n('income_change')),
        }),
        more,
      }
    }
    case 'payee_switch':
      return {
        title: t('insights.cards.payee_switch.title', { from: p.from_payee, to: p.to_payee }),
        detail: t('insights.cards.payee_switch.detail', {
          category: p.category, before: money(n('before')), after: money(n('after')),
          pct: `${n('change_pct') >= 0 ? '+' : '−'}${Math.abs(n('change_pct'))}`, since: dayLabel(String(p.since), locale),
        }),
      }
    case 'habit_ended':
      return {
        title: t('insights.cards.habit_ended.title', { payee: p.payee }),
        detail: t('insights.cards.habit_ended.detail', {
          monthly: money(n('monthly')), category: p.category, last: dayLabel(String(p.last), locale),
        }),
      }
    case 'price_up':
    case 'price_down':
      return {
        title: t(`insights.cards.${card.kind}.title`, { payee: p.payee }),
        detail: t('insights.cards.price.detail', {
          before: money(n('before')), after: money(n('after')), since: dayLabel(String(p.since), locale),
        }) + (card.params.yearly ? ` · ${t('insights.cards.price.yearly')}` : ''),
      }
    case 'payee_new':
      return {
        title: t('insights.cards.payee_new.title', { payee: p.payee }),
        detail: t('insights.cards.payee_new.detail', { amount: money(n('amount')), category: p.category }),
      }
    case 'payee_stopped':
      return {
        title: t('insights.cards.payee_stopped.title', { payee: p.payee }),
        detail: t('insights.cards.payee_stopped.detail', {
          amount: money(n('amount')), last: dayLabel(String(p.last), locale), category: p.category,
        }),
      }
    case 'small_burst':
      return {
        title: t('insights.cards.small_burst.title', { payee: p.payee }),
        detail: t('insights.cards.small_burst.detail', {
          count: n('count'), amount: money(n('amount')), usual: n('usual_count'),
        }),
      }
    case 'income_change':
      return {
        title: t(`insights.cards.income_change.title_${card.direction}`, { payee: p.payee }),
        detail: t('insights.cards.income_change.detail', {
          before: money(n('before')), after: money(n('after')), pct: n('change_pct'),
        }),
      }
    default:
      return { title: card.kind, detail: '' }
  }
}
