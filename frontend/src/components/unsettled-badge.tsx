import { Clock } from 'lucide-react'
import { useTranslation } from 'react-i18next'

/** Fork: marks a bank-synced row the bank hasn't settled yet. */
export function UnsettledBadge() {
  const { t } = useTranslation()
  return (
    <span
      title={t('transactions.unsettledHint')}
      className="shrink-0 inline-flex items-center gap-1 rounded-full border border-amber-200 bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-400"
    >
      <Clock size={10} aria-hidden="true" />
      {t('transactions.unsettled')}
    </span>
  )
}
