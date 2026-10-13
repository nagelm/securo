import { ArrowLeftRight, ArrowRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { AccountIcon } from '@/components/account-icon'
import { getAccountName } from '@/lib/account-utils'
import { cn } from '@/lib/utils'

type PillAccount = {
  type: string
  name: string
  display_name?: string | null
  institution_logo_url?: string | null
}

/**
 * A linked transfer drawn as "From [thumb] A → To [thumb] B" in the row's
 * description slot. The words "From"/"To" drop out on narrow screens and each
 * name truncates, so the pill fits a phone row. Screen readers get one
 * sentence ("Transfer from A to B") instead of the visual pieces; the tooltip
 * adds the bank's own description and why transfers stay out of totals.
 */
export function TransferAccountsPill({
  from,
  to,
  description,
  className,
}: {
  from: PillAccount
  to: PillAccount
  /** The transaction's own description, kept reachable in the tooltip. */
  description?: string
  className?: string
}) {
  const { t } = useTranslation()
  const fromName = getAccountName(from)
  const toName = getAccountName(to)
  const label = t('transactions.transferPillLabel', { from: fromName, to: toName })
  const title = [label, description, t('transactions.transferTooltip')].filter(Boolean).join('\n')

  return (
    <span
      title={title}
      className={cn(
        'inline-flex items-center gap-1 min-w-0 max-w-full text-xs font-medium text-blue-700 bg-blue-50 border border-blue-200 dark:text-blue-300 dark:bg-blue-950/40 dark:border-blue-900 rounded-full pl-1.5 pr-2 py-0.5',
        className,
      )}
    >
      <span className="sr-only">{label}</span>
      <ArrowLeftRight aria-hidden="true" className="h-3 w-3 shrink-0" />
      <span aria-hidden="true" className="hidden sm:inline shrink-0 text-[10px] font-semibold uppercase tracking-wide text-blue-500 dark:text-blue-400">
        {t('transactions.transferPillFrom')}
      </span>
      <AccountIcon account={from} size="xs" className="w-4 h-4 rounded" />
      <span aria-hidden="true" className="truncate min-w-0">{fromName}</span>
      <ArrowRight aria-hidden="true" className="h-3 w-3 shrink-0" />
      <span aria-hidden="true" className="hidden sm:inline shrink-0 text-[10px] font-semibold uppercase tracking-wide text-blue-500 dark:text-blue-400">
        {t('transactions.transferPillTo')}
      </span>
      <AccountIcon account={to} size="xs" className="w-4 h-4 rounded" />
      <span aria-hidden="true" className="truncate min-w-0">{toName}</span>
    </span>
  )
}
