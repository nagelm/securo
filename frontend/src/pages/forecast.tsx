import React, { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Bar,
  ComposedChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
} from 'recharts'
import { AlertCircle, ArrowDown, ArrowUp, HelpCircle, Pencil, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  accounts as accountsApi,
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  forecast as forecastApi,
  recurring as recurringApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { getAccountName, sortAccountsByPosition } from '@/lib/account-utils'
import { localDateString } from '@/lib/date-utils'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { useDisplayLocale, useDateLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { PageHeader } from '@/components/page-header'
import { CategorySelect } from '@/components/category-select'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import type {
  Account,
  Category,
  CategoryGroup,
  ForecastItem,
  ForecastItemKind,
  ForecastResponse,
  ForecastScenario,
  LoanForecast,
  RecurringTransaction,
  SpendingEstimate,
} from '@/types'

// Same forward presets as the cash flow report, stretched to five years.
const RANGE_OPTIONS = [
  { key: '3m', months: 3, label: 'reports.range3m' },
  { key: '6m', months: 6, label: 'reports.range6m' },
  { key: '1y', months: 12, label: 'reports.range1y' },
  { key: '2y', months: 24, label: 'reports.range2y' },
  { key: '5y', months: 60, label: 'reports.range5y' },
] as const

const INTERVAL_OPTIONS = [
  { value: 'weekly', label: 'reports.intervalWeekly' },
  { value: 'monthly', label: 'reports.intervalMonthly' },
] as const

const BASE_COLOR = '#6366F1'
// Fork: the real balance before today, neutral; projections after it are
// dashed, as on Reports → Cash Flow.
const ACTUAL_COLOR = '#64748B'
const FORECAST_DASH = '6 3'
// Loans section bars: what each repayment paid in interest and in principal.
const INTEREST_COLOR = '#E11D48'
// Offered to a new scenario in turn; the base line keeps indigo.
const SCENARIO_COLORS = ['#F59E0B', '#10B981', '#EC4899', '#0EA5E9', '#8B5CF6', '#F97316', '#14B8A6', '#F43F5E']

const TH = 'text-xs font-medium text-muted-foreground py-3'
const selectClass = 'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

const tooltipStyle = {
  background: 'var(--card)',
  color: 'var(--foreground)',
  border: '1px solid var(--border)',
  borderRadius: '0.75rem',
  boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
  fontSize: '12px',
}

function formatCompact(value: number, currency: string, locale: string) {
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden mb-5">
      {children}
    </div>
  )
}

function SectionHeader({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div>
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      {action}
    </div>
  )
}

function useFrequencyLabel() {
  const { t } = useTranslation()
  return (f: string | null) => (f ? t(`recurring.${f}`, { defaultValue: f }) : '')
}

function invalidateForecast(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: ['forecast'] })
  queryClient.invalidateQueries({ queryKey: ['forecast-items'] })
  queryClient.invalidateQueries({ queryKey: ['forecast-scenarios'] })
  // Expected payments also feed Reports → Cash Flow.
  queryClient.invalidateQueries({ queryKey: ['reports'] })
}

function ForecastView() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const { user } = useAuth()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const [rangeKey, setRangeKey] = useState<(typeof RANGE_OPTIONS)[number]['key']>('1y')
  const [interval, setInterval] = useState<'weekly' | 'monthly'>('monthly')
  const [includeSpending, setIncludeSpending] = useState(true)
  const months = RANGE_OPTIONS.find((r) => r.key === rangeKey)?.months ?? 12

  const { data, isLoading, isError, error, refetch } = useQuery<ForecastResponse>({
    queryKey: ['forecast', months, interval, includeSpending],
    queryFn: () => forecastApi.get(months, interval, includeSpending),
  })
  const currency = data?.currency ?? userCurrency
  const money = (v: number) => mask(formatCurrency(v, currency, locale))
  const shortDate = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(dateLocale, { day: 'numeric', month: 'short', year: '2-digit' })

  // One row per date. A line's lowest day is added as its own row (other
  // lines skip it via connectNulls) so the marker sits on the real low.
  const chartData = useMemo(() => {
    if (!data) return []
    const rows = new Map<string, Record<string, number | string | null>>()
    const row = (date: string) => {
      let r = rows.get(date)
      if (!r) {
        r = { date }
        rows.set(date, r)
      }
      return r
    }
    for (const p of data.actuals ?? []) row(p.date).actual = p.value
    for (const p of data.base) row(p.date).base = p.value
    row(data.summary.lowest_date).base = data.summary.lowest_balance
    for (const s of data.scenarios) {
      for (const p of s.points) row(p.date)[s.id] = p.value
      row(s.summary.lowest_date)[s.id] = s.summary.lowest_balance
    }
    return [...rows.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)))
  }, [data])

  const hasActuals = (data?.actuals ?? []).length > 0
  const actualSeries = { key: 'actual', name: t('forecast.actual'), color: ACTUAL_COLOR }
  const lines = useMemo(
    () => [
      { key: 'base', name: t('forecast.base'), color: BASE_COLOR, summary: data?.summary },
      ...(data?.scenarios ?? []).map((s) => ({ key: s.id, name: s.name, color: s.color, summary: s.summary })),
    ],
    [data, t],
  )

  return (
    <div>
      <PageHeader
        section={t('forecast.section')}
        title={t('forecast.title')}
        action={
          <div className="flex flex-wrap items-center gap-2">
            <div
              className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors ${
                includeSpending
                  ? 'border-primary/40 bg-primary/10 text-primary'
                  : 'border-border bg-card text-muted-foreground'
              }`}
            >
              <button
                type="button"
                onClick={() => setIncludeSpending((v) => !v)}
                className="flex items-center gap-2 hover:text-foreground transition-colors"
                aria-pressed={includeSpending}
              >
                <span
                  className={`relative inline-flex h-3.5 w-6 shrink-0 items-center rounded-full transition-colors ${
                    includeSpending ? 'bg-primary' : 'bg-muted'
                  }`}
                >
                  <span
                    className={`inline-block h-2.5 w-2.5 transform rounded-full bg-white transition-transform ${
                      includeSpending ? 'translate-x-3' : 'translate-x-0.5'
                    }`}
                  />
                </span>
                {t('forecast.includeSpending')}
              </button>
              <span
                title={t('forecast.includeSpendingHelp')}
                aria-label={t('forecast.includeSpendingHelp')}
                className="inline-flex cursor-help"
              >
                <HelpCircle className="h-3.5 w-3.5 opacity-60" />
              </span>
            </div>
            <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
              {RANGE_OPTIONS.map((opt) => (
                <button
                  key={opt.key}
                  onClick={() => setRangeKey(opt.key)}
                  className={`px-3 py-1.5 text-xs font-semibold transition-colors ${
                    rangeKey === opt.key
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  }`}
                >
                  {t(opt.label)}
                </button>
              ))}
            </div>
            <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden">
              {INTERVAL_OPTIONS.map((opt) => (
                <button
                  key={opt.value}
                  onClick={() => setInterval(opt.value)}
                  className={`px-2.5 py-1.5 text-xs font-semibold transition-colors ${
                    interval === opt.value
                      ? 'bg-primary text-primary-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                  }`}
                >
                  {t(opt.label)}
                </button>
              ))}
            </div>
          </div>
        }
      />

      {isError && (
        <div className="flex items-center justify-between gap-3 bg-rose-50 dark:bg-rose-500/10 border border-rose-200 dark:border-rose-500/30 rounded-lg px-4 py-2.5 mb-5">
          <div className="flex items-center gap-2.5 min-w-0">
            <AlertCircle size={16} className="shrink-0 text-rose-600 dark:text-rose-400" />
            <span className="text-sm text-rose-900 dark:text-rose-200 truncate">
              {extractApiError(error, t('forecast.loadError'))}
            </span>
          </div>
          <button
            type="button"
            onClick={() => refetch()}
            className="shrink-0 text-sm font-semibold text-rose-600 dark:text-rose-400 hover:underline"
          >
            {t('common.retry')}
          </button>
        </div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 mb-5">
        {isLoading || !data ? (
          <>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-24 w-full" />
          </>
        ) : (
          <>
            <div className="bg-card rounded-xl border border-border shadow-sm px-5 py-4">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-0.5">
                {t('forecast.endBalance')}
              </p>
              <p className="text-2xl font-bold tabular-nums text-foreground">{money(data.summary.end_balance)}</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t('forecast.fromToday', { amount: money(data.starting_balance) })}
              </p>
            </div>
            <div className="bg-card rounded-xl border border-border shadow-sm px-5 py-4">
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-0.5">
                {t('forecast.lowestBalance')}
              </p>
              <p className={cn('text-2xl font-bold tabular-nums', data.summary.lowest_balance < 0 ? 'text-rose-500' : 'text-foreground')}>
                {money(data.summary.lowest_balance)}
              </p>
              <p className="text-xs text-muted-foreground mt-0.5">
                {t('forecast.onDate', { date: shortDate(data.summary.lowest_date) })}
              </p>
            </div>
            {data.scenarios.map((s) => (
              <div key={s.id} className="bg-card rounded-xl border border-border shadow-sm px-5 py-4" data-testid="scenario-summary">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <div className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: s.color }} />
                  <p className="text-xs font-medium text-muted-foreground truncate">{s.name}</p>
                </div>
                <p className="text-2xl font-bold tabular-nums text-foreground">{money(s.summary.end_balance)}</p>
                <p className={cn('text-xs font-semibold tabular-nums mt-0.5', s.difference >= 0 ? 'text-emerald-600' : 'text-rose-500')}>
                  {mask(`${s.difference >= 0 ? '+' : '−'}${formatCurrency(Math.abs(s.difference), currency, locale)}`)}{' '}
                  <span className="font-normal text-muted-foreground">{t('forecast.vsBase')}</span>
                </p>
                <p className="text-[11px] text-muted-foreground">
                  {t('forecast.lowestOn', { amount: money(s.summary.lowest_balance), date: shortDate(s.summary.lowest_date) })}
                </p>
              </div>
            ))}
          </>
        )}
      </div>

      {/* Chart */}
      <div className="bg-card rounded-xl border border-border shadow-sm mb-5">
        <div className="px-5 pt-5 pb-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-foreground">{t('forecast.chartTitle')}</p>
          <div className="flex flex-wrap items-center gap-3">
            {[...(hasActuals ? [actualSeries] : []), ...lines].map((l) => (
              <div key={l.key} className="flex items-center gap-1.5">
                <div className="w-3 h-0.5 rounded-full" style={{ backgroundColor: l.color }} />
                <span className="text-[11px] text-muted-foreground">{l.name}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="px-1 pb-4" style={{ height: 340 }}>
          {isLoading || !data ? (
            <div className="px-4 h-full"><Skeleton className="h-full w-full" /></div>
          ) : (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData} margin={{ top: 12, right: 24, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="date"
                  tickFormatter={shortDate}
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tickFormatter={(v) => (privacyMode ? '' : v === 0 ? '0' : formatCompact(v, currency, locale))}
                  tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }}
                  axisLine={false}
                  tickLine={false}
                  width={64}
                  tickCount={5}
                />
                <Tooltip
                  content={({ active, payload, label }) => {
                    if (!active || !payload || payload.length === 0) return null
                    return (
                      <div style={tooltipStyle} className="px-3 py-2">
                        <p className="text-xs font-medium mb-1">{shortDate(String(label))}</p>
                        {payload.map((p) => {
                          const line = [actualSeries, ...lines].find((l) => l.key === p.dataKey)
                          if (!line || p.value == null) return null
                          return (
                            <p key={line.key} className="text-xs" style={{ color: line.color }}>
                              {line.name}: {privacyMode ? MASK : formatCurrency(Number(p.value), currency, locale)}
                            </p>
                          )
                        })}
                      </div>
                    )
                  }}
                />
                <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="3 3" />
                {hasActuals && (
                  <ReferenceLine
                    x={data.start_date}
                    stroke="var(--muted-foreground)"
                    strokeDasharray="3 3"
                    strokeOpacity={0.6}
                    label={{ value: t('reports.today'), position: 'insideTopRight', fill: 'var(--muted-foreground)', fontSize: 10 }}
                  />
                )}
                {hasActuals && (
                  <Line
                    type="monotone"
                    dataKey="actual"
                    name={actualSeries.name}
                    stroke={ACTUAL_COLOR}
                    strokeWidth={2}
                    dot={false}
                    activeDot={{ r: 4, fill: ACTUAL_COLOR }}
                    connectNulls
                    isAnimationActive={false}
                  />
                )}
                {lines.map((l) => (
                  <Line
                    key={l.key}
                    type="monotone"
                    dataKey={l.key}
                    name={l.name}
                    stroke={l.color}
                    strokeWidth={l.key === 'base' ? 2.5 : 2}
                    strokeDasharray={FORECAST_DASH}
                    dot={false}
                    activeDot={{ r: 4, fill: l.color }}
                    connectNulls
                    isAnimationActive={false}
                  />
                ))}
                {lines.map((l) =>
                  l.summary ? (
                    <ReferenceDot
                      key={`low-${l.key}`}
                      x={l.summary.lowest_date}
                      y={l.summary.lowest_balance}
                      r={4}
                      fill="var(--card)"
                      stroke={l.color}
                      strokeWidth={2}
                    />
                  ) : null,
                )}
                {lines.map((l) =>
                  l.summary ? (
                    <ReferenceDot
                      key={`end-${l.key}`}
                      x={data.end_date}
                      y={l.summary.end_balance}
                      r={4}
                      fill={l.color}
                      stroke={l.color}
                      label={privacyMode ? undefined : {
                        value: formatCompact(l.summary.end_balance, currency, locale),
                        position: 'left',
                        fontSize: 10,
                        fill: l.color,
                      }}
                    />
                  ) : null,
                )}
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </div>
        <p className="px-5 pb-4 text-[11px] text-muted-foreground">{t('forecast.chartHint')}</p>
      </div>

      <LoansSection loans={data?.loans ?? []} />
      <RecurringLine />
      <ExpectedPaymentsSection />
      <ScenariosSection estimates={data?.spending_estimates ?? []} loans={data?.loans ?? []} currency={currency} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Loans (fork): each table loan's balance and repayments, base and scenarios
// ---------------------------------------------------------------------------

function useFrequencyName() {
  const { t } = useTranslation()
  return (f: string | null) => (f ? t(`forecast.loans.frequencies.${f}`, { defaultValue: f.toLowerCase() }) : '')
}

/** "3 months" / "2 weeks" for a payoff shift given in days. */
function usePayoffShift() {
  const { t } = useTranslation()
  return (days: number) => {
    const abs = Math.abs(days)
    const months = Math.round(abs / 30.44)
    const period = months >= 1
      ? t('forecast.loans.months', { count: months })
      : t('forecast.loans.weeks', { count: Math.max(1, Math.round(abs / 7)) })
    return days < 0 ? t('forecast.loans.earlier', { period }) : t('forecast.loans.later', { period })
  }
}

function LoansSection({ loans }: { loans: LoanForecast[] }) {
  const { t } = useTranslation()
  if (loans.length === 0) return null
  return (
    <SectionCard>
      <SectionHeader title={t('forecast.loans.title')} hint={t('forecast.loans.hint')} />
      <div className="divide-y divide-border">
        {loans.map((loan) => (
          <LoanRow key={loan.account_id} loan={loan} />
        ))}
      </div>
    </SectionCard>
  )
}

function LoanRow({ loan }: { loan: LoanForecast }) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask, privacyMode, MASK } = usePrivacyMode()
  const frequencyName = useFrequencyName()
  const payoffShift = usePayoffShift()
  const money = (v: number) => mask(formatCurrency(v, loan.currency, locale))
  const shortDate = (iso: string) =>
    new Date(iso + 'T00:00:00').toLocaleDateString(dateLocale, { day: 'numeric', month: 'short', year: '2-digit' })
  const monthYear = (iso: string | null) =>
    iso ? new Date(iso + 'T00:00:00').toLocaleDateString(dateLocale, { month: 'short', year: 'numeric' }) : t('forecast.loans.never')
  const base = loan.base

  const rows = useMemo(() => {
    if (!base) return []
    const byDate = new Map<string, Record<string, number | string>>()
    for (const p of loan.actuals ?? []) byDate.set(p.date, { date: p.date, actual: p.value })
    for (const p of base.points) {
      byDate.set(p.date, { ...byDate.get(p.date), date: p.date, base: p.balance, interest: p.interest, principal: p.principal })
    }
    for (const s of loan.scenarios) {
      for (const p of s.points) {
        const row = byDate.get(p.date) ?? { date: p.date }
        row[s.scenario_id] = p.balance
        byDate.set(p.date, row)
      }
    }
    return [...byDate.values()].sort((a, b) => String(a.date).localeCompare(String(b.date)))
  }, [base, loan.actuals, loan.scenarios])
  const startIso = base?.points[0]?.date ?? ''
  // Bars are what each future repayment pays, so they start after today.
  const splitRows = rows.filter((r) => typeof r.interest === 'number' && String(r.date) > startIso)
  const hasHistory = (loan.actuals ?? []).length > 0
  const lines = [
    { key: 'base', name: t('forecast.base'), color: BASE_COLOR },
    ...loan.scenarios.map((s) => ({ key: s.scenario_id, name: s.name, color: s.color })),
  ]
  const axisTick = { fontSize: 10, fill: 'var(--muted-foreground)' }
  const yTick = (v: number) => (privacyMode ? '' : v === 0 ? '0' : formatCompact(v, loan.currency, locale))

  return (
    <div className="px-4 sm:px-5 py-4" data-testid="loan-row">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <Link to={`/accounts/${loan.account_id}`} className="text-sm font-semibold text-foreground hover:underline">
          {loan.name}
        </Link>
        <p className="text-xs text-muted-foreground tabular-nums">
          {t('forecast.loans.owed', { amount: money(loan.balance) })}
          {loan.rate != null && <> · {t('forecast.loans.rateValue', { rate: loan.rate })}</>}
          {base && <> · {t('forecast.loans.repaymentValue', { amount: money(base.repayment), frequency: frequencyName(loan.frequency) })}</>}
        </p>
      </div>

      {!base ? (
        <p className="text-xs text-muted-foreground mt-2" data-testid="loan-missing-details">
          {t('forecast.loans.missingDetails')}{' '}
          <Link to={`/accounts/${loan.account_id}`} className="font-semibold text-primary hover:underline">
            {t('forecast.loans.addDetails')}
          </Link>
        </p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2">
            <div>
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">{t('forecast.loans.payoff')}</p>
              <p className="text-sm font-semibold text-foreground tabular-nums">{monthYear(base.payoff_date)}</p>
            </div>
            <div>
              <p className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">{t('forecast.loans.totalInterest')}</p>
              <p className="text-sm font-semibold text-foreground tabular-nums">{money(base.total_interest)}</p>
            </div>
          </div>
          {loan.scenarios.length > 0 && (
            <ul className="mt-2 space-y-1">
              {loan.scenarios.map((s) => {
                const saved = s.interest_saved
                return (
                  <li key={s.scenario_id} className="flex items-center gap-1.5 text-xs" data-testid="loan-scenario">
                    <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: s.color }} />
                    <span className="font-medium text-foreground">{s.name}:</span>
                    <span className="text-muted-foreground">
                      {s.payoff_date == null
                        ? t('forecast.loans.neverPaidOff')
                        : s.payoff_change_days == null || s.payoff_change_days === 0
                          ? t('forecast.loans.samePayoff', { date: monthYear(s.payoff_date) })
                          : `${payoffShift(s.payoff_change_days)} (${monthYear(s.payoff_date)})`}
                      {saved != null && saved !== 0 && (
                        <>
                          {', '}
                          <span className={cn('font-semibold', saved > 0 ? 'text-emerald-600' : 'text-rose-500')}>
                            {saved > 0
                              ? t('forecast.loans.saves', { amount: money(saved) })
                              : t('forecast.loans.costs', { amount: money(-saved) })}
                          </span>
                        </>
                      )}
                      {s.repayment !== base.repayment && (
                        <>{', '}{t('forecast.loans.newRepayment', { amount: money(s.repayment) })}</>
                      )}
                    </span>
                  </li>
                )
              })}
            </ul>
          )}

          <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <p className="text-[11px] font-medium text-muted-foreground mb-1">{t('forecast.loans.balanceChart')}</p>
              <div style={{ height: 180 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                    <XAxis dataKey="date" tickFormatter={shortDate} tick={axisTick} axisLine={false} tickLine={false} interval="preserveStartEnd" />
                    <YAxis tickFormatter={yTick} tick={axisTick} axisLine={false} tickLine={false} width={56} tickCount={4} />
                    <Tooltip
                      content={({ active, payload, label }) => {
                        if (!active || !payload || payload.length === 0) return null
                        return (
                          <div style={tooltipStyle} className="px-3 py-2">
                            <p className="text-xs font-medium mb-1">{shortDate(String(label))}</p>
                            {payload.map((p) => {
                              const line = [{ key: 'actual', name: t('forecast.actual'), color: ACTUAL_COLOR }, ...lines].find((l) => l.key === p.dataKey)
                              if (!line || p.value == null) return null
                              return (
                                <p key={line.key} className="text-xs" style={{ color: line.color }}>
                                  {line.name}: {privacyMode ? MASK : formatCurrency(Number(p.value), loan.currency, locale)}
                                </p>
                              )
                            })}
                          </div>
                        )
                      }}
                    />
                    {hasHistory && (
                      <ReferenceLine x={startIso} stroke="var(--muted-foreground)" strokeDasharray="3 3" strokeOpacity={0.6} />
                    )}
                    {hasHistory && (
                      <Line
                        type="monotone"
                        dataKey="actual"
                        name={t('forecast.actual')}
                        stroke={ACTUAL_COLOR}
                        strokeWidth={2}
                        dot={false}
                        connectNulls
                        isAnimationActive={false}
                      />
                    )}
                    {lines.map((l) => (
                      <Line
                        key={l.key}
                        type="monotone"
                        dataKey={l.key}
                        name={l.name}
                        stroke={l.color}
                        strokeWidth={l.key === 'base' ? 2.5 : 2}
                        strokeDasharray={FORECAST_DASH}
                        dot={false}
                        connectNulls
                        isAnimationActive={false}
                      />
                    ))}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-3 mb-1">
                <p className="text-[11px] font-medium text-muted-foreground">{t('forecast.loans.splitChart')}</p>
                <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: INTEREST_COLOR }} />{t('forecast.loans.interest')}
                </span>
                <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                  <span className="w-2 h-2 rounded-sm" style={{ backgroundColor: BASE_COLOR }} />{t('forecast.loans.principal')}
                </span>
              </div>
              <div style={{ height: 180 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={splitRows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                    <XAxis dataKey="date" tickFormatter={shortDate} tick={axisTick} axisLine={false} tickLine={false} interval="preserveStartEnd" />
                    <YAxis tickFormatter={yTick} tick={axisTick} axisLine={false} tickLine={false} width={56} tickCount={4} />
                    <Tooltip
                      cursor={{ fill: 'var(--muted)', opacity: 0.4 }}
                      content={({ active, payload, label }) => {
                        if (!active || !payload || payload.length === 0) return null
                        return (
                          <div style={tooltipStyle} className="px-3 py-2">
                            <p className="text-xs font-medium mb-1">{shortDate(String(label))}</p>
                            {payload.map((p) => (
                              <p key={String(p.dataKey)} className="text-xs" style={{ color: p.color }}>
                                {p.dataKey === 'interest' ? t('forecast.loans.interest') : t('forecast.loans.principal')}:{' '}
                                {privacyMode ? MASK : formatCurrency(Number(p.value), loan.currency, locale)}
                              </p>
                            ))}
                          </div>
                        )
                      }}
                    />
                    <Bar dataKey="interest" stackId="split" fill={INTEREST_COLOR} isAnimationActive={false} />
                    <Bar dataKey="principal" stackId="split" fill={BASE_COLOR} radius={[2, 2, 0, 0]} isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 1. Recurring: one line; the list itself lives on the Recurring tab
// ---------------------------------------------------------------------------

function RecurringLine() {
  const { t } = useTranslation()
  const { data: recurringList } = useQuery({ queryKey: ['recurring'], queryFn: recurringApi.list })
  const count = (recurringList ?? []).filter((r) => r.is_active).length

  return (
    <p className="text-xs text-muted-foreground mb-5 px-1" data-testid="recurring-line">
      {t('forecast.recurringLine', { count })}
      {' · '}
      <Link to="/recurring" className="font-semibold text-primary hover:underline">
        {t('forecast.manageRecurring')}
      </Link>
    </p>
  )
}

// ---------------------------------------------------------------------------
// 2. Expected payments
// ---------------------------------------------------------------------------

function useFormLookups() {
  const { data: accountsList } = useQuery({ queryKey: ['accounts'], queryFn: () => accountsApi.list() })
  const { data: categoriesList } = useQuery({ queryKey: ['categories'], queryFn: categoriesApi.list })
  const { data: categoryGroupsList } = useQuery({ queryKey: ['categoryGroups'], queryFn: categoryGroupsApi.list })
  return {
    accounts: accountsList ?? [],
    categories: categoriesList ?? [],
    categoryGroups: categoryGroupsList ?? [],
  }
}

function ExpectedPaymentsSection() {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const { accounts, categories, categoryGroups } = useFormLookups()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ForecastItem | null>(null)
  const [deleting, setDeleting] = useState<ForecastItem | null>(null)

  const { data: items } = useQuery({ queryKey: ['forecast-items'], queryFn: forecastApi.items.list })
  const expected = (items ?? []).filter((i) => i.scenario_id === null)

  const close = () => { setDialogOpen(false); setEditing(null) }
  const saveMutation = useMutation({
    mutationFn: (payload: Partial<ForecastItem>) =>
      editing ? forecastApi.items.update(editing.id, payload) : forecastApi.items.create(payload),
    onSuccess: () => {
      invalidateForecast(queryClient)
      toast.success(t(editing ? 'forecast.expectedUpdated' : 'forecast.expectedCreated'))
      close()
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => forecastApi.items.delete(id),
    onSuccess: () => {
      invalidateForecast(queryClient)
      setDeleting(null)
      toast.success(t('forecast.expectedDeleted'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  const accountName = (id: string | null) => {
    const acc = accounts.find((a) => a.id === id)
    return acc ? getAccountName(acc) : '—'
  }

  return (
    <SectionCard>
      <SectionHeader
        title={t('forecast.expectedTitle')}
        hint={t('forecast.expectedHint')}
        action={
          canWrite ? (
            <Button size="sm" className="gap-1.5 h-8" onClick={() => { setEditing(null); setDialogOpen(true) }}>
              <Plus size={13} /> <span className="hidden sm:inline">{t('forecast.addExpected')}</span>
            </Button>
          ) : undefined
        }
      />
      {expected.length > 0 ? (
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th className={`${TH} pl-4 sm:pl-5 text-left w-28`}>{t('forecast.date')}</th>
              <th className={`${TH} text-left`}>{t('recurring.description')}</th>
              <th className={`${TH} text-left hidden md:table-cell`}>{t('recurring.account')}</th>
              <th className={`${TH} text-left w-36`}>{t('recurring.amount')}</th>
              <th className={`${TH} text-left w-28 hidden sm:table-cell`}>{t('recurring.status')}</th>
              {canWrite && <th className={`${TH} pr-4 sm:pr-5 text-right w-24`}>{t('recurring.actions')}</th>}
            </tr>
          </thead>
          <tbody>
            {expected.map((item) => (
              <tr key={item.id} className="border-b border-border last:border-0 hover:bg-muted transition-colors">
                <td className="py-2.5 pl-4 sm:pl-5 text-xs text-muted-foreground tabular-nums">
                  {item.date ? new Date(item.date + 'T00:00:00').toLocaleDateString(dateLocale) : '—'}
                </td>
                <td className="py-2.5 text-sm text-foreground">{item.description || '—'}</td>
                <td className="py-2.5 text-xs text-muted-foreground hidden md:table-cell">{accountName(item.account_id)}</td>
                <td className={`py-2.5 text-xs sm:text-sm font-semibold tabular-nums ${item.type === 'credit' ? 'text-emerald-600' : 'text-rose-500'}`}>
                  {mask(`${item.type === 'credit' ? '+' : '−'}${formatCurrency(Number(item.amount ?? 0), item.currency, locale)}`)}
                </td>
                <td className="py-2.5 hidden sm:table-cell">
                  <span className={cn(
                    'text-[11px] font-semibold px-2 py-0.5 rounded-full border',
                    item.matched_transaction_id
                      ? 'bg-emerald-50 text-emerald-600 border-emerald-100'
                      : 'bg-muted text-muted-foreground border-border',
                  )}>
                    {item.matched_transaction_id ? t('forecast.arrived') : t('forecast.expected')}
                  </span>
                </td>
                {canWrite && (
                  <td className="py-2.5 pr-4 sm:pr-5">
                    <div className="flex items-center justify-end gap-1">
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                        onClick={() => { setEditing(item); setDialogOpen(true) }}
                        aria-label={t('common.edit')}
                        title={t('common.edit')}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                        onClick={() => setDeleting(item)}
                        aria-label={t('common.delete')}
                        title={t('common.delete')}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-sm text-muted-foreground text-center py-8">{t('forecast.expectedEmpty')}</p>
      )}

      <Dialog open={dialogOpen} onOpenChange={close}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('forecast.editExpected') : t('forecast.addExpected')}</DialogTitle>
          </DialogHeader>
          <ExpectedPaymentForm
            key={editing?.id ?? 'new'}
            item={editing}
            accounts={accounts}
            categories={categories}
            categoryGroups={categoryGroups}
            loading={saveMutation.isPending}
            onCancel={close}
            onSave={(payload) => saveMutation.mutate(payload)}
          />
        </DialogContent>
      </Dialog>

      <DeleteConfirmationDialog
        open={!!deleting}
        title={t('forecast.confirmDeleteExpected')}
        description={t('forecast.confirmDeleteExpectedDescription', { description: deleting?.description || '—' })}
        isPending={deleteMutation.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && deleteMutation.mutate(deleting.id)}
      />
    </SectionCard>
  )
}

function ExpectedPaymentForm({
  item,
  accounts,
  categories,
  categoryGroups,
  loading,
  onCancel,
  onSave,
}: {
  item: ForecastItem | null
  accounts: Account[]
  categories: Category[]
  categoryGroups: CategoryGroup[]
  loading: boolean
  onCancel: () => void
  onSave: (payload: Partial<ForecastItem>) => void
}) {
  const { t } = useTranslation()
  const sortedAccounts = useMemo(() => sortAccountsByPosition(accounts.filter((a) => !a.is_closed)), [accounts])
  const [description, setDescription] = useState(item?.description ?? '')
  const [amount, setAmount] = useState(item?.amount != null ? String(Number(item.amount)) : '')
  const [type, setType] = useState<'debit' | 'credit'>(item?.type ?? 'debit')
  const [date, setDate] = useState(item?.date ?? localDateString())
  const [accountId, setAccountId] = useState(item?.account_id ?? sortedAccounts[0]?.id ?? '')
  const [categoryId, setCategoryId] = useState(item?.category_id ?? '')

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        onSave({
          kind: 'one_off',
          description,
          amount: amount,
          type,
          date,
          account_id: accountId || null,
          category_id: categoryId || null,
        })
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="expected-description">{t('recurring.description')}</Label>
        <Input id="expected-description" value={description} onChange={(e) => setDescription(e.target.value)} required />
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="expected-amount">{t('recurring.amount')}</Label>
          <Input id="expected-amount" type="number" step="0.01" min="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        </div>
        <div className="space-y-2">
          <Label htmlFor="expected-type">{t('recurring.type')}</Label>
          <select id="expected-type" className={selectClass} value={type} onChange={(e) => setType(e.target.value as 'debit' | 'credit')}>
            <option value="debit">{t('forecast.moneyOut')}</option>
            <option value="credit">{t('forecast.moneyIn')}</option>
          </select>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>{t('forecast.date')}</Label>
          <DatePickerInput value={date} onChange={setDate} className="w-full justify-start" />
        </div>
        <div className="space-y-2">
          <Label htmlFor="expected-account">{t('recurring.account')}</Label>
          <select id="expected-account" className={selectClass} value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            <option value="">{t('forecast.noAccount')}</option>
            {sortedAccounts.map((acc) => (
              <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="space-y-2">
        <Label>{t('recurring.category')}</Label>
        <CategorySelect
          value={categoryId}
          onChange={setCategoryId}
          categories={categories}
          groups={categoryGroups}
          allowNone={true}
          className={selectClass}
        />
      </div>
      <p className="text-xs text-muted-foreground">{t('forecast.matchHint')}</p>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={loading}>{loading ? t('common.loading') : t('common.save')}</Button>
      </DialogFooter>
    </form>
  )
}

// ---------------------------------------------------------------------------
// 3. Scenarios
// ---------------------------------------------------------------------------

function ScenariosSection({
  estimates,
  loans,
  currency,
}: {
  estimates: SpendingEstimate[]
  loans: LoanForecast[]
  currency: string
}) {
  const { t } = useTranslation()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const lookups = useFormLookups()
  const { data: recurringList } = useQuery({ queryKey: ['recurring'], queryFn: recurringApi.list })
  const { data: scenarios } = useQuery({ queryKey: ['forecast-scenarios'], queryFn: forecastApi.scenarios.list })
  const { data: items } = useQuery({ queryKey: ['forecast-items'], queryFn: forecastApi.items.list })
  const [scenarioDialog, setScenarioDialog] = useState<{ open: boolean; scenario: ForecastScenario | null }>({ open: false, scenario: null })
  const [changeDialog, setChangeDialog] = useState<{ scenarioId: string; item: ForecastItem | null } | null>(null)
  const [deleting, setDeleting] = useState<ForecastScenario | null>(null)
  const ordered = useMemo(() => [...(scenarios ?? [])].sort((a, b) => a.position - b.position), [scenarios])

  const onError = (err: unknown) => toast.error(extractApiError(err, t('common.error')))
  const updateScenario = useMutation({
    mutationFn: ({ id, ...payload }: Partial<ForecastScenario> & { id: string }) => forecastApi.scenarios.update(id, payload),
    onSuccess: () => invalidateForecast(queryClient),
    onError,
  })
  const saveScenario = useMutation({
    mutationFn: (payload: Partial<ForecastScenario>) =>
      scenarioDialog.scenario
        ? forecastApi.scenarios.update(scenarioDialog.scenario.id, payload)
        : forecastApi.scenarios.create(payload),
    onSuccess: () => {
      invalidateForecast(queryClient)
      setScenarioDialog({ open: false, scenario: null })
    },
    onError,
  })
  const deleteScenario = useMutation({
    mutationFn: (id: string) => forecastApi.scenarios.delete(id),
    onSuccess: () => {
      invalidateForecast(queryClient)
      setDeleting(null)
      toast.success(t('forecast.scenarioDeleted'))
    },
    onError,
  })
  const saveChange = useMutation({
    mutationFn: (payload: Partial<ForecastItem>) =>
      changeDialog?.item ? forecastApi.items.update(changeDialog.item.id, payload) : forecastApi.items.create(payload),
    onSuccess: () => {
      invalidateForecast(queryClient)
      setChangeDialog(null)
    },
    onError,
  })
  const deleteChange = useMutation({
    mutationFn: (id: string) => forecastApi.items.delete(id),
    onSuccess: () => invalidateForecast(queryClient),
    onError,
  })

  // Swap positions with the neighbour; positions are renumbered 0..n first so
  // ties from concurrent creates cannot stall a move.
  const move = async (index: number, by: -1 | 1) => {
    const other = ordered[index + by]
    if (!other) return
    const current = ordered[index]
    try {
      await Promise.all([
        forecastApi.scenarios.update(current.id, { position: index + by }),
        forecastApi.scenarios.update(other.id, { position: index }),
      ])
    } catch (err) {
      onError(err)
    }
    invalidateForecast(queryClient)
  }

  return (
    <SectionCard>
      <SectionHeader
        title={t('forecast.scenariosTitle')}
        hint={t('forecast.scenariosHint')}
        action={
          canWrite ? (
            <Button size="sm" className="gap-1.5 h-8" onClick={() => setScenarioDialog({ open: true, scenario: null })}>
              <Plus size={13} /> <span className="hidden sm:inline">{t('forecast.addScenario')}</span>
            </Button>
          ) : undefined
        }
      />
      {ordered.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-8">{t('forecast.scenariosEmpty')}</p>
      ) : (
        <div className="divide-y divide-border">
          {ordered.map((scenario, index) => {
            const changes = (items ?? []).filter((i) => i.scenario_id === scenario.id)
            return (
              <div key={scenario.id} className="px-4 sm:px-5 py-4" data-testid="scenario-row">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <Switch
                      checked={scenario.is_enabled}
                      disabled={!canWrite}
                      onCheckedChange={(checked) => updateScenario.mutate({ id: scenario.id, is_enabled: checked })}
                      aria-labelledby={`scenario-name-${scenario.id}`}
                    />
                    <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: scenario.color }} />
                    <p id={`scenario-name-${scenario.id}`} className="text-sm font-semibold text-foreground truncate">
                      {scenario.name}
                    </p>
                  </div>
                  {canWrite && (
                    <div className="flex items-center gap-1">
                      <Button size="sm" variant="outline" className="gap-1.5 h-7 text-xs" onClick={() => setChangeDialog({ scenarioId: scenario.id, item: null })}>
                        <Plus size={12} /> {t('forecast.addChange')}
                      </Button>
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-foreground disabled:opacity-30"
                        onClick={() => move(index, -1)}
                        disabled={index === 0}
                        aria-label={t('forecast.moveUp')}
                        title={t('forecast.moveUp')}
                      >
                        <ArrowUp size={13} />
                      </button>
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-foreground disabled:opacity-30"
                        onClick={() => move(index, 1)}
                        disabled={index === ordered.length - 1}
                        aria-label={t('forecast.moveDown')}
                        title={t('forecast.moveDown')}
                      >
                        <ArrowDown size={13} />
                      </button>
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                        onClick={() => setScenarioDialog({ open: true, scenario })}
                        aria-label={t('common.edit')}
                        title={t('common.edit')}
                      >
                        <Pencil size={13} />
                      </button>
                      <button
                        className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                        onClick={() => setDeleting(scenario)}
                        aria-label={t('common.delete')}
                        title={t('common.delete')}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  )}
                </div>
                {changes.length === 0 ? (
                  <p className="text-xs text-muted-foreground mt-2 ml-11">{t('forecast.noChanges')}</p>
                ) : (
                  <ul className="mt-2 ml-11 space-y-1">
                    {changes.map((change) => (
                      <li key={change.id} className="flex items-center justify-between gap-2 text-sm">
                        <ChangeSummary
                          item={change}
                          recurring={recurringList ?? []}
                          estimates={estimates}
                          categories={lookups.categories}
                          accounts={lookups.accounts}
                          currency={currency}
                        />
                        {canWrite && (
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              className="p-1 rounded-md text-muted-foreground hover:text-primary"
                              onClick={() => setChangeDialog({ scenarioId: scenario.id, item: change })}
                              aria-label={t('common.edit')}
                              title={t('common.edit')}
                            >
                              <Pencil size={12} />
                            </button>
                            <button
                              className="p-1 rounded-md text-muted-foreground hover:text-rose-500"
                              onClick={() => deleteChange.mutate(change.id)}
                              aria-label={t('common.delete')}
                              title={t('common.delete')}
                            >
                              <Trash2 size={12} />
                            </button>
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )
          })}
        </div>
      )}

      <Dialog open={scenarioDialog.open} onOpenChange={() => setScenarioDialog({ open: false, scenario: null })}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{scenarioDialog.scenario ? t('forecast.editScenario') : t('forecast.addScenario')}</DialogTitle>
          </DialogHeader>
          <ScenarioForm
            key={scenarioDialog.scenario?.id ?? 'new'}
            scenario={scenarioDialog.scenario}
            defaultColor={SCENARIO_COLORS[ordered.length % SCENARIO_COLORS.length]}
            loading={saveScenario.isPending}
            onCancel={() => setScenarioDialog({ open: false, scenario: null })}
            onSave={(payload) => saveScenario.mutate(payload)}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={changeDialog !== null} onOpenChange={() => setChangeDialog(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{changeDialog?.item ? t('forecast.editChange') : t('forecast.addChange')}</DialogTitle>
          </DialogHeader>
          {changeDialog && (
            <ChangeForm
              key={changeDialog.item?.id ?? 'new'}
              scenarioId={changeDialog.scenarioId}
              item={changeDialog.item}
              recurring={recurringList ?? []}
              estimates={estimates}
              loans={loans}
              categories={lookups.categories}
              categoryGroups={lookups.categoryGroups}
              currency={currency}
              loading={saveChange.isPending}
              onCancel={() => setChangeDialog(null)}
              onSave={(payload) => saveChange.mutate(payload)}
            />
          )}
        </DialogContent>
      </Dialog>

      <DeleteConfirmationDialog
        open={!!deleting}
        title={t('forecast.confirmDeleteScenario')}
        description={t('forecast.confirmDeleteScenarioDescription', { name: deleting?.name })}
        isPending={deleteScenario.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && deleteScenario.mutate(deleting.id)}
      />
    </SectionCard>
  )
}

function ScenarioForm({
  scenario,
  defaultColor,
  loading,
  onCancel,
  onSave,
}: {
  scenario: ForecastScenario | null
  defaultColor: string
  loading: boolean
  onCancel: () => void
  onSave: (payload: Partial<ForecastScenario>) => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(scenario?.name ?? '')
  const [color, setColor] = useState(scenario?.color ?? defaultColor)
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        onSave({ name, color })
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="scenario-name">{t('common.name')}</Label>
        <Input id="scenario-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={t('forecast.scenarioNamePlaceholder')} required />
      </div>
      <div className="space-y-2">
        <Label>{t('forecast.color')}</Label>
        <div className="flex flex-wrap gap-2">
          {SCENARIO_COLORS.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setColor(c)}
              className={cn('w-7 h-7 rounded-full border-2', color === c ? 'border-foreground' : 'border-transparent')}
              style={{ backgroundColor: c }}
              aria-label={c}
              aria-pressed={color === c}
            />
          ))}
        </div>
      </div>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={loading}>{loading ? t('common.loading') : t('common.save')}</Button>
      </DialogFooter>
    </form>
  )
}

function ChangeSummary({
  item,
  recurring,
  estimates,
  categories,
  accounts,
  currency,
}: {
  item: ForecastItem
  recurring: RecurringTransaction[]
  estimates: SpendingEstimate[]
  categories: Category[]
  accounts: Account[]
  currency: string
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const dateLocale = useDateLocale()
  const { mask } = usePrivacyMode()
  const frequencyLabel = useFrequencyLabel()
  const fmtDate = (iso: string | null) => (iso ? new Date(iso + 'T00:00:00').toLocaleDateString(dateLocale) : '')
  const amount = Number(item.amount ?? 0)
  const money = (v: number) => mask(formatCurrency(Math.abs(v), item.currency || currency, locale))
  const signed = (v: number) => `${v >= 0 ? '+' : '−'}${money(v)}`
  const percent = Number(item.percent ?? 0)
  const range = item.end_date
    ? t('forecast.summary.between', { from: fmtDate(item.start_date), to: fmtDate(item.end_date) })
    : t('forecast.summary.from', { from: fmtDate(item.start_date) })
  const recurringName = recurring.find((r) => r.id === item.recurring_transaction_id)?.description ?? '—'
  const categoryName =
    estimates.find((e) => e.category_id === item.category_id)?.name ??
    categories.find((c) => c.id === item.category_id)?.name ?? '—'

  let text: string
  switch (item.kind) {
    case 'one_off':
      text = t('forecast.summary.oneOff', { amount: signed(item.type === 'credit' ? amount : -amount), date: fmtDate(item.date) })
      break
    case 'repeating':
      text = t('forecast.summary.repeating', {
        amount: signed(item.type === 'credit' ? amount : -amount),
        frequency: frequencyLabel(item.frequency).toLowerCase(),
        range,
      })
      break
    case 'recurring_change':
      text = item.change_mode === 'stop'
        ? t('forecast.summary.stop', { name: recurringName, from: fmtDate(item.start_date) })
        : item.change_mode === 'pause'
          ? t('forecast.summary.pause', { name: recurringName, range })
          : t('forecast.summary.newAmount', { name: recurringName, amount: money(amount), from: fmtDate(item.start_date) })
      break
    case 'income_change':
      text = t('forecast.summary.income', {
        name: item.recurring_transaction_id ? recurringName : t('forecast.allIncome'),
        change: item.change_mode === 'percent' ? `${percent >= 0 ? '+' : ''}${percent}%` : signed(amount),
        range,
      })
      break
    case 'loan_change': {
      const loan = accounts.find((a) => a.id === item.account_id)
      const name = loan ? getAccountName(loan) : '—'
      text = item.change_mode === 'rate'
        ? t(item.recalculate_repayment === false ? 'forecast.summary.loanRateKeep' : 'forecast.summary.loanRateRecalc', {
          name, rate: percent, from: fmtDate(item.start_date),
        })
        : item.change_mode === 'lump_sum'
          ? t('forecast.summary.loanLumpSum', { name, amount: money(amount), date: fmtDate(item.start_date) })
          : t('forecast.summary.loanRepayment', { name, amount: money(amount), from: fmtDate(item.start_date) })
      break
    }
    case 'spending_change':
      text = t('forecast.summary.spending', {
        name: categoryName,
        change: item.change_mode === 'percent' ? `${percent >= 0 ? '+' : ''}${percent}%` : t('forecast.perMonth', { amount: signed(amount) }),
        range,
      })
      break
    default:
      text = item.description
  }
  return (
    <span className="text-foreground min-w-0">
      {item.description ? <span className="font-medium">{item.description}: </span> : null}
      <span className="text-muted-foreground">{text}</span>
    </span>
  )
}

const KINDS: ForecastItemKind[] = ['one_off', 'repeating', 'recurring_change', 'income_change', 'spending_change', 'loan_change']

function ChangeForm({
  scenarioId,
  item,
  recurring,
  estimates,
  loans,
  categories,
  categoryGroups,
  currency,
  loading,
  onCancel,
  onSave,
}: {
  scenarioId: string
  item: ForecastItem | null
  recurring: RecurringTransaction[]
  estimates: SpendingEstimate[]
  loans: LoanForecast[]
  categories: Category[]
  categoryGroups: CategoryGroup[]
  currency: string
  loading: boolean
  onCancel: () => void
  onSave: (payload: Partial<ForecastItem>) => void
}) {
  const { t } = useTranslation()
  const locale = useDisplayLocale()
  const { mask } = usePrivacyMode()
  const today = localDateString()
  const initialAmount = item?.amount != null ? Number(item.amount) : null
  const initialPercent = item?.percent != null ? Number(item.percent) : null
  const [kind, setKind] = useState<ForecastItemKind>(item?.kind ?? 'one_off')
  const [description, setDescription] = useState(item?.description ?? '')
  const [amount, setAmount] = useState(
    initialAmount != null ? String(Math.abs(initialAmount)) : initialPercent != null ? String(Math.abs(initialPercent)) : '',
  )
  // Income and spending changes are signed; the form asks which way.
  const [direction, setDirection] = useState<'up' | 'down'>(
    (initialAmount ?? initialPercent ?? (item?.kind === 'income_change' ? 1 : -1)) >= 0 ? 'up' : 'down',
  )
  const [type, setType] = useState<'debit' | 'credit'>(item?.type ?? 'debit')
  const [date, setDate] = useState(item?.date ?? today)
  const [startDate, setStartDate] = useState(item?.start_date ?? today)
  const [endDate, setEndDate] = useState(item?.end_date ?? '')
  const [frequency, setFrequency] = useState<string>(item?.frequency ?? 'monthly')
  const activeRecurring = recurring.filter((r) => r.is_active)
  const [recurringId, setRecurringId] = useState(item?.recurring_transaction_id ?? '')
  const [changeMode, setChangeMode] = useState<string>(
    item?.change_mode ?? (kind === 'recurring_change' ? 'amount' : 'percent'),
  )
  const [categoryId, setCategoryId] = useState(item?.category_id ?? estimates[0]?.category_id ?? '')
  // Fork: loan changes pick a loan the forecast can project.
  const projectable = loans.filter((l) => !l.missing_details)
  const [loanId, setLoanId] = useState(
    (item?.kind === 'loan_change' ? item.account_id : null) ?? projectable[0]?.account_id ?? '',
  )
  const [recalculate, setRecalculate] = useState(item?.recalculate_repayment !== false)

  const isRecurringChange = kind === 'recurring_change'
  const isLoanChange = kind === 'loan_change'
  const isSigned = kind === 'income_change' || kind === 'spending_change'
  const modes = isRecurringChange ? ['amount', 'pause', 'stop'] : isLoanChange ? ['rate', 'amount', 'lump_sum'] : ['percent', 'amount']
  const loan = loans.find((l) => l.account_id === loanId)
  const mode = modes.includes(changeMode) ? changeMode : modes[0]
  const estimate = estimates.find((e) => e.category_id === categoryId)
  const signedValue = (direction === 'down' ? -1 : 1) * Number(amount || 0)
  const monthlyEffect = kind === 'spending_change' && estimate
    ? Math.max(mode === 'percent' ? (estimate.monthly_amount * signedValue) / 100 : signedValue, -estimate.monthly_amount)
    : null
  const otherCategories = categories.filter(
    (c) => !c.treat_as_transfer && !c.is_ignored && !estimates.some((e) => e.category_id === c.id),
  )

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const payload: Partial<ForecastItem> = { kind, description }
    if (!item) payload.scenario_id = scenarioId
    if (kind === 'one_off') Object.assign(payload, { amount, type, date })
    if (kind === 'repeating') Object.assign(payload, { amount, type, frequency, start_date: startDate, end_date: endDate || null })
    if (isRecurringChange) {
      Object.assign(payload, {
        recurring_transaction_id: recurringId || null,
        change_mode: mode,
        start_date: startDate,
        end_date: mode === 'pause' ? endDate || null : null,
        amount: mode === 'amount' ? amount : null,
      })
    }
    if (isLoanChange) {
      Object.assign(payload, {
        account_id: loanId || null,
        change_mode: mode,
        start_date: startDate,
        end_date: null,
        percent: mode === 'rate' ? amount : null,
        amount: mode === 'rate' ? null : amount,
        recalculate_repayment: mode === 'rate' ? recalculate : null,
      })
    }
    if (isSigned) {
      Object.assign(payload, {
        change_mode: mode,
        start_date: startDate,
        end_date: endDate || null,
        amount: mode === 'amount' ? String(signedValue) : null,
        percent: mode === 'percent' ? String(signedValue) : null,
        recurring_transaction_id: kind === 'income_change' ? recurringId || null : null,
        category_id: kind === 'spending_change' ? categoryId || null : null,
      })
    }
    onSave(payload)
  }

  return (
    <form className="space-y-4" onSubmit={submit}>
      <div className="space-y-2">
        <Label htmlFor="change-kind">{t('forecast.changeKind')}</Label>
        <select id="change-kind" className={selectClass} value={kind} disabled={!!item} onChange={(e) => setKind(e.target.value as ForecastItemKind)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>{t(`forecast.kinds.${k}`)}</option>
          ))}
        </select>
      </div>
      <div className="space-y-2">
        <Label htmlFor="change-description">{t('recurring.description')}</Label>
        <Input id="change-description" value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t('forecast.optional')} />
      </div>

      {(kind === 'one_off' || kind === 'repeating') && (
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="change-amount">{t('recurring.amount')}</Label>
            <Input id="change-amount" type="number" step="0.01" min="0.01" value={amount} onChange={(e) => setAmount(e.target.value)} required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="change-type">{t('recurring.type')}</Label>
            <select id="change-type" className={selectClass} value={type} onChange={(e) => setType(e.target.value as 'debit' | 'credit')}>
              <option value="debit">{t('forecast.moneyOut')}</option>
              <option value="credit">{t('forecast.moneyIn')}</option>
            </select>
          </div>
        </div>
      )}
      {kind === 'one_off' && (
        <div className="space-y-2">
          <Label>{t('forecast.date')}</Label>
          <DatePickerInput value={date} onChange={setDate} className="w-full justify-start" />
        </div>
      )}
      {kind === 'repeating' && (
        <div className="space-y-2">
          <Label htmlFor="change-frequency">{t('recurring.frequency')}</Label>
          <select id="change-frequency" className={selectClass} value={frequency} onChange={(e) => setFrequency(e.target.value)}>
            {['weekly', 'biweekly', 'monthly', 'quarterly', 'semiannual', 'yearly'].map((f) => (
              <option key={f} value={f}>{t(`recurring.${f}`)}</option>
            ))}
          </select>
        </div>
      )}

      {(isRecurringChange || kind === 'income_change') && (
        <div className="space-y-2">
          <Label htmlFor="change-recurring">{t('forecast.recurringPayment')}</Label>
          <select
            id="change-recurring"
            className={selectClass}
            value={recurringId}
            onChange={(e) => setRecurringId(e.target.value)}
            required={isRecurringChange}
          >
            {isRecurringChange
              ? <option value="" disabled>{t('forecast.pickRecurring')}</option>
              : <option value="">{t('forecast.allIncome')}</option>}
            {activeRecurring
              .filter((r) => isRecurringChange || r.type === 'credit')
              .map((r) => (
                <option key={r.id} value={r.id}>
                  {r.description} ({mask(formatCurrency(r.amount, r.currency, locale))})
                </option>
              ))}
          </select>
        </div>
      )}

      {kind === 'spending_change' && (
        <div className="space-y-2">
          <Label htmlFor="change-category">{t('recurring.category')}</Label>
          {estimates.length > 0 || otherCategories.length > 0 ? (
            <select id="change-category" className={selectClass} value={categoryId} onChange={(e) => setCategoryId(e.target.value)} required>
              <option value="" disabled>{t('forecast.pickCategory')}</option>
              {estimates.map((e) => (
                <option key={e.category_id} value={e.category_id}>
                  {e.name} — {t('forecast.perMonth', { amount: mask(formatCurrency(e.monthly_amount, currency, locale)) })}
                </option>
              ))}
              {otherCategories.length > 0 && (
                <optgroup label={t('forecast.noAverage')}>
                  {otherCategories.map((c) => (
                    <option key={c.id} value={c.id}>{c.name}</option>
                  ))}
                </optgroup>
              )}
            </select>
          ) : (
            <CategorySelect value={categoryId} onChange={setCategoryId} categories={categories} groups={categoryGroups} className={selectClass} />
          )}
        </div>
      )}

      {isLoanChange && (
        <div className="space-y-2">
          <Label htmlFor="change-loan">{t('forecast.loans.loan')}</Label>
          {loans.length > 0 ? (
            <select id="change-loan" className={selectClass} value={loanId} onChange={(e) => setLoanId(e.target.value)} required>
              <option value="" disabled>{t('forecast.loans.pickLoan')}</option>
              {loans.map((l) => (
                <option key={l.account_id} value={l.account_id} disabled={l.missing_details}>
                  {l.missing_details ? t('forecast.loans.needsDetails', { name: l.name }) : l.name}
                </option>
              ))}
            </select>
          ) : (
            <p className="text-xs text-muted-foreground">{t('forecast.loans.noLoans')}</p>
          )}
          {loan?.base && (
            <p className="text-xs text-muted-foreground" data-testid="loan-current">
              {t('forecast.loans.current', {
                rate: loan.rate,
                amount: mask(formatCurrency(loan.base.repayment, loan.currency, locale)),
                frequency: t(`forecast.loans.frequencies.${loan.frequency}`, { defaultValue: (loan.frequency ?? '').toLowerCase() }),
              })}
            </p>
          )}
        </div>
      )}

      {(isRecurringChange || isSigned || isLoanChange) && (
        <div className="space-y-2">
          <Label htmlFor="change-mode">{t('forecast.changeMode')}</Label>
          <select id="change-mode" className={selectClass} value={mode} onChange={(e) => setChangeMode(e.target.value)}>
            {modes.map((m) => (
              <option key={m} value={m}>
                {t(isLoanChange ? `forecast.loans.modes.${m}` : `forecast.modes.${isRecurringChange ? m : `${m}Change`}`)}
              </option>
            ))}
          </select>
        </div>
      )}
      {isLoanChange && (
        <div className="space-y-2">
          <Label htmlFor="change-loan-value">
            {mode === 'rate' ? t('forecast.loans.newRate') : mode === 'lump_sum' ? t('forecast.loans.extraAmount') : t('forecast.loans.newRepaymentAmount')}
          </Label>
          <Input
            id="change-loan-value"
            type="number"
            step="0.01"
            min={mode === 'rate' ? '0' : '0.01'}
            max={mode === 'rate' ? '100' : undefined}
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            required
          />
        </div>
      )}
      {isLoanChange && mode === 'rate' && (
        <div className="space-y-2">
          <Label>{t('forecast.loans.repaymentAfter')}</Label>
          <div className="flex items-center rounded-lg border border-border bg-card overflow-hidden w-fit" role="group">
            {[true, false].map((value) => (
              <button
                key={String(value)}
                type="button"
                onClick={() => setRecalculate(value)}
                aria-pressed={recalculate === value}
                className={`px-3 py-1.5 text-xs font-semibold transition-colors ${
                  recalculate === value
                    ? 'bg-primary text-primary-foreground'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'
                }`}
              >
                {value ? t('forecast.loans.recalculate') : t('forecast.loans.keep')}
              </button>
            ))}
          </div>
          <p className="text-xs text-muted-foreground">
            {recalculate ? t('forecast.loans.recalculateHint') : t('forecast.loans.keepHint')}
          </p>
        </div>
      )}
      {isSigned && (
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="change-direction">{t('forecast.direction')}</Label>
            <select id="change-direction" className={selectClass} value={direction} onChange={(e) => setDirection(e.target.value as 'up' | 'down')}>
              <option value="down">{t('forecast.decrease')}</option>
              <option value="up">{t('forecast.increase')}</option>
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="change-value">
              {mode === 'percent' ? t('forecast.percent') : kind === 'spending_change' ? t('forecast.amountPerMonth') : t('forecast.amountPerPayment')}
            </Label>
            <Input id="change-value" type="number" step="0.01" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} required />
          </div>
        </div>
      )}
      {monthlyEffect !== null && (
        <p className="text-xs text-muted-foreground" data-testid="spending-effect">
          {t('forecast.spendingEffect', {
            current: mask(formatCurrency(estimate!.monthly_amount, currency, locale)),
            next: mask(formatCurrency(estimate!.monthly_amount + monthlyEffect, currency, locale)),
          })}
        </p>
      )}
      {isRecurringChange && mode === 'amount' && (
        <div className="space-y-2">
          <Label htmlFor="change-new-amount">{t('forecast.newAmount')}</Label>
          <Input id="change-new-amount" type="number" step="0.01" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} required />
        </div>
      )}

      {kind !== 'one_off' && (
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label>{isLoanChange && mode === 'lump_sum' ? t('forecast.date') : t('recurring.startDate')}</Label>
            <DatePickerInput value={startDate} onChange={setStartDate} className="w-full justify-start" />
          </div>
          {((!isRecurringChange && !isLoanChange) || mode === 'pause') && (
            <div className="space-y-2">
              <Label>{t('recurring.endDate')}</Label>
              <DatePickerInput value={endDate} onChange={setEndDate} placeholder={t('forecast.optional')} className="w-full justify-start" />
            </div>
          )}
        </div>
      )}

      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={loading}>{loading ? t('common.loading') : t('common.save')}</Button>
      </DialogFooter>
    </form>
  )
}

/** Fork: the Forecast page (Analysis). Recurring has its own page in Setup. */
export default function ForecastPage() {
  return <ForecastView />
}
