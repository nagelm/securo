import {
  isMet,
  periodDaysLeft,
  periodPercentage,
  planScale,
  recentRecord,
} from '@/lib/goal-contribution'
import type { ContributionPeriod, ContributionPeriodResult, ContributionProgress } from '@/types'

type Translate = (key: string, options?: Record<string, unknown>) => string
type Money = (amount: number) => string

const STATUS_TEXT: Record<ContributionPeriodResult['status'], string> = {
  met: 'text-emerald-600 dark:text-emerald-400',
  over: 'text-emerald-600 dark:text-emerald-400',
  short: 'text-rose-600 dark:text-rose-400',
  pending: 'text-muted-foreground',
}

const PLAN_TEXT = {
  ahead: 'text-emerald-600 dark:text-emerald-400',
  behind: 'text-orange-600 dark:text-orange-400',
  on: 'text-primary',
} as const

const PLAN_FILL = {
  ahead: 'bg-emerald-500 dark:bg-emerald-400',
  behind: 'bg-orange-400',
  on: 'bg-primary',
} as const

/** Cuts the middle out of the ring, so the row's own background (and its
 * hover) shows through. */
const RING_HOLE = 'radial-gradient(farthest-side, transparent calc(100% - 7px), #000 calc(100% - 7px))'

/**
 * This period's progress as a 64px ring: indigo while under the target,
 * emerald once met, with the whole percent in the middle, or a check mark
 * once met.
 */
export function PeriodRing({ period, t }: { period: ContributionPeriodResult; t: Translate }) {
  const met = isMet(period.status)
  const share = met ? 100 : periodPercentage(Number(period.contributed), Number(period.target))
  return (
    <div
      data-testid="period-ring"
      className={`relative h-16 w-16 shrink-0 ${met ? 'text-emerald-500 dark:text-emerald-400' : 'text-primary'}`}
    >
      <div
        aria-hidden
        className="absolute inset-0 rounded-full"
        style={{
          background: `conic-gradient(currentColor 0 ${share}%, var(--muted) ${share}% 100%)`,
          WebkitMask: RING_HOLE,
          mask: RING_HOLE,
        }}
      />
      <div className="absolute inset-0 flex items-center justify-center text-[13px] font-semibold tabular-nums text-foreground">
        {met ? (
          <>
            <svg
              aria-hidden
              viewBox="0 0 24 24"
              className="h-5 w-5 text-emerald-600 dark:text-emerald-400"
              fill="none"
              stroke="currentColor"
              strokeWidth={3}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M5 12.5l4.5 4.5L19 7.5" />
            </svg>
            <span className="sr-only">{t('goals.savings.status.met')}</span>
          </>
        ) : (
          `${Math.floor(share)}%`
        )}
      </div>
    </div>
  )
}

/**
 * The last finished periods as dots, oldest left: lit emerald when the target
 * was met, dim when it fell short. Then "8 of last 10 · Streak 3".
 */
export function PeriodRecord({
  progress,
  money,
  t,
  formatDate,
}: {
  progress: ContributionProgress
  money: Money
  t: Translate
  formatDate: (iso: string) => string
}) {
  const record = recentRecord(progress)
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      {record.total > 0 && (
        <span className="flex items-center gap-[3px]" role="list" aria-label={t('goals.savings.historyLabel')}>
          {record.periods.map((p) => {
            const title = t('goals.savings.historyTitle', {
              start: formatDate(p.start),
              end: formatDate(p.end),
              contributed: money(Number(p.contributed)),
              target: money(Number(p.target)),
              status: t(`goals.savings.status.${p.status}`),
            })
            return (
              <span
                key={p.start}
                role="listitem"
                title={title}
                aria-label={title}
                data-status={p.status}
                className={`h-[7px] w-[7px] shrink-0 rounded-full ${
                  isMet(p.status)
                    ? 'bg-emerald-500 shadow-[0_0_4px_rgba(52,211,153,0.6)] dark:bg-emerald-400'
                    : 'bg-muted-foreground/30'
                }`}
              />
            )
          })}
        </span>
      )}
      <span className="tabular-nums">
        {record.total > 0 && (
          <>
            {t('goals.savings.ofLast', { met: record.met, total: record.total })}
            {' · '}
          </>
        )}
        {t('goals.savings.streak', { count: progress.streak })}
      </span>
    </div>
  )
}

/**
 * Ahead or behind the plan since money last left the account: a label on
 * top, then a track with the plan as a tick in the middle and a bar growing
 * right when ahead, left when behind (four periods' target fills a half).
 * Nothing while the account has had nothing in or out.
 */
export function PlanScale({
  progress,
  amount,
  t,
  formatDate,
  className = '',
}: {
  progress: ContributionProgress
  /** The ahead/behind amount as it should read, privacy mask applied. */
  amount: (aheadBy: number) => string
  t: Translate
  formatDate: (iso: string) => string
  className?: string
}) {
  if (progress.ahead_by == null || !progress.since) return null
  const aheadBy = Number(progress.ahead_by)
  const scale = planScale(aheadBy, Number(progress.target))
  const shown = amount(aheadBy)
  const since = formatDate(progress.since)
  const label = {
    ahead: t('goals.savings.ahead', { amount: shown }),
    behind: t('goals.savings.behind', { amount: shown }),
    on: t('goals.savings.onPlan'),
  }[scale.state]
  const accessibleName = {
    ahead: t('goals.savings.aheadSince', { amount: shown, date: since }),
    behind: t('goals.savings.behindSince', { amount: shown, date: since }),
    on: t('goals.savings.onPlanSince', { date: since }),
  }[scale.state]
  return (
    <div
      role="img"
      aria-label={accessibleName}
      title={accessibleName}
      data-state={scale.state}
      className={`flex flex-col gap-1.5 ${className}`}
    >
      <span className={`text-center text-[13px] font-semibold tabular-nums ${PLAN_TEXT[scale.state]}`}>{label}</span>
      <span className="relative block h-2 rounded-full bg-muted">
        <span
          data-testid="plan-bar"
          className={`absolute inset-y-0 rounded-full ${PLAN_FILL[scale.state]}`}
          style={{ left: `${scale.left}%`, width: `${scale.width}%` }}
        />
        <span className="absolute -inset-y-1 left-1/2 w-0.5 -translate-x-1/2 rounded-full bg-foreground" />
      </span>
      <span className="flex justify-between text-[10px] text-muted-foreground">
        <span>{t('goals.savings.scale.behind')}</span>
        <span>{t('goals.savings.scale.plan')}</span>
        <span>{t('goals.savings.scale.ahead')}</span>
      </span>
    </div>
  )
}

/**
 * A periodic goal on an account: this period's ring, then this period and
 * the record of recent ones, then how far ahead or behind the plan it is.
 * The scale wraps under the rest on a narrow screen.
 */
export function ContributionRow({
  period,
  progress,
  money,
  planAmount,
  t,
  formatDate,
  today = new Date(),
}: {
  period: ContributionPeriod
  progress: ContributionProgress
  money: Money
  planAmount: (aheadBy: number) => string
  t: Translate
  formatDate: (iso: string) => string
  today?: Date
}) {
  const { current } = progress
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
      <PeriodRing period={current} t={t} />
      <div className="flex min-w-0 flex-1 basis-40 flex-col gap-1.5 text-xs text-muted-foreground">
        <span className="inline-flex flex-wrap items-center gap-x-1.5 tabular-nums">
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
        <PeriodRecord progress={progress} money={money} t={t} formatDate={formatDate} />
      </div>
      <PlanScale
        progress={progress}
        amount={planAmount}
        t={t}
        formatDate={formatDate}
        className="w-[170px] max-w-full shrink-0"
      />
    </div>
  )
}
