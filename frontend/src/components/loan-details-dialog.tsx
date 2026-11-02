import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Info, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { formatAmountInput, parseAmountInput } from '@/lib/format'
import {
  INTEREST_TYPES,
  LOAN_PURPOSES,
  LOAN_TYPES,
  REPAYMENT_FREQUENCIES,
  formToLoanDetails,
  loanDetailsToForm,
  type LoanDetails,
  type LoanDetailsForm,
} from '@/lib/loan-details'
import type { Account } from '@/types'

interface LoanDetailsDialogProps {
  open: boolean
  onClose: () => void
  loan: Account
  locale: string
  /**
   * `creditLimit` is passed only when the details make the loan revolving
   * (null clears it); a loan that stops being revolving loses its limit on
   * the server.
   */
  onSave: (details: LoanDetails | null, creditLimit?: number | null) => void
  loading: boolean
  error?: string | null
}

const SELECT_CLASS =
  'w-full border border-border rounded-md px-3 py-2 text-sm bg-card focus:outline-none focus-visible:ring-ring/30 focus-visible:ring-[2px]'

/**
 * Edit a loan's details (rate, term, maturity, repayment) in the shape the
 * bank uses, so typed-in and bank values show the same way. Display only:
 * no balance, report or forecast reads them. On a bank-connected loan, sync
 * replaces the values the bank sends and keeps the rest.
 *
 * A revolving loan also takes a credit limit here, beside the loan type that
 * makes it revolving (the bank does not send one).
 */
export function LoanDetailsDialog({ open, onClose, loan, locale, onSave, loading, error }: LoanDetailsDialogProps) {
  const { t } = useTranslation()
  const [form, setForm] = useState<LoanDetailsForm>(() => loanDetailsToForm(loan.loan_details, locale))
  const limitText = (value: number | null) => (value != null ? formatAmountInput(Number(value), locale, 2) : '')
  const [creditLimit, setCreditLimit] = useState(() => limitText(loan.credit_limit))

  // Reset each time the dialog opens, from the loan's current details.
  const formKey = JSON.stringify([open, loan.id, loan.loan_details, loan.credit_limit])
  const [previousFormKey, setPreviousFormKey] = useState<string | null>(null)
  if (formKey !== previousFormKey) {
    setPreviousFormKey(formKey)
    if (open) {
      setForm(loanDetailsToForm(loan.loan_details, locale))
      setCreditLimit(limitText(loan.credit_limit))
    }
  }

  const set = <K extends keyof LoanDetailsForm>(key: K, value: LoanDetailsForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }))
  const { details, errors: detailErrors } = formToLoanDetails(form, locale)
  const revolving = form.type === 'REVOLVING'
  const parsedLimit = creditLimit.trim() ? parseAmountInput(creditLimit, locale) : null
  const limitInvalid = revolving && creditLimit.trim() !== '' && (parsedLimit == null || parsedLimit < 0 || parsedLimit > 1e12)
  const errors: string[] = limitInvalid ? [...detailErrors, 'creditLimit'] : detailErrors
  const invalid = new Set(errors)

  const enumSelect = (
    id: string,
    key: 'purpose' | 'type' | 'interestType' | 'repaymentFrequency',
    values: readonly string[],
    enumKey: string,
  ) => (
    <select id={id} className={SELECT_CLASS} value={form[key]} onChange={(e) => set(key, e.target.value)}>
      <option value="">{t('loans.details.notSet')}</option>
      {values.map((v) => (
        <option key={v} value={v}>
          {t(`loans.details.${enumKey}.${v}`)}
        </option>
      ))}
    </select>
  )

  const numberInput = (
    id: string,
    key: 'rate' | 'termYears' | 'termMonths' | 'initialPrincipal' | 'repaymentAmount',
    suffix?: string,
  ) => (
    <div className="relative">
      <Input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        value={form[key]}
        onChange={(e) => set(key, e.target.value)}
        aria-invalid={invalid.has(key) || undefined}
        className={suffix ? 'pr-12' : undefined}
      />
      {suffix && (
        <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-muted-foreground">
          {suffix}
        </span>
      )}
    </div>
  )

  const dateInput = (id: string, key: 'rateExpiresAt' | 'interestOnlyExpiresAt' | 'maturesAt' | 'nextRepaymentDate') => (
    <div role="group" aria-labelledby={id} className="flex items-center gap-1">
      <DatePickerInput value={form[key]} onChange={(v) => set(key, v)} className="flex-1" />
      {form[key] && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          className="h-8 w-8 shrink-0"
          aria-label={t('loans.details.clearDate')}
          onClick={() => set(key, '')}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      )}
    </div>
  )

  const field = (id: string, labelKey: string, control: ReactNode, errorKey?: string) => (
    <div className="space-y-1.5">
      <Label htmlFor={id} id={`${id}-label`}>
        {t(`loans.details.${labelKey}`)}
      </Label>
      {control}
      {errorKey && <p className="text-xs text-destructive">{t(`loans.details.errors.${errorKey}`)}</p>}
    </div>
  )
  // Date pickers are buttons; their group is labelled by the label element.
  const dateField = (id: string, labelKey: string, key: Parameters<typeof dateInput>[1]) => (
    <div className="space-y-1.5">
      <Label id={id}>{t(`loans.details.${labelKey}`)}</Label>
      {dateInput(id, key)}
    </div>
  )

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose() }}>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('loans.details.editTitle')}</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            if (errors.length > 0 || loading) return
            if (revolving) onSave(details, parsedLimit)
            else onSave(details)
          }}
          className="space-y-4"
        >
          <p className="flex gap-2 text-xs text-muted-foreground">
            <Info className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            {loan.connection_id ? t('loans.details.syncedHint') : t('loans.details.hint')}
          </p>

          <div className="grid gap-4 sm:grid-cols-2">
            {field('loan-details-purpose', 'purpose', enumSelect('loan-details-purpose', 'purpose', LOAN_PURPOSES, 'purposes'))}
            {field('loan-details-type', 'loanType', enumSelect('loan-details-type', 'type', LOAN_TYPES, 'loanTypes'))}
          </div>

          {revolving && (
            <div className="space-y-1.5">
              <Label htmlFor="loan-details-credit-limit">{t('loans.details.creditLimit')}</Label>
              <div className="relative">
                <Input
                  id="loan-details-credit-limit"
                  inputMode="decimal"
                  autoComplete="off"
                  value={creditLimit}
                  onChange={(e) => setCreditLimit(e.target.value)}
                  aria-invalid={limitInvalid || undefined}
                  aria-describedby="loan-details-credit-limit-hint"
                  className="pr-12"
                />
                <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-muted-foreground">
                  {loan.currency}
                </span>
              </div>
              {limitInvalid ? (
                <p id="loan-details-credit-limit-hint" className="text-xs text-destructive">
                  {t('loans.details.errors.creditLimit')}
                </p>
              ) : (
                <p id="loan-details-credit-limit-hint" className="text-xs text-muted-foreground">
                  {t('loans.details.creditLimitHint')}
                </p>
              )}
            </div>
          )}

          <fieldset className="space-y-3">
            <legend className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
              {t('loans.details.sectionInterest')}
            </legend>
            <div className="grid gap-4 sm:grid-cols-2">
              {field(
                'loan-details-rate',
                'interestRate',
                numberInput('loan-details-rate', 'rate', '%'),
                invalid.has('rate') ? 'rate' : undefined,
              )}
              {field(
                'loan-details-interest-type',
                'interestType',
                enumSelect('loan-details-interest-type', 'interestType', INTEREST_TYPES, 'interestTypes'),
              )}
            </div>
            {dateField('loan-details-rate-expires', 'rateExpiresAt', 'rateExpiresAt')}
            <div className="flex items-center gap-3">
              <Switch
                id="loan-details-interest-only"
                aria-labelledby="loan-details-interest-only-label"
                checked={form.isInterestOnly}
                onCheckedChange={(checked) => set('isInterestOnly', checked)}
              />
              <Label id="loan-details-interest-only-label" htmlFor="loan-details-interest-only">
                {t('loans.details.interestOnly')}
              </Label>
            </div>
            {form.isInterestOnly &&
              dateField('loan-details-interest-only-expires', 'interestOnlyExpiresAt', 'interestOnlyExpiresAt')}
          </fieldset>

          <fieldset className="space-y-3">
            <legend className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
              {t('loans.details.sectionTerm')}
            </legend>
            {field(
              'loan-details-principal',
              'initialPrincipal',
              numberInput('loan-details-principal', 'initialPrincipal', loan.currency),
              invalid.has('initialPrincipal') ? 'amount' : undefined,
            )}
            <div className="grid gap-4 grid-cols-2">
              {field(
                'loan-details-term-years',
                'termYearsLabel',
                numberInput('loan-details-term-years', 'termYears'),
                invalid.has('termYears') ? 'years' : undefined,
              )}
              {field(
                'loan-details-term-months',
                'termMonthsLabel',
                numberInput('loan-details-term-months', 'termMonths'),
                invalid.has('termMonths') ? 'months' : undefined,
              )}
            </div>
            {dateField('loan-details-matures', 'maturesAt', 'maturesAt')}
          </fieldset>

          <fieldset className="space-y-3">
            <legend className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
              {t('loans.details.sectionRepayment')}
            </legend>
            <div className="grid gap-4 sm:grid-cols-2">
              {field(
                'loan-details-repayment-amount',
                'repaymentAmount',
                numberInput('loan-details-repayment-amount', 'repaymentAmount', loan.currency),
                invalid.has('repaymentAmount') ? 'amount' : undefined,
              )}
              {field(
                'loan-details-frequency',
                'repaymentFrequency',
                enumSelect('loan-details-frequency', 'repaymentFrequency', REPAYMENT_FREQUENCIES, 'frequencies'),
              )}
            </div>
            {dateField('loan-details-next-date', 'nextRepaymentDate', 'nextRepaymentDate')}
          </fieldset>

          {error && <p className="text-sm text-destructive">{error}</p>}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={errors.length > 0 || loading}>
              {t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
