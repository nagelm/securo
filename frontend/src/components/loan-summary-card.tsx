import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { format, parseISO } from 'date-fns'
import { toast } from 'sonner'
import { Info, Landmark, Pencil, Plus, Trash2 } from 'lucide-react'

import { AssetEquityLine } from '@/components/asset-equity-summary'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { LoanPaymentDialog } from '@/components/loan-payment-dialog'
import { Button } from '@/components/ui/button'
import {
  accounts as accountsApi,
  assets as assetsApi,
  categories as categoriesApi,
  categoryGroups as categoryGroupsApi,
  loanPayments as loanPaymentsApi,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { formatCurrency } from '@/lib/format'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { useDateLocale } from '@/hooks/use-display-locale'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import type { Account, LoanPayment, LoanPaymentCreate, LoanPaymentUpdate } from '@/types'

interface LoanSummaryCardProps {
  account: Account
  /** Every account in the workspace; the dialog picks the paying one. */
  accounts: Account[]
  locale: string
  canWrite: boolean
  /** Privacy-mode masking from usePrivacyMode(). */
  mask?: (value: string) => string
}

const identity = (value: string) => value

/** Provider facts arrive as a free-form object (Akahu `meta.loan_details`).
 *  Show every scalar, one level of nesting flattened, with its key humanised. */
function loanDetailRows(details: Record<string, unknown> | null): { key: string; label: string; value: string }[] {
  if (!details) return []
  const rows: { key: string; label: string; value: string }[] = []
  const humanise = (key: string) => {
    const words = key.replace(/[_.]+/g, ' ').trim()
    return words.charAt(0).toUpperCase() + words.slice(1)
  }
  const add = (key: string, value: unknown) => {
    if (value == null || value === '') return
    if (typeof value === 'object') return
    rows.push({ key, label: humanise(key), value: String(value) })
  }
  for (const [key, value] of Object.entries(details)) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      for (const [inner, innerValue] of Object.entries(value as Record<string, unknown>)) {
        add(`${key}.${inner}`, innerValue)
      }
    } else {
      add(key, value)
    }
  }
  return rows
}

/**
 * The loan block on a loan account's page: what the loan is, what secures it
 * and the equity there, provider facts, the last 12 months of paid / interest
 * / principal (read from the ledger, so synced loans get it too), and for
 * manual loans the recorded payments and the "Record payment" form.
 *
 * Synced loans never offer the form: sync already writes the repayment and
 * the interest, and a second copy would double count both.
 */
export function LoanSummaryCard({ account, accounts, locale, canWrite, mask = identity }: LoanSummaryCardProps) {
  const { t, i18n } = useTranslation()
  const dateLocale = useDateLocale()
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<LoanPayment | null>(null)
  const [deleting, setDeleting] = useState<LoanPayment | null>(null)

  const { data: summary } = useQuery({
    queryKey: ['accounts', account.id, 'loan-summary'],
    queryFn: () => accountsApi.loanSummary(account.id),
  })
  const manualAllowed = summary?.manual_payments_allowed ?? false

  const { data: payments = [] } = useQuery({
    queryKey: ['loan-payments', account.id],
    queryFn: () => loanPaymentsApi.list(account.id),
  })

  const { data: asset } = useQuery({
    queryKey: ['assets', account.secured_asset_id],
    queryFn: () => assetsApi.get(account.secured_asset_id!),
    enabled: !!account.secured_asset_id,
  })

  const formOpen = dialogOpen || editing != null
  const { data: categories = [] } = useQuery({
    queryKey: ['categories'],
    queryFn: () => categoriesApi.list(),
    enabled: formOpen,
  })
  const { data: groups = [] } = useQuery({
    queryKey: ['category-groups'],
    queryFn: () => categoryGroupsApi.list(),
    enabled: formOpen,
  })

  const sortedPayments = useMemo(
    () => [...payments].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : b.created_at.localeCompare(a.created_at))),
    [payments],
  )
  // The interest category last used on this loan is the form's default.
  const lastInterestCategoryId = sortedPayments.find((p) => p.interest_category_id)?.interest_category_id ?? null

  const refresh = () => {
    invalidateFinancialQueries(queryClient)
    queryClient.invalidateQueries({ queryKey: ['loan-payments'] })
    // Equity on the secured asset moves with the loan balance.
    queryClient.invalidateQueries({ queryKey: ['assets'] })
  }

  const createMutation = useMutation({
    mutationFn: (payload: LoanPaymentCreate) => loanPaymentsApi.create(payload),
    onSuccess: () => {
      refresh()
      setDialogOpen(false)
      toast.success(t('loans.payment.recorded'))
    },
  })
  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: LoanPaymentUpdate }) => loanPaymentsApi.update(id, payload),
    onSuccess: () => {
      refresh()
      setEditing(null)
      toast.success(t('loans.payment.updated'))
    },
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => loanPaymentsApi.delete(id),
    onSuccess: () => {
      refresh()
      setDeleting(null)
      toast.success(t('loans.payment.deleted'))
    },
    onError: (error) => toast.error(extractApiError(error)),
  })

  const money = (value: number, currency = account.currency) => mask(formatCurrency(value, currency, locale))
  const monthLabel = (month: string) =>
    format(parseISO(`${month}-01T00:00:00`), 'MMM yyyy', {
      locale: resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language),
    })
  const dateLabel = (date: string) => new Date(`${date}T00:00:00`).toLocaleDateString(dateLocale)

  const activeMonths = (summary?.months ?? []).filter(
    (m) => m.paid !== 0 || m.interest !== 0 || m.principal_reduction !== 0,
  )
  const details = loanDetailRows(account.loan_details)
  const closeDialog = () => {
    setDialogOpen(false)
    setEditing(null)
    createMutation.reset()
    updateMutation.reset()
  }
  const dialogError = createMutation.error
    ? extractApiError(createMutation.error)
    : updateMutation.error
      ? extractApiError(updateMutation.error)
      : null

  return (
    <section
      aria-label={t('loans.summary.title')}
      className="bg-card rounded-xl border border-border shadow-sm mb-6 overflow-hidden"
    >
      <div className="flex flex-col gap-3 px-5 py-4 border-b border-border sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Landmark className="h-4 w-4 text-rose-500" />
            {account.loan_subtype
              ? t(`accounts.loan.subtypes.${account.loan_subtype}`)
              : t('accounts.loan.type')}
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
        {manualAllowed && canWrite && (
          <Button size="sm" onClick={() => setDialogOpen(true)}>
            <Plus className="h-4 w-4 mr-1" />
            {t('loans.payment.record')}
          </Button>
        )}
      </div>

      {summary && !summary.manual_payments_allowed && account.connection_id && (
        <p className="flex items-center gap-2 px-5 py-3 text-xs text-muted-foreground border-b border-border">
          <Info className="h-3.5 w-3.5 shrink-0" />
          {t('loans.summary.syncedNote')}
        </p>
      )}

      {details.length > 0 && (
        <dl
          aria-label={t('loans.summary.providerDetails')}
          className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 px-5 py-3 text-xs border-b border-border"
        >
          {details.map((row) => (
            <div key={row.key} className="contents">
              <dt className="text-muted-foreground">{row.label}</dt>
              <dd className="tabular-nums">{row.value}</dd>
            </div>
          ))}
        </dl>
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

      {sortedPayments.length > 0 && (
        <div className="px-5 py-4 border-t border-border">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
            {t('loans.summary.payments')}
          </h3>
          <ul className="divide-y divide-border text-sm">
            {sortedPayments.map((p) => (
              <li key={p.id} className="flex items-center gap-3 py-2" data-testid="loan-payment-row">
                <span className="text-muted-foreground tabular-nums shrink-0">{dateLabel(p.date)}</span>
                <span className="truncate flex-1">{p.description}</span>
                <span className="hidden sm:inline text-xs text-muted-foreground tabular-nums shrink-0">
                  {t('loans.summary.interest')} {money(p.interest)} · {t('loans.summary.principal')} {money(p.principal)}
                </span>
                <span className="tabular-nums font-medium shrink-0">{money(p.total)}</span>
                {canWrite && manualAllowed && (
                  <span className="flex shrink-0 gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label={t('loans.payment.edit')}
                      onClick={() => setEditing(p)}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      aria-label={t('loans.payment.delete')}
                      onClick={() => setDeleting(p)}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {manualAllowed && canWrite && (
        <LoanPaymentDialog
          open={formOpen}
          onClose={closeDialog}
          loan={account}
          accounts={accounts}
          categories={categories}
          groups={groups}
          locale={locale}
          payment={editing}
          defaultInterestCategoryId={lastInterestCategoryId}
          onCreate={(payload) => createMutation.mutate(payload)}
          onUpdate={(id, payload) => updateMutation.mutate({ id, payload })}
          loading={createMutation.isPending || updateMutation.isPending}
          error={dialogError}
        />
      )}

      <DeleteConfirmationDialog
        open={deleting != null}
        title={t('loans.payment.deleteTitle')}
        description={
          deleting && !deleting.cash_leg_created
            ? t('loans.payment.deleteDescriptionPaired')
            : t('loans.payment.deleteDescription')
        }
        isPending={deleteMutation.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && deleteMutation.mutate(deleting.id)}
      />
    </section>
  )
}
