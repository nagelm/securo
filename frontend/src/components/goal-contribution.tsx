import {
  contributionTargetLabel,
  periodDaysLeft,
  periodElapsedFraction,
  periodsMet,
} from '@/lib/goal-contribution'
import type { ContributionPeriod, ContributionPeriodResult, ContributionProgress } from '@/types'

type Translate = (key: string, options?: Record<string, unknown>) => string
type Money = (amount: number) => string

const STATUS_FILL: Record<ContributionPeriodResult['status'], string> = {
  met: 'bg-emerald-500 dark:bg-emerald-400',
  over: 'bg-emerald-500 dark:bg-emerald-400',
  short: 'bg-rose-400 dark:bg-rose-500',
  pending: 'bg-blue-500 dark:bg-blue-400',
}

const STATUS_TEXT: Record<ContributionPeriodResult['status'], string> = {
  met: 'text-emerald-600 dark:text-emerald-400',
  over: 'text-emerald-600 dark:text-emerald-400',
  short: 'text-rose-600 dark:text-rose-400',
  pending: 'text-muted-foreground',
}

/**
 * The goal's periodic target, "$50 / week". With progress it adds the current
 * period and the record: "This week $30 of $50 · 4 days left" and
 * "Streak 3 · 10 of 12 met". Without progress (a goal not on an account) only
 * the target shows.
 */
export function ContributionLine({
  amount,
  period,
  progress,
  money,
  t,
  today = new Date(),
}: {
  amount: number
  period: ContributionPeriod
  progress: ContributionProgress | null
  money: Money
  t: Translate
  today?: Date
}) {
  const target = (
    <span className="font-medium text-foreground">{contributionTargetLabel(amount, period, money, t)}</span>
  )
  if (!progress) return <span className="inline-flex items-center tabular-nums">{target}</span>

  const { current } = progress
  const record = periodsMet(progress)
  return (
    <div className="flex flex-col gap-0.5 tabular-nums">
      <span className="inline-flex flex-wrap items-center gap-x-1.5">
        {target}
        <span aria-hidden>·</span>
        <span className={STATUS_TEXT[current.status]}>
          {t('goals.savings.thisPeriod', {
            label: t(`goals.savings.current.${period}`),
            contributed: money(Number(current.contributed)),
            target: money(Number(current.target)),
          })}
        </span>
        <span aria-hidden>·</span>
        <span>{t('goals.savings.daysLeft', { count: periodDaysLeft(current.end, today) })}</span>
      </span>
      <span className="inline-flex flex-wrap items-center gap-x-1.5">
        <span>{t('goals.savings.streak', { count: progress.streak })}</span>
        {record.total > 0 && (
          <>
            <span aria-hidden>·</span>
            <span>{t('goals.savings.periodsMet', { met: record.met, total: record.total })}</span>
          </>
        )}
      </span>
    </div>
  )
}

/**
 * One column per recent period, oldest left, current right. A column fills
 * to the share of its target contributed: green when met (with a darker cap
 * when exceeded), red when it fell short, blue while in progress. The current
 * period is outlined and carries a tick at the share of the period gone, so a
 * fill at or above the tick is on pace. The dashed line on top is the target.
 */
export function ContributionBars({
  progress,
  money,
  t,
  formatDate,
  today = new Date(),
  className = '',
}: {
  progress: ContributionProgress
  money: Money
  t: Translate
  formatDate: (iso: string) => string
  today?: Date
  className?: string
}) {
  return (
    <div
      className={`relative h-7 min-w-0 ${className}`}
      role="list"
      aria-label={t('goals.savings.historyLabel')}
    >
      <div className="flex h-full items-stretch gap-[3px]">
        {progress.history.map((p) => {
          const title = t('goals.savings.historyTitle', {
            start: formatDate(p.start),
            end: formatDate(p.end),
            contributed: money(Number(p.contributed)),
            target: money(Number(p.target)),
            status: t(`goals.savings.status.${p.status}`),
          })
          const contributed = Number(p.contributed)
          const target = Number(p.target)
          const share = target > 0 ? Math.max(0, Math.min(1, contributed / target)) : contributed > 0 ? 1 : 0
          const pace = p.current ? periodElapsedFraction(p.start, p.end, today) : null
          return (
            <div
              key={p.start}
              role="listitem"
              title={title}
              aria-label={title}
              data-status={p.status}
              className={`relative min-w-2 flex-1 overflow-hidden rounded-[3px] bg-muted/70 ${
                p.current ? 'ring-1 ring-foreground/50' : ''
              }`}
            >
              {share > 0 && (
                <div
                  className={`absolute inset-x-0 bottom-0 ${STATUS_FILL[p.status]}`}
                  style={{ height: `max(2px, ${share * 100}%)` }}
                />
              )}
              {p.status === 'over' && (
                <div className="absolute inset-x-0 top-0 h-[3px] bg-emerald-800 dark:bg-emerald-200" />
              )}
              {p.status === 'short' && contributed <= 0 && (
                <div className="absolute inset-x-0 bottom-0 h-[2px] bg-rose-400/60 dark:bg-rose-500/60" />
              )}
              {pace != null && (
                <div
                  data-testid="pace-tick"
                  className="absolute inset-x-0 h-[2px] translate-y-1/2 bg-foreground/80"
                  style={{ bottom: `${pace * 100}%` }}
                />
              )}
            </div>
          )
        })}
      </div>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-muted-foreground/50"
      />
    </div>
  )
}
