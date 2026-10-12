import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { formatCurrency } from '@/lib/format'
import type { Asset } from '@/types'

/**
 * Equity of an asset against the loans secured on it.
 *
 * Every figure comes from the backend (`linked_debt`, `equity`, each loan's
 * `debt`, all in the asset's currency); nothing is computed here. Equity is
 * display only: net worth already counts the loan as a liability and the
 * asset at its value, so it is never added to any total.
 */
interface AssetEquityProps {
  asset: Asset
  locale: string
  /** Privacy-mode masking from usePrivacyMode(). */
  mask?: (value: string) => string
}

const identity = (value: string) => value

function hasLinkedLoans(asset: Asset): boolean {
  return (asset.linked_loans?.length ?? 0) > 0
}

function money(value: number | null | undefined, asset: Asset, locale: string, mask: (v: string) => string) {
  return value == null ? '—' : mask(formatCurrency(value, asset.currency, locale))
}

/** One line under an asset row: Value · Debt · Equity. */
export function AssetEquityLine({ asset, locale, mask = identity }: AssetEquityProps) {
  const { t } = useTranslation()
  if (!hasLinkedLoans(asset)) return null

  return (
    <p
      className="text-[11px] text-muted-foreground tabular-nums"
      data-testid="asset-equity-line"
    >
      <span>{t('assets.equity.value')} {money(asset.current_value, asset, locale, mask)}</span>
      <span aria-hidden="true"> · </span>
      <span className="text-rose-500">{t('assets.equity.debt')} {money(asset.linked_debt, asset, locale, mask)}</span>
      <span aria-hidden="true"> · </span>
      <span className="font-medium text-foreground">{t('assets.equity.equity')} {money(asset.equity, asset, locale, mask)}</span>
    </p>
  )
}

/** Expanded-row block: the linked loans, each linking to its account page. */
export function AssetEquitySummary({ asset, locale, mask = identity }: AssetEquityProps) {
  const { t } = useTranslation()
  if (!hasLinkedLoans(asset)) return null

  return (
    <section
      aria-label={t('assets.equity.title')}
      className="rounded-lg border border-border bg-muted/20 px-3 py-2.5 text-sm"
    >
      <h4 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-1.5">
        {t('assets.equity.linkedLoans')}
      </h4>
      <ul className="space-y-1">
        {asset.linked_loans.map((loan) => (
          <li key={loan.id} className="flex items-center justify-between gap-3">
            <Link to={`/accounts/${loan.id}`} className="truncate text-primary hover:underline">
              {loan.name}
            </Link>
            <span className="tabular-nums text-rose-500 shrink-0">
              {money(loan.debt, asset, locale, mask)}
            </span>
          </li>
        ))}
      </ul>
      <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 border-t border-border pt-2 tabular-nums">
        <dt className="text-muted-foreground">{t('assets.equity.value')}</dt>
        <dd className="text-right">{money(asset.current_value, asset, locale, mask)}</dd>
        <dt className="text-muted-foreground">{t('assets.equity.debt')}</dt>
        <dd className="text-right text-rose-500">{money(asset.linked_debt, asset, locale, mask)}</dd>
        <dt className="font-medium">{t('assets.equity.equity')}</dt>
        <dd className="text-right font-semibold">{money(asset.equity, asset, locale, mask)}</dd>
      </dl>
      <p className="mt-1.5 text-[11px] text-muted-foreground">{t('assets.equity.note')}</p>
    </section>
  )
}
