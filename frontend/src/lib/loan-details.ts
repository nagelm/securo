import { formatAmountInput, parseAmountInput } from '@/lib/format'

/**
 * A loan's details: the keys Akahu documents for `meta.loan_details`, whether
 * the bank sent them or the user typed them in (backend
 * `app/services/loan_details.py`). Display only: nothing that totals money
 * reads them.
 */
export const LOAN_PURPOSES = ['HOME', 'PERSONAL', 'BUSINESS', 'UNKNOWN'] as const
export const LOAN_TYPES = ['TABLE', 'REDUCING', 'REVOLVING', 'UNKNOWN'] as const
export const INTEREST_TYPES = ['FIXED', 'FLOATING'] as const
export const REPAYMENT_FREQUENCIES = [
  'WEEKLY',
  'FORTNIGHTLY',
  'MONTHLY',
  'QUARTERLY',
  'BIANNUALLY',
  'ANNUALLY',
] as const

export interface LoanDetails {
  purpose?: string
  type?: string
  interest?: { rate?: number; type?: string; expires_at?: string }
  is_interest_only?: boolean
  interest_only_expires_at?: string
  term?: { years?: number; months?: number } | number | string
  matures_at?: string
  initial_principal?: number
  repayment?: { frequency?: string; next_date?: string; next_amount?: number }
}

/** How a known detail is shown. */
export type LoanDetailKind = 'enum' | 'percent' | 'money' | 'date' | 'boolean' | 'term'

/** Known details, in display order: dotted path, label key, kind. */
export const LOAN_DETAIL_FIELDS: { path: string; label: string; kind: LoanDetailKind; enumKey?: string }[] = [
  { path: 'purpose', label: 'purpose', kind: 'enum', enumKey: 'purposes' },
  { path: 'type', label: 'loanType', kind: 'enum', enumKey: 'loanTypes' },
  { path: 'interest.rate', label: 'interestRate', kind: 'percent' },
  { path: 'interest.type', label: 'interestType', kind: 'enum', enumKey: 'interestTypes' },
  { path: 'interest.expires_at', label: 'rateExpiresAt', kind: 'date' },
  { path: 'is_interest_only', label: 'interestOnly', kind: 'boolean' },
  { path: 'interest_only_expires_at', label: 'interestOnlyExpiresAt', kind: 'date' },
  { path: 'initial_principal', label: 'initialPrincipal', kind: 'money' },
  { path: 'term', label: 'term', kind: 'term' },
  { path: 'matures_at', label: 'maturesAt', kind: 'date' },
  { path: 'repayment.next_amount', label: 'repaymentAmount', kind: 'money' },
  { path: 'repayment.frequency', label: 'repaymentFrequency', kind: 'enum', enumKey: 'frequencies' },
  { path: 'repayment.next_date', label: 'nextRepaymentDate', kind: 'date' },
]

const KNOWN_ENUMS: Record<string, readonly string[]> = {
  purposes: LOAN_PURPOSES,
  loanTypes: LOAN_TYPES,
  interestTypes: INTEREST_TYPES,
  frequencies: REPAYMENT_FREQUENCIES,
}

export interface LoanDetailFormatters {
  /** i18n `t`. */
  t: (key: string, options?: Record<string, unknown>) => string
  money: (value: number) => string
  /** Formats a YYYY-MM-DD date. */
  date: (value: string) => string
  locale: string
}

export interface LoanDetailRow {
  key: string
  label: string
  value: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value != null && typeof value === 'object' && !Array.isArray(value)
}

function at(details: Record<string, unknown>, path: string): unknown {
  const [head, inner] = path.split('.')
  const value = details[head]
  if (inner === undefined) return value
  return isRecord(value) ? value[inner] : undefined
}

/** The date part of an ISO date or date-time (a bank sends date-times). */
export function isoDatePart(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const match = /^(\d{4}-\d{2}-\d{2})/.exec(value.trim())
  return match ? match[1] : null
}

function humanise(key: string): string {
  const words = key.replace(/[_.]+/g, ' ').trim()
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function formatKnown(
  field: (typeof LOAN_DETAIL_FIELDS)[number],
  value: unknown,
  f: LoanDetailFormatters,
): string | null {
  if (value == null || value === '') return null
  if (typeof value === 'object' && field.kind !== 'term') return null
  switch (field.kind) {
    case 'enum': {
      const text = String(value)
      const known = field.enumKey && KNOWN_ENUMS[field.enumKey]?.includes(text.toUpperCase())
      return known ? f.t(`loans.details.${field.enumKey}.${text.toUpperCase()}`) : text
    }
    case 'percent': {
      const rate = Number(value)
      if (!Number.isFinite(rate)) return String(value)
      return new Intl.NumberFormat(f.locale, { style: 'percent', maximumFractionDigits: 4 }).format(rate / 100)
    }
    case 'money': {
      const amount = Number(value)
      return Number.isFinite(amount) ? f.money(amount) : String(value)
    }
    case 'date': {
      const day = isoDatePart(value)
      return day ? f.date(day) : String(value)
    }
    case 'boolean':
      return typeof value === 'boolean' ? f.t(value ? 'loans.details.yes' : 'loans.details.no') : String(value)
    case 'term': {
      if (!isRecord(value)) return typeof value === 'object' ? null : String(value)
      const parts: string[] = []
      const years = Number(value.years)
      const months = Number(value.months)
      if (value.years != null && Number.isFinite(years) && years > 0) {
        parts.push(f.t('loans.details.termYears', { count: years }))
      }
      if (value.months != null && Number.isFinite(months) && months > 0) {
        parts.push(f.t('loans.details.termMonths', { count: months }))
      }
      return parts.length ? parts.join(', ') : null
    }
  }
}

/**
 * The rows the loan card shows: known details with their own label and
 * formatting, in a fixed order, then anything else the bank sent, one level
 * of nesting flattened, with its key humanised.
 */
export function loanDetailRows(details: Record<string, unknown> | null, f: LoanDetailFormatters): LoanDetailRow[] {
  if (!details) return []
  const rows: LoanDetailRow[] = []
  const knownPaths = new Set(LOAN_DETAIL_FIELDS.map((field) => field.path))
  for (const field of LOAN_DETAIL_FIELDS) {
    const value = formatKnown(field, at(details, field.path), f)
    if (value != null) rows.push({ key: field.path, label: f.t(`loans.details.${field.label}`), value })
  }
  const addOther = (key: string, value: unknown) => {
    if (knownPaths.has(key) || value == null || value === '' || typeof value === 'object') return
    rows.push({ key, label: humanise(key), value: String(value) })
  }
  for (const [key, value] of Object.entries(details)) {
    if (key === 'term') continue
    if (isRecord(value)) {
      for (const [inner, innerValue] of Object.entries(value)) addOther(`${key}.${inner}`, innerValue)
    } else {
      addOther(key, value)
    }
  }
  return rows
}

/** The edit form's fields, all as typed text. */
export interface LoanDetailsForm {
  purpose: string
  type: string
  rate: string
  interestType: string
  rateExpiresAt: string
  isInterestOnly: boolean
  interestOnlyExpiresAt: string
  termYears: string
  termMonths: string
  maturesAt: string
  initialPrincipal: string
  repaymentAmount: string
  repaymentFrequency: string
  nextRepaymentDate: string
}

function knownEnum(value: unknown, allowed: readonly string[]): string {
  const text = typeof value === 'string' ? value.toUpperCase() : ''
  return allowed.includes(text) ? text : ''
}

function numberText(value: unknown, locale: string, digits: number): string {
  const n = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN
  return Number.isFinite(n) ? formatAmountInput(n, locale, digits) : ''
}

/** Prefill the form from stored details (bank values the form can't hold are left blank). */
export function loanDetailsToForm(details: Record<string, unknown> | null, locale: string): LoanDetailsForm {
  const d = details ?? {}
  const term = isRecord(d.term) ? d.term : {}
  return {
    purpose: knownEnum(d.purpose, LOAN_PURPOSES),
    type: knownEnum(d.type, LOAN_TYPES),
    rate: numberText(at(d, 'interest.rate'), locale, 4),
    interestType: knownEnum(at(d, 'interest.type'), INTEREST_TYPES),
    rateExpiresAt: isoDatePart(at(d, 'interest.expires_at')) ?? '',
    isInterestOnly: d.is_interest_only === true,
    interestOnlyExpiresAt: isoDatePart(d.interest_only_expires_at) ?? '',
    termYears: numberText(term.years, locale, 0),
    termMonths: numberText(term.months, locale, 0),
    maturesAt: isoDatePart(d.matures_at) ?? '',
    initialPrincipal: numberText(d.initial_principal, locale, 2),
    repaymentAmount: numberText(at(d, 'repayment.next_amount'), locale, 2),
    repaymentFrequency: knownEnum(at(d, 'repayment.frequency'), REPAYMENT_FREQUENCIES),
    nextRepaymentDate: isoDatePart(at(d, 'repayment.next_date')) ?? '',
  }
}

export type LoanDetailsFormError = 'rate' | 'termYears' | 'termMonths' | 'initialPrincipal' | 'repaymentAmount'

/**
 * The request body for the form, or the fields that don't parse. Blank
 * fields are left out; nothing at all is null, which clears the details.
 */
export function formToLoanDetails(
  form: LoanDetailsForm,
  locale: string,
): { details: LoanDetails | null; errors: LoanDetailsFormError[] } {
  const errors: LoanDetailsFormError[] = []
  const num = (
    field: LoanDetailsFormError,
    text: string,
    { max, whole = false }: { max: number; whole?: boolean },
  ): number | undefined => {
    if (!text.trim()) return undefined
    const value = parseAmountInput(text, locale)
    if (value == null || value < 0 || value > max || (whole && !Number.isInteger(value))) {
      errors.push(field)
      return undefined
    }
    return value
  }
  const prune = <T extends Record<string, unknown>>(obj: T): T | undefined => {
    const kept = Object.fromEntries(Object.entries(obj).filter(([, v]) => v !== undefined && v !== '')) as T
    return Object.keys(kept).length ? kept : undefined
  }

  const details = prune({
    purpose: form.purpose || undefined,
    type: form.type || undefined,
    interest: prune({
      rate: num('rate', form.rate, { max: 100 }),
      type: form.interestType || undefined,
      expires_at: form.rateExpiresAt || undefined,
    }),
    is_interest_only: form.isInterestOnly ? true : undefined,
    interest_only_expires_at: (form.isInterestOnly && form.interestOnlyExpiresAt) || undefined,
    term: prune({
      years: num('termYears', form.termYears, { max: 100, whole: true }),
      months: num('termMonths', form.termMonths, { max: 1200, whole: true }),
    }),
    matures_at: form.maturesAt || undefined,
    initial_principal: num('initialPrincipal', form.initialPrincipal, { max: 1e12 }),
    repayment: prune({
      frequency: form.repaymentFrequency || undefined,
      next_date: form.nextRepaymentDate || undefined,
      next_amount: num('repaymentAmount', form.repaymentAmount, { max: 1e12 }),
    }),
  } as Record<string, unknown>) as LoanDetails | undefined
  return { details: details ?? null, errors }
}

/**
 * Whether a loan is revolving (its loan type is REVOLVING). Only a revolving
 * loan carries a credit limit, read like a card's: limit, owed and available
 * to draw (backend `account_service.carries_credit_limit`). Display only.
 */
export function isRevolvingLoan(account: { type: string; loan_details: Record<string, unknown> | null }): boolean {
  if (account.type !== 'loan') return false
  const type = account.loan_details?.type
  return typeof type === 'string' && type.toUpperCase() === 'REVOLVING'
}

export interface RevolvingLimit {
  limit: number
  /** What is owed: the negative balance as a positive figure; 0 when in credit. */
  owed: number
  /** limit - owed, never below 0 (the server's figure when it sent one). */
  available: number
}

/** A revolving loan's limit, owed and available, or null when it has no limit. */
export function revolvingLimit(account: {
  type: string
  loan_details: Record<string, unknown> | null
  credit_limit: number | null
  available_credit: number | null
  current_balance: number
}): RevolvingLimit | null {
  if (!isRevolvingLoan(account) || account.credit_limit == null) return null
  const limit = Number(account.credit_limit)
  const balance = Number(account.current_balance)
  const owed = balance < 0 ? -balance : 0
  const available = account.available_credit != null ? Number(account.available_credit) : Math.max(limit - owed, 0)
  return { limit, owed, available }
}
