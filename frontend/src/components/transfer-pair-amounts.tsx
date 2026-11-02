import { useTranslation } from 'react-i18next'

import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'

type PairLeg = {
  type: 'debit' | 'credit'
  amount: number | string
  currency: string
  date: string
  is_ignored?: boolean
}

/**
 * The amount slot of a transfer drawn as one row: "−X" taken from the source
 * account over "+Y" put into the destination, each in its own currency so a
 * cross-currency transfer shows what each account actually moved. Each line
 * carries its account in a tooltip and, for screen readers, as text read
 * before the amount. When the legs landed on different days, the incoming
 * line's tooltip says when it arrived.
 */
export function TransferPairAmounts({
  out,
  in: inLeg,
  fromName,
  toName,
  locale,
  dateLocale,
  className,
}: {
  out: PairLeg
  in: PairLeg
  fromName?: string
  toName?: string
  locale: string
  dateLocale: string
  className?: string
}) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()

  const outLabel = fromName ? t('transactions.transferLegOut', { account: fromName }) : ''
  const inLabelBase = toName ? t('transactions.transferLegIn', { account: toName }) : ''
  const inDate = inLeg.date !== out.date
    ? t('transactions.transferLegInDate', {
        date: new Date(inLeg.date + 'T00:00:00').toLocaleDateString(dateLocale),
      })
    : ''
  const inLabel = [inLabelBase, inDate].filter(Boolean).join(' · ')

  const line = (leg: PairLeg, sign: string, colour: string, label: string) => (
    <span
      title={label || undefined}
      className={cn('block font-bold tabular-nums', leg.is_ignored ? 'text-gray-500' : colour)}
    >
      {label && <span className="sr-only">{label}: </span>}
      {mask(`${leg.is_ignored ? ' ' : sign}${formatCurrency(Math.abs(Number(leg.amount)), leg.currency, locale)}`)}
    </span>
  )

  return (
    <div className={cn('flex flex-col items-end leading-tight', className)}>
      {line(out, '−', 'text-rose-500', outLabel)}
      {line(inLeg, '+', 'text-emerald-600', inLabel)}
    </div>
  )
}
