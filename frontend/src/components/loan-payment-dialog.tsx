import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { addDays, format, parseISO } from 'date-fns'
import { Info } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { CategorySelect } from '@/components/category-select'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { transactions as transactionsApi } from '@/lib/api'
import { getAccountLabel, sortAccountsByPosition } from '@/lib/account-utils'
import { localDateString } from '@/lib/date-utils'
import { formatCurrency } from '@/lib/format'
import {
  EXISTING_DEBIT_WINDOW_DAYS,
  centsToAmount,
  interestCategoriesFrom,
  isExistingDebitCandidate,
  payingAccountsFor,
  toCents,
} from '@/lib/loan-payments'
import type {
  Account,
  Category,
  CategoryGroup,
  LoanPayment,
  LoanPaymentCreate,
  LoanPaymentUpdate,
} from '@/types'

type Mode = 'new' | 'existing'

interface LoanPaymentDialogProps {
  open: boolean
  onClose: () => void
  loan: Account
  accounts: Account[]
  categories: Category[]
  groups: CategoryGroup[]
  locale: string
  /** The payment being edited; null or absent records a new one. */
  payment?: LoanPayment | null
  /** Interest category to start from (the last one used on this loan). */
  defaultInterestCategoryId?: string | null
  onCreate: (payload: LoanPaymentCreate) => void
  onUpdate: (id: string, payload: LoanPaymentUpdate) => void
  loading: boolean
  error?: string | null
}

const SELECT_CLASS =
  'w-full border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px] disabled:opacity-60'

/**
 * Record or edit one payment on a manual loan.
 *
 * The payment is a transfer of the whole amount from the paying account to the
 * loan, plus an interest debit on the loan; the backend writes those rows. The
 * principal shown here is form arithmetic for the user's benefit and is never
 * sent: nothing stores it.
 */
export function LoanPaymentDialog({
  open,
  onClose,
  loan,
  accounts,
  categories,
  groups,
  locale,
  payment,
  defaultInterestCategoryId,
  onCreate,
  onUpdate,
  loading,
  error,
}: LoanPaymentDialogProps) {
  const { t } = useTranslation()
  const editing = payment ?? null
  const bankFixed = editing != null && !editing.cash_leg_created

  const payingAccounts = useMemo(
    () => sortAccountsByPosition(payingAccountsFor(loan, accounts)),
    [loan, accounts],
  )
  const interestCategories = useMemo(() => interestCategoriesFrom(categories), [categories])

  const [mode, setMode] = useState<Mode>('new')
  const [cashAccountId, setCashAccountId] = useState('')
  const [date, setDate] = useState(localDateString)
  const [total, setTotal] = useState('')
  const [interest, setInterest] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [description, setDescription] = useState('')
  const [existingId, setExistingId] = useState('')

  // Reset the form each time it opens (or switches payment), the same way the
  // transfer dialog does: state set during render, keyed on what it depends on.
  const formKey = JSON.stringify([open, editing?.id ?? null, defaultInterestCategoryId ?? null])
  const [previousFormKey, setPreviousFormKey] = useState<string | null>(null)
  if (formKey !== previousFormKey) {
    setPreviousFormKey(formKey)
    if (open) {
      setExistingId('')
      if (editing) {
        setMode('new')
        setCashAccountId(editing.cash_account_id)
        setDate(editing.date)
        setTotal(editing.total.toFixed(2))
        setInterest(editing.interest.toFixed(2))
        setCategoryId(editing.interest_category_id ?? defaultInterestCategoryId ?? '')
        setDescription(editing.description)
      } else {
        const first = payingAccounts[0]
        setCashAccountId(first?.id ?? '')
        setMode(first?.connection_id ? 'existing' : 'new')
        setDate(localDateString())
        setTotal('')
        setInterest('')
        setCategoryId(defaultInterestCategoryId ?? '')
        setDescription('')
      }
    }
  }

  const totalCents = toCents(total)
  const interestCents = interest.trim() === '' ? 0 : toCents(interest)
  const principalCents =
    totalCents != null && interestCents != null ? totalCents - interestCents : null

  const interestTooHigh =
    totalCents != null && interestCents != null && interestCents > totalCents
  const amountsValid =
    totalCents != null && totalCents > 0 && interestCents != null && interestCents >= 0 && !interestTooHigh
  const needsCategory = interestCents != null && interestCents > 0
  const categoryMissing = needsCategory && !categoryId
  const existingMode = !editing && mode === 'existing'

  const candidateWindow = useMemo(() => {
    if (!date) return null
    const center = parseISO(date + 'T00:00:00')
    return {
      from: format(addDays(center, -EXISTING_DEBIT_WINDOW_DAYS), 'yyyy-MM-dd'),
      to: format(addDays(center, EXISTING_DEBIT_WINDOW_DAYS), 'yyyy-MM-dd'),
    }
  }, [date])

  const { data: candidateData, isFetching: candidatesLoading } = useQuery({
    queryKey: ['loan-payment-candidates', cashAccountId, candidateWindow?.from, candidateWindow?.to],
    queryFn: () =>
      transactionsApi.list({
        account_id: cashAccountId,
        type: 'debit',
        status: 'posted',
        exclude_transfers: true,
        exclude_ignored: true,
        from: candidateWindow!.from,
        to: candidateWindow!.to,
        limit: 200,
      }),
    enabled: open && existingMode && !!cashAccountId && candidateWindow != null,
  })
  const candidates = useMemo(
    () =>
      totalCents != null && totalCents > 0
        ? (candidateData?.items ?? []).filter((tx) => isExistingDebitCandidate(tx, totalCents))
        : [],
    [candidateData, totalCents],
  )
  // A picked debit stops being valid once the amount or window no longer
  // matches it.
  const pickedExisting = existingMode && candidates.some((c) => c.id === existingId) ? existingId : ''

  const canSubmit =
    amountsValid &&
    !categoryMissing &&
    !!cashAccountId &&
    (existingMode ? !!pickedExisting : !!date) &&
    !loading

  const money = (cents: number) => formatCurrency(cents / 100, loan.currency, locale)

  const handleSubmit = () => {
    if (!canSubmit || totalCents == null || interestCents == null) return
    const interestCategory = needsCategory ? categoryId : null
    const desc = description.trim()
    if (editing) {
      const payload: LoanPaymentUpdate = {
        interest_amount: centsToAmount(interestCents),
        interest_category_id: interestCategory,
      }
      if (desc) payload.description = desc
      // A payment that paired a bank row keeps the bank's date and total.
      if (!bankFixed) {
        payload.date = date
        payload.total_amount = centsToAmount(totalCents)
      }
      onUpdate(editing.id, payload)
      return
    }
    const payload: LoanPaymentCreate = {
      loan_account_id: loan.id,
      total_amount: centsToAmount(totalCents),
      interest_amount: centsToAmount(interestCents),
      interest_category_id: interestCategory,
    }
    if (desc) payload.description = desc
    if (existingMode) {
      // The debit's own date is the payment date.
      payload.existing_transaction_id = pickedExisting
    } else {
      payload.cash_account_id = cashAccountId
      payload.date = date
    }
    onCreate(payload)
  }

  const cashAccount = accounts.find((a) => a.id === cashAccountId)

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editing ? t('loans.payment.editTitle') : t('loans.payment.title')}</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            handleSubmit()
          }}
          className="space-y-4"
        >
          <div className="space-y-2">
            <Label htmlFor="loan-payment-account">{t('loans.payment.payingAccount')}</Label>
            {editing ? (
              <p id="loan-payment-account" className="text-sm">
                {cashAccount ? getAccountLabel(cashAccount) : '—'}
              </p>
            ) : (
              <select
                id="loan-payment-account"
                className={SELECT_CLASS}
                value={cashAccountId}
                onChange={(e) => {
                  setCashAccountId(e.target.value)
                  setExistingId('')
                }}
                required
              >
                <option value="" disabled>{t('transactions.account')}</option>
                {payingAccounts.map((acc) => (
                  <option key={acc.id} value={acc.id}>
                    {getAccountLabel(acc)} ({acc.currency})
                  </option>
                ))}
              </select>
            )}
            {!editing && payingAccounts.length === 0 && (
              <p className="text-xs text-muted-foreground">{t('loans.payment.noPayingAccounts')}</p>
            )}
          </div>

          {!editing && (
            <div
              role="radiogroup"
              aria-label={t('loans.payment.cashLeg')}
              className="inline-flex rounded-lg border border-border bg-muted p-0.5 text-xs font-medium"
            >
              {(['new', 'existing'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={mode === m}
                  onClick={() => {
                    setMode(m)
                    setExistingId('')
                  }}
                  className={`px-3 py-1.5 rounded-md transition-colors ${mode === m ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground'}`}
                >
                  {m === 'new' ? t('loans.payment.modeNew') : t('loans.payment.modeExisting')}
                </button>
              ))}
            </div>
          )}

          {bankFixed && (
            <div className="flex items-start gap-2 p-3 text-xs bg-muted/50 border border-border rounded-md text-muted-foreground">
              <Info size={14} className="shrink-0 mt-0.5" />
              {t('loans.payment.bankFixedHint')}
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="loan-payment-total">
                {t('loans.payment.total')}
                <span className="text-muted-foreground ml-1">({loan.currency})</span>
              </Label>
              <Input
                id="loan-payment-total"
                type="number"
                step="0.01"
                min="0.01"
                value={total}
                onChange={(e) => {
                  setTotal(e.target.value)
                  setExistingId('')
                }}
                disabled={bankFixed}
                required
              />
            </div>
            <div className="space-y-2">
              <Label>{existingMode ? t('loans.payment.approxDate') : t('transactions.date')}</Label>
              <DatePickerInput
                value={date}
                onChange={(v) => {
                  setDate(v)
                  setExistingId('')
                }}
                className="w-full justify-start"
                disabled={bankFixed}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="loan-payment-interest">{t('loans.payment.interest')}</Label>
            <Input
              id="loan-payment-interest"
              type="number"
              step="0.01"
              min="0"
              value={interest}
              onChange={(e) => setInterest(e.target.value)}
              placeholder="0.00"
              aria-invalid={interestTooHigh || undefined}
            />
            {interestTooHigh && (
              <p role="alert" className="text-xs text-destructive">{t('loans.payment.interestTooHigh')}</p>
            )}
          </div>

          <p className="text-sm tabular-nums" data-testid="loan-payment-principal">
            <span className="text-muted-foreground">{t('loans.payment.principal')}: </span>
            <span className="font-medium">
              {principalCents != null && principalCents >= 0 ? money(principalCents) : '—'}
            </span>
          </p>

          {needsCategory && (
            <div className="space-y-2">
              <Label>{t('loans.payment.interestCategory')}</Label>
              <CategorySelect
                value={categoryId}
                onChange={setCategoryId}
                categories={interestCategories}
                groups={groups}
                placeholder={t('loans.payment.interestCategoryPlaceholder')}
              />
              <p className="text-xs text-muted-foreground">{t('loans.payment.interestCategoryHint')}</p>
            </div>
          )}

          {existingMode && (
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium mb-1">{t('loans.payment.existingTitle')}</legend>
              <p className="text-xs text-muted-foreground">
                {t('loans.payment.existingHint', { days: EXISTING_DEBIT_WINDOW_DAYS })}
              </p>
              {totalCents == null || totalCents <= 0 ? null : candidatesLoading ? (
                <p className="text-xs text-muted-foreground">{t('common.loading')}</p>
              ) : candidates.length === 0 ? (
                <p className="text-xs text-muted-foreground">{t('loans.payment.existingNone')}</p>
              ) : (
                <ul className="max-h-48 overflow-y-auto rounded-md border border-border divide-y divide-border">
                  {candidates.map((tx) => (
                    <li key={tx.id}>
                      <label className="flex items-center gap-3 px-3 py-2 text-sm cursor-pointer hover:bg-muted/50">
                        <input
                          type="radio"
                          name="loan-payment-existing"
                          value={tx.id}
                          checked={pickedExisting === tx.id}
                          onChange={() => setExistingId(tx.id)}
                        />
                        <span className="tabular-nums text-muted-foreground shrink-0">{tx.date}</span>
                        <span className="truncate flex-1">{tx.description}</span>
                        <span className="tabular-nums shrink-0">
                          {formatCurrency(tx.amount, tx.currency, locale)}
                        </span>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
            </fieldset>
          )}

          <div className="space-y-2">
            <Label htmlFor="loan-payment-description">{t('loans.payment.description')}</Label>
            <Input
              id="loan-payment-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t('loans.payment.descriptionPlaceholder')}
            />
          </div>

          {error && (
            <p role="alert" className="text-sm text-destructive">{error}</p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {loading ? t('common.loading') : t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
