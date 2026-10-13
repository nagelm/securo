import { contributionTargetLabel } from '@/lib/goal-contribution'
import type { ContributionPeriod, ContributionPeriodResult, ContributionProgress } from '@/types'

type Translate = (key: string, options?: Record<string, unknown>) => string
type Money = (amount: number) => string

const STATUS_DOT: Record<ContributionPeriodResult['status'], string> = {
  met: 'bg-emerald-500',
  over: 'bg-emerald-600 ring-2 ring-emerald-500/30',
  short: 'bg-rose-400',
  pending: 'bg-muted-foreground/30',
}

const STATUS_TEXT: Record<ContributionPeriodResult['status'], string> = {
  met: 'text-emerald-600 dark:text-emerald-400',
  over: 'text-emerald-600 dark:text-emerald-400',
  short: 'text-rose-600 dark:text-rose-400',
  pending: 'text-muted-foreground',
}

/**
 * "$50 / week · This week $30 of $50 · Streak 3" for a goal with a periodic
 * target. Without progress (a goal not on an account) only the target shows.
 */
export function ContributionLine({
  amount,
  period,
  progress,
  money,
  t,
}: {
  amount: number
  period: ContributionPeriod
  progress: ContributionProgress | null
  money: Money
  t: Translate
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-1.5 tabular-nums">
      <span className="font-medium text-foreground">{contributionTargetLabel(amount, period, money, t)}</span>
      {progress && (
        <>
          <span aria-hidden>·</span>
          <span className={STATUS_TEXT[progress.current.status]}>
            {t('goals.savings.thisPeriod', {
              label: t(`goals.savings.current.${period}`),
              contributed: money(Number(progress.current.contributed)),
              target: money(Number(progress.current.target)),
            })}
          </span>
          <span aria-hidden>·</span>
          <span>{t('goals.savings.streak', { count: progress.streak })}</span>
        </>
      )}
    </span>
  )
}

/** One dot per recent period, oldest first: met, over, short or in progress. */
export function ContributionDots({
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
  return (
    <div className="flex items-center gap-1" role="list" aria-label={t('goals.savings.historyLabel')}>
      {progress.history.map((p) => {
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
            className={`inline-block h-2.5 w-2.5 rounded-full ${STATUS_DOT[p.status]} ${p.current ? 'outline outline-1 outline-offset-1 outline-foreground/40' : ''}`}
          />
        )
      })}
    </div>
  )
}
