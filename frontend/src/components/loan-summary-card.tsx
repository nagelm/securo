import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { format, parseISO } from 'date-fns'
import { toast } from 'sonner'
import { Info, Landmark, Pencil } from 'lucide-react'

import { AssetEquityLine } from '@/components/asset-equity-summary'
import { LoanDetailsDialog } from '@/components/loan-details-dialog'
import { Button } from '@/components/ui/button'
import { accounts as accountsApi, assets as assetsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { formatCurrency } from '@/lib/format'
import { isRevolvingLoan, loanDetailRows, revolvingLimit, type LoanDetails } from '@/lib/loan-details'
import { useDateLocale } from '@/hooks/use-display-locale'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import type { Account } from '@/types'

interface LoanSummaryCardProps {
  account: Account
  locale: string
  canWrite: boolean
  /** Privacy-mode masking from usePrivacyMode(). */
  mask?: (value: string) => string
}

const identity = (value: string) => value

/**
 * The loan block on a loan account's page: what the loan is, what secures it
 * and the equity there, a revolving loan's credit limit (limit, owed,
 * available to draw, like a card; display only), the loan's details (typed in or from the bank, with an
 * "Edit details" form), the last 12 months of paid / interest
 * / principal (read from the ledger).
 */
export function LoanSummaryCard({ account, locale, canWrite, mask = identity }: LoanSummaryCardProps) {
  const { t, i18n } = useTranslation()
  const dateLocale = useDateLocale()
  const queryClient = useQueryClient()
  const [detailsOpen, setDetailsOpen] = useState(false)

  const { data: summary } = useQuery({
    queryKey: ['accounts', account.id, 'loan-summary'],
    queryFn: () => accountsApi.loanSummary(account.id),
  })

  const { data: asset } = useQuery({
    queryKey: ['assets', account.secured_asset_id],
    queryFn: () => assetsApi.get(account.secured_asset_id!),
    enabled: !!account.secured_asset_id,
  })

  const detailsMutation = useMutation({
    mutationFn: ({ details, creditLimit }: { details: LoanDetails | null; creditLimit?: number | null }) =>
      accountsApi.update(account.id, {
        loan_details: details as Record<string, unknown> | null,
        ...(creditLimit !== undefined && { credit_limit: creditLimit }),
      }),
    onSuccess: () => {
      // Display only: no balance or report moves, so only the account refreshes.
      queryClient.invalidateQueries({ queryKey: ['accounts'] })
      setDetailsOpen(false)
      toast.success(t('loans.details.saved'))
    },
  })
  const closeDetails = () => {
    setDetailsOpen(false)
    detailsMutation.reset()
  }

  const money = (value: number, currency = account.currency) => mask(formatCurrency(value, currency, locale))
  const monthLabel = (month: string) =>
    format(parseISO(`${month}-01T00:00:00`), 'MMM yyyy', {
      locale: resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language),
    })
  const dateLabel = (date: string) => new Date(`${date}T00:00:00`).toLocaleDateString(dateLocale)

  const activeMonths = (summary?.months ?? []).filter(
    (m) => m.paid !== 0 || m.interest !== 0 || m.principal_reduction !== 0,
  )
  const details = loanDetailRows(account.loan_details, {
    t,
    money: (value) => money(value),
    date: dateLabel,
    locale,
  })
  const revolving = isRevolvingLoan(account)
  const limit = revolvingLimit(account)

  return (
    <section
      aria-label={t('loans.summary.title')}
      className="bg-card rounded-xl border border-border shadow-sm mb-6 overflow-hidden"
    >
      <div className="flex flex-col gap-3 px-5 py-4 border-b border-border sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Landmark className="h-4 w-4 text-rose-500" />
            {account.loan_kind
              ? t(`accounts.loanKinds.${account.loan_kind}`)
              : t('accounts.typeLoan')}
          </h2>
          {account.secured_asset_id && asset && (
            <div className="space-y-0.5" data-testid="loan-secured-asset">
              <p className="text-xs text-muted-foreground">
                {t('loans.summary.securedAgainst')}{' '}
                <Link to="/assets" className="text-primary hover:underline">{asset.name}</Link>
                {asset.equity != null && (
                  <>
                    {' · '}
                    {t('loans.summary.equity')}{' '}
                    <span className="font-medium text-foreground tabular-nums">
                      {money(asset.equity, asset.currency)}
                    </span>
                  </>
                )}
              </p>
              <AssetEquityLine asset={asset} locale={locale} mask={mask} />
            </div>
          )}
        </div>
      </div>

      {account.connection_id && (
        <p className="flex items-center gap-2 px-5 py-3 text-xs text-muted-foreground border-b border-border">
          <Info className="h-3.5 w-3.5 shrink-0" />
          {t('loans.summary.syncedNote')}
        </p>
      )}

      {revolving && (
        <div className="px-5 py-3 border-b border-border" data-testid="loan-credit-limit">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            {t('loans.limit.title')}
          </h3>
          {limit ? (
            <dl className="grid grid-cols-3 gap-2 text-xs" aria-label={t('loans.limit.title')}>
              <div>
                <dt className="text-muted-foreground">{t('loans.limit.limit')}</dt>
                <dd className="text-sm font-semibold tabular-nums text-foreground">{money(limit.limit)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t('loans.limit.owed')}</dt>
                <dd className="text-sm font-semibold tabular-nums text-rose-500">{money(limit.owed)}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">{t('loans.limit.available')}</dt>
                <dd className="text-sm font-semibold tabular-nums text-emerald-600">{money(limit.available)}</dd>
              </div>
            </dl>
          ) : (
            <p className="text-xs text-muted-foreground">
              {t('loans.limit.notSet')}
              {canWrite && <> {t('loans.limit.notSetHint')}</>}
            </p>
          )}
        </div>
      )}

      {(details.length > 0 || canWrite) && (
        <div className="px-5 py-3 border-b border-border">
          <div className="flex items-center justify-between gap-2 mb-1">
            <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {t('loans.summary.providerDetails')}
            </h3>
            {canWrite && (
              <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => setDetailsOpen(true)}>
                <Pencil className="h-3.5 w-3.5 mr-1" />
                {t('loans.details.edit')}
              </Button>
            )}
          </div>
          {details.length > 0 ? (
            <dl
              aria-label={t('loans.summary.providerDetails')}
              className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs"
            >
              {details.map((row) => (
                <div key={row.key} className="contents">
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd className="tabular-nums">{row.value}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-muted-foreground">{t('loans.details.empty')}</p>
          )}
        </div>
      )}

      <div className="px-5 py-4">
        <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
          {t('loans.summary.last12Months')}
        </h3>
        {activeMonths.length === 0 ? (
          <p className="text-sm text-muted-foreground">{t('loans.summary.noActivity')}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm tabular-nums">
              <thead>
                <tr className="text-xs text-muted-foreground">
                  <th className="text-left font-medium py-1">{t('loans.summary.month')}</th>
                  <th className="text-right font-medium py-1">{t('loans.summary.paid')}</th>
                  <th className="text-right font-medium py-1">{t('loans.summary.interest')}</th>
                  <th className="text-right font-medium py-1">{t('loans.summary.principal')}</th>
                </tr>
              </thead>
              <tbody>
                {activeMonths.map((m) => (
                  <tr key={m.month} className="border-t border-border">
                    <td className="py-1.5 capitalize">{monthLabel(m.month)}</td>
                    <td className="py-1.5 text-right">{money(m.paid)}</td>
                    <td className="py-1.5 text-right text-rose-500">{money(m.interest)}</td>
                    <td className="py-1.5 text-right">{money(m.principal_reduction)}</td>
                  </tr>
                ))}
              </tbody>
              {summary && (
                <tfoot>
                  <tr className="border-t-2 border-border font-semibold">
                    <td className="py-1.5">{t('loans.summary.total')}</td>
                    <td className="py-1.5 text-right">{money(summary.total_paid)}</td>
                    <td className="py-1.5 text-right text-rose-500">{money(summary.total_interest)}</td>
                    <td className="py-1.5 text-right">{money(summary.total_principal_reduction)}</td>
                  </tr>
                </tfoot>
              )}
            </table>
          </div>
        )}
      </div>

      {canWrite && (
        <LoanDetailsDialog
          open={detailsOpen}
          onClose={closeDetails}
          loan={account}
          locale={locale}
          onSave={(next, creditLimit) => detailsMutation.mutate({ details: next, creditLimit })}
          loading={detailsMutation.isPending}
          error={detailsMutation.error ? extractApiError(detailsMutation.error) : null}
        />
      )}

    </section>
  )
}
