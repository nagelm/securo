import React, { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { monthLabel } from '@/lib/month-utils'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  categories as categoriesApi,
  categoryGroups as groupsApi,
  budgets as budgetsApi,
  budgetGroups as budgetGroupsApi,
  forecast as forecastApi,
  type BudgetGroupInput,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Budget, BudgetGroup, BudgetGroupVsActual, Category, CategoryGroup, ForecastBudgetChoices } from '@/types'
import { Pencil, Trash2, Plus, Repeat, CalendarIcon, Layers } from 'lucide-react'
import { format } from 'date-fns'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { MonthPicker } from '@/components/ui/monthpicker'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { findCategoryReference } from '@/lib/category-reference-utils'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'

function currentMonth() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

const TH = 'text-xs font-medium text-muted-foreground py-3'

// Fork: an end month travels as the first day of the month; the form keeps
// yyyy-MM ('' = no end).
const toMonthInput = (iso: string | null | undefined) => (iso ? iso.slice(0, 7) : '')
const fromMonthInput = (value: string) => (value ? `${value}-01` : null)

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}
function SectionHeader({ title, hint, action }: { title: string; hint?: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      {action}
    </div>
  )
}

/**
 * Fork: a month picker for an optional "until" month. The app's own month
 * grid in a popover, not a native month input: Firefox and Safari show
 * that as free text (a typed "March 2027" saved as "March 2027-01"), and
 * Chrome reports a half-edited one as empty, which saved as "no end".
 */
function MonthField({
  id,
  value,
  onChange,
  min,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  min?: string
}) {
  const { t, i18n } = useTranslation()
  const [open, setOpen] = useState(false)
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const dateFnsLocale = resolveDateFnsLocale(uiLocale)
  return (
    <div className="flex items-center gap-2">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            id={id}
            type="button"
            className={cn(
              'flex-1 inline-flex items-center gap-2 border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground hover:bg-muted/50 transition-colors cursor-pointer',
              !value && 'text-muted-foreground',
            )}
          >
            <CalendarIcon className="size-3.5 text-muted-foreground shrink-0" />
            {value ? monthLabel(value, uiLocale) : t('budgets.noEnd')}
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-auto p-0">
          <MonthPicker
            locale={dateFnsLocale}
            selectedMonth={value ? new Date(`${value}-01T00:00:00`) : undefined}
            minDate={min ? new Date(`${min}-01T00:00:00`) : undefined}
            onMonthSelect={(date) => {
              onChange(format(date, 'yyyy-MM'))
              setOpen(false)
            }}
          />
        </PopoverContent>
      </Popover>
      {value && (
        <Button type="button" variant="outline" size="sm" className="h-9" onClick={() => onChange('')}>
          {t('budgets.clearEnd')}
        </Button>
      )}
    </div>
  )
}

/** Fork: spending against a limit, drawn as the dashboard draws a category's budget. */
function SpentBar({ spent, limit, pct, label }: { spent: number; limit: number; pct: number | null; label: string }) {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const { user } = useAuth()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const used = pct ?? (limit > 0 ? (spent / limit) * 100 : 0)
  const barColor = used > 100 ? 'bg-rose-500' : used >= 80 ? 'bg-amber-400' : 'bg-emerald-500'
  const textColor = used > 100 ? 'text-rose-500' : used >= 80 ? 'text-amber-500' : 'text-muted-foreground'
  return (
    <div className="flex items-center gap-2 min-w-0">
      <div
        className="flex-1 h-1.5 bg-muted/60 rounded-full overflow-hidden"
        role="progressbar"
        aria-label={label}
        aria-valuenow={Math.round(used)}
      >
        <div className={cn('h-full rounded-full transition-all', barColor)} style={{ width: `${Math.min(used, 100)}%` }} />
      </div>
      <span className={cn('text-[11px] tabular-nums font-medium shrink-0', textColor)}>
        {mask(t('budgets.spentOf', {
          spent: formatCurrency(spent, currency, locale),
          cap: formatCurrency(limit, currency, locale),
        }))}
      </span>
    </div>
  )
}

/** Fork: one row of the Budgets or the Budget groups table; both draw alike. */
function BudgetRow({
  testId,
  icon,
  name,
  extra,
  isRecurring,
  endMonth,
  spent,
  limit,
  pct,
  inForecast,
  onForecastChange,
  forecastDisabled,
  onEdit,
  onDelete,
}: {
  testId: string
  icon: React.ReactNode
  name: string
  extra?: React.ReactNode
  isRecurring: boolean
  endMonth: string | null | undefined
  spent: number
  limit: number
  pct: number | null
  inForecast: boolean
  onForecastChange: (on: boolean) => void
  forecastDisabled: boolean
  onEdit: () => void
  onDelete: () => void
}) {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const locale = useDisplayLocale()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const currency = user?.preferences?.currency_display ?? 'USD'
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const bar = <SpentBar spent={spent} limit={limit} pct={pct} label={name} />
  return (
    <tr className="border-b border-border last:border-0 hover:bg-muted transition-colors" data-testid={testId}>
      <td className="py-3 pl-4 sm:pl-5 pr-2 text-sm font-medium text-foreground w-full max-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
          {icon}
          <span className="truncate min-w-0">{name}</span>
          {isRecurring && (
            <span title={t('budgets.recurringLabel')} className="text-muted-foreground shrink-0">
              <Repeat size={12} />
            </span>
          )}
          {endMonth && (
            <span
              className="shrink-0 text-[11px] font-medium text-muted-foreground bg-muted px-1.5 py-0.5 rounded-full"
              data-testid="budget-until"
            >
              {t('budgets.untilLabel', { month: monthLabel(toMonthInput(endMonth), uiLocale) })}
            </span>
          )}
          {extra}
        </div>
        {/* Narrow screens: the progress sits under the name. */}
        <div className="mt-1.5 sm:hidden">{bar}</div>
      </td>
      {/* A fixed-width box: the name cell takes the rest (w-full), which would squeeze the bar to nothing. */}
      <td className="py-3 pr-4 hidden sm:table-cell">
        <div className="w-56">{bar}</div>
      </td>
      <td className="py-3 pr-2 text-sm font-semibold tabular-nums text-foreground whitespace-nowrap">{mask(formatCurrency(limit, currency, locale))}</td>
      <td className="py-3 text-center">
        <Switch
          checked={inForecast}
          onCheckedChange={onForecastChange}
          disabled={!canWrite || forecastDisabled}
          aria-label={t('budgets.includeInForecastFor', { name })}
          className="align-middle"
        />
      </td>
      {canWrite && (
        <td className="py-3 pr-4 sm:pr-5">
          <div className="flex items-center justify-end gap-1">
            <button
              className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
              onClick={onEdit}
              aria-label={t('common.edit')}
              title={t('common.edit')}
            >
              <Pencil size={13} />
            </button>
            <button
              className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
              onClick={onDelete}
              aria-label={t('common.delete')}
              title={t('common.delete')}
            >
              <Trash2 size={13} />
            </button>
          </div>
        </td>
      )}
    </tr>
  )
}

function BudgetTableHead({ nameLabel, limitLabel }: { nameLabel: string; limitLabel: string }) {
  const { t } = useTranslation()
  const { canWrite } = useWorkspace()
  return (
    <thead>
      <tr className="border-b border-border">
        <th className={`${TH} pl-4 sm:pl-5 text-left`}>{nameLabel}</th>
        <th className={`${TH} text-left w-56 hidden sm:table-cell`}>{t('budgets.spent')}</th>
        <th className={`${TH} text-left w-24 sm:w-28`}>{limitLabel}</th>
        <th className={`${TH} text-center w-20 sm:w-24`} title={t('budgets.forecastHint')}>{t('budgets.inForecast')}</th>
        {canWrite && <th className={`${TH} pr-4 sm:pr-5 text-right w-20 sm:w-24`}>{t('budgets.actions')}</th>}
      </tr>
    </thead>
  )
}

/** Fork: which budgets the Forecast follows (Include in forecast). */
function useForecastChoices() {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const { data: choices } = useQuery({ queryKey: ['forecast-budget-choices'], queryFn: forecastApi.budgetChoices.get })
  const saveMutation = useMutation({
    mutationFn: (next: ForecastBudgetChoices) => forecastApi.budgetChoices.set(next),
    onSuccess: (saved) => {
      queryClient.setQueryData(['forecast-budget-choices'], saved)
      queryClient.invalidateQueries({ queryKey: ['forecast'] })
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })
  const current = { category_ids: choices?.category_ids ?? [], budget_group_ids: choices?.budget_group_ids ?? [] }
  const toggle = (list: string[], id: string, on: boolean) => (on ? [...new Set([...list, id])] : list.filter((x) => x !== id))
  return {
    pending: saveMutation.isPending || !choices,
    hasCategory: (id: string) => current.category_ids.includes(id),
    hasGroup: (id: string) => current.budget_group_ids.includes(id),
    setCategory: (id: string, on: boolean) =>
      saveMutation.mutate({ ...current, category_ids: toggle(current.category_ids, id, on) }),
    setGroup: (id: string, on: boolean) =>
      saveMutation.mutate({ ...current, budget_group_ids: toggle(current.budget_group_ids, id, on) }),
  }
}

export default function BudgetsPage() {
  const { t, i18n } = useTranslation()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const [selectedMonth, setSelectedMonth] = useState(currentMonth)
  const [monthCalOpen, setMonthCalOpen] = useState(false)
  const dateFnsLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language)
  const monthParam = `${selectedMonth}-01`
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Budget | null>(null)
  const [deletingBudget, setDeletingBudget] = useState<Budget | null>(null)
  const [newRecurring, setNewRecurring] = useState(false)
  const [endMonth, setEndMonth] = useState('')
  const forecastChoices = useForecastChoices()

  const { data: budgetsList } = useQuery({
    queryKey: ['budgets', selectedMonth],
    queryFn: () => budgetsApi.list(monthParam),
  })
  // Fork: each budget's spending this month (the dashboard's figures).
  const { data: comparison } = useQuery({
    queryKey: ['budgets', 'comparison', monthParam],
    queryFn: () => budgetsApi.comparison(monthParam),
  })
  const spendingByCategory = useMemo(
    () => new Map((comparison ?? []).map((c) => [c.category_id, c])),
    [comparison],
  )

  const { data: categoriesList } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.list,
  })

  // Budgets can point at a hidden default category. The picker below only
  // offers visible ones, but existing rows still have to name what they budget.
  const { data: allCategoriesList } = useQuery({
    queryKey: ['categories', 'management'],
    queryFn: categoriesApi.listIncludingHidden,
  })

  const { data: groupsList } = useQuery({
    queryKey: ['category-groups'],
    queryFn: groupsApi.list,
  })

  // Fork: a budget's months feed the Forecast too.
  const invalidateBudgets = () => {
    queryClient.invalidateQueries({ queryKey: ['budgets'] })
    queryClient.invalidateQueries({ queryKey: ['forecast'] })
  }
  const closeDialog = () => { setDialogOpen(false); setEditing(null) }

  const createMutation = useMutation({
    mutationFn: (data: {
      category_id: string
      amount: number
      month: string
      is_recurring?: boolean
      end_month?: string | null
    }) => budgetsApi.create(data),
    onSuccess: () => {
      invalidateBudgets()
      setDialogOpen(false)
      toast.success(t('budgets.created'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...changes }: {
      id: string
      amount: number
      end_month?: string | null
      effective_month?: string
    }) => budgetsApi.update(id, changes),
    onSuccess: () => {
      invalidateBudgets()
      closeDialog()
      toast.success(t('budgets.updated'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => budgetsApi.delete(id),
    onSuccess: () => {
      invalidateBudgets()
      setDeletingBudget(null)
      toast.success(t('budgets.deleted'))
    },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const displayCategories = allCategoriesList ?? categoriesList ?? []

  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const monthTitle = monthLabel(selectedMonth, uiLocale).replace(/^\w/, c => c.toUpperCase())

  return (
    <div>
      <PageHeader
        section={t('budgets.title')}
        title={monthTitle}
        action={
          <div className="flex items-center gap-1">
            <button
              className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all text-base"
              onClick={() => {
                const [y, m] = selectedMonth.split('-').map(Number)
                const d = new Date(y, m - 2, 1)
                setSelectedMonth(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
              }}
            >‹</button>
            <Popover open={monthCalOpen} onOpenChange={setMonthCalOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-2 border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground hover:bg-muted/50 transition-all cursor-pointer min-w-[180px]"
                >
                  <CalendarIcon className="size-3.5 text-muted-foreground" />
                  {monthTitle}
                </button>
              </PopoverTrigger>
              <PopoverContent align="center" className="w-auto p-0">
                <MonthPicker
                  locale={dateFnsLocale}
                  selectedMonth={new Date(`${selectedMonth}-01T00:00:00`)}
                  onMonthSelect={(date) => {
                    if (!date) return
                    setSelectedMonth(format(date, 'yyyy-MM'))
                    setMonthCalOpen(false)
                  }}
                />
              </PopoverContent>
            </Popover>
            <button
              className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all text-base"
              onClick={() => {
                const [y, m] = selectedMonth.split('-').map(Number)
                const d = new Date(y, m, 1)
                setSelectedMonth(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`)
              }}
            >›</button>
          </div>
        }
      />

      <SectionCard>
        <SectionHeader
          title={t('budgets.title')}
          action={
            canWrite ? (
              <Button
                size="sm"
                className="gap-1.5 h-8"
                onClick={() => { setEditing(null); setNewRecurring(false); setEndMonth(''); setDialogOpen(true) }}
              >
                <Plus size={13} /> {t('budgets.add')}
              </Button>
            ) : undefined
          }
        />
        {budgetsList && budgetsList.length > 0 ? (
          <table className="w-full">
            <BudgetTableHead nameLabel={t('budgets.category')} limitLabel={t('budgets.amount')} />
            <tbody>
              {budgetsList.map((budget) => {
                const category = findCategoryReference(displayCategories, budget.category_id)
                const spending = spendingByCategory.get(budget.category_id)
                return (
                  <BudgetRow
                    key={budget.id}
                    testId="budget-row"
                    icon={<CategoryIcon icon={category?.icon} color={category?.color} size="sm" />}
                    name={category?.name ?? budget.category_id}
                    isRecurring={budget.is_recurring}
                    endMonth={budget.end_month}
                    spent={Number(spending?.projected_amount ?? 0)}
                    limit={Number(budget.amount)}
                    pct={spending?.percentage_used ?? null}
                    inForecast={forecastChoices.hasCategory(budget.category_id)}
                    onForecastChange={(on) => forecastChoices.setCategory(budget.category_id, on)}
                    forecastDisabled={forecastChoices.pending}
                    onEdit={() => {
                      setEditing(budget)
                      setEndMonth(toMonthInput(budget.end_month))
                      setDialogOpen(true)
                    }}
                    onDelete={() => setDeletingBudget(budget)}
                  />
                )
              })}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('budgets.empty')}</p>
        )}
      </SectionCard>

      <Dialog open={dialogOpen} onOpenChange={closeDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('budgets.edit') : t('budgets.add')}</DialogTitle>
          </DialogHeader>
          <form
            key={editing?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const amount = parseFloat(formData.get('amount') as string)
              if (editing) {
                // Fork: a new amount from a later month starts in that month
                // (an effective-from copy); the months before keep theirs.
                const fromHere = editing.is_recurring && amount !== Number(editing.amount)
                  && toMonthInput(editing.month) < selectedMonth
                updateMutation.mutate({
                  id: editing.id,
                  amount,
                  ...(editing.is_recurring ? { end_month: fromMonthInput(endMonth) } : {}),
                  ...(fromHere ? { effective_month: monthParam } : {}),
                })
              } else {
                createMutation.mutate({
                  category_id: formData.get('category_id') as string,
                  amount,
                  month: monthParam,
                  is_recurring: newRecurring,
                  ...(newRecurring ? { end_month: fromMonthInput(endMonth) } : {}),
                })
              }
            }}
            className="space-y-4"
          >
            {!editing && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="budget-category">{t('budgets.category')}</Label>
                  <select
                    id="budget-category"
                    name="category_id"
                    className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                    required
                  >
                    <option value="">{t('budgets.selectCategory')}</option>
                    {groupsList?.map((group) => (
                      <optgroup key={group.id} label={group.name}>
                        {group.categories.map((cat) => (
                          <option key={cat.id} value={cat.id}>{cat.name}</option>
                        ))}
                      </optgroup>
                    ))}
                    {categoriesList?.filter((c) => !c.group_id).map((cat) => (
                      <option key={cat.id} value={cat.id}>{cat.name}</option>
                    ))}
                  </select>
                </div>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    name="is_recurring"
                    className="rounded border-border"
                    checked={newRecurring}
                    onChange={(e) => setNewRecurring(e.target.checked)}
                  />
                  <span className="text-sm text-foreground">{t('budgets.repeatEveryMonth')}</span>
                </label>
              </>
            )}
            {/* Fork: a recurring budget can stop after a month ("until"). */}
            {(editing ? editing.is_recurring : newRecurring) && (
              <div className="space-y-2">
                <Label htmlFor="budget-end-month">{t('budgets.untilOptional')}</Label>
                <MonthField
                  id="budget-end-month"
                  value={endMonth}
                  onChange={setEndMonth}
                  min={editing ? toMonthInput(editing.month) : selectedMonth}
                />
              </div>
            )}
            <div className="space-y-2">
              <Label htmlFor="budget-amount">{t('budgets.amount')}</Label>
              <Input
                id="budget-amount"
                name="amount"
                type="number"
                step="0.01"
                defaultValue={editing?.amount?.toString() ?? ''}
                required
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={closeDialog}>
                {t('common.cancel')}
              </Button>
              <Button type="submit" disabled={createMutation.isPending || updateMutation.isPending}>
                {t('common.save')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <BudgetGroupsSection
        month={monthParam}
        selectedMonth={selectedMonth}
        categories={categoriesList ?? []}
        displayCategories={displayCategories}
        groups={groupsList ?? []}
        forecastChoices={forecastChoices}
      />

      <DeleteConfirmationDialog
        open={!!deletingBudget}
        title={t('budgets.confirmDeleteTitle')}
        description={t(
          deletingBudget?.is_recurring
            ? 'budgets.confirmDeleteRecurringDescription'
            : 'budgets.confirmDeleteDescription',
          {
            name: findCategoryReference(displayCategories, deletingBudget?.category_id ?? '')?.name
              ?? t('budgets.category'),
          },
        )}
        isPending={deleteMutation.isPending}
        onClose={() => setDeletingBudget(null)}
        onConfirm={() => deletingBudget && deleteMutation.mutate(deletingBudget.id)}
      />
    </div>
  )
}

// ---------------------------------------------------------------------------
// Fork: budget groups, one cap over several categories
// ---------------------------------------------------------------------------

/** The group's member categories: a count chip that opens the list. */
function MemberChip({ ids, displayCategories }: { ids: string[]; displayCategories: Category[] }) {
  const { t } = useTranslation()
  const members = ids.map((id) => findCategoryReference(displayCategories, id))
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="shrink-0 text-[11px] font-medium text-muted-foreground bg-muted hover:bg-muted-foreground/15 px-1.5 py-0.5 rounded-full transition-colors cursor-pointer"
          data-testid="group-member-count"
        >
          {t('budgets.categoryCount', { count: ids.length })}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-60 p-2">
        <ul className="max-h-64 overflow-y-auto space-y-1" data-testid="group-members">
          {members.map((category, index) => (
            <li key={ids[index]} className="flex items-center gap-2 px-1 py-0.5 text-sm text-foreground">
              <CategoryIcon icon={category?.icon} color={category?.color} size="xs" />
              <span className="truncate">{category?.name ?? '—'}</span>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}

function BudgetGroupsSection({
  month,
  selectedMonth,
  categories,
  displayCategories,
  groups,
  forecastChoices,
}: {
  month: string
  selectedMonth: string
  categories: Category[]
  displayCategories: Category[]
  groups: CategoryGroup[]
  forecastChoices: ReturnType<typeof useForecastChoices>
}) {
  const { t } = useTranslation()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<BudgetGroupVsActual | null>(null)
  const [deleting, setDeleting] = useState<BudgetGroupVsActual | null>(null)

  const { data: comparison } = useQuery({
    queryKey: ['budget-groups', 'comparison', month],
    queryFn: () => budgetGroupsApi.comparison(month),
  })
  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['budget-groups'] })
    queryClient.invalidateQueries({ queryKey: ['forecast'] })
  }
  const close = () => { setDialogOpen(false); setEditing(null) }
  const saveMutation = useMutation({
    mutationFn: (data: Parameters<typeof budgetGroupsApi.update>[1]): Promise<BudgetGroup> =>
      editing ? budgetGroupsApi.update(editing.id, data) : budgetGroupsApi.create(data as BudgetGroupInput),
    onSuccess: () => {
      invalidate()
      toast.success(t(editing ? 'budgets.groupUpdated' : 'budgets.groupCreated'))
      close()
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })
  const deleteMutation = useMutation({
    mutationFn: (id: string) => budgetGroupsApi.delete(id),
    onSuccess: () => {
      invalidate()
      setDeleting(null)
      toast.success(t('budgets.groupDeleted'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  return (
    <div className="mt-6">
      <SectionCard>
        <SectionHeader
          title={t('budgets.groupsTitle')}
          hint={t('budgets.groupsHint')}
          action={
            canWrite ? (
              <Button size="sm" className="gap-1.5 h-8" onClick={() => { setEditing(null); setDialogOpen(true) }}>
                <Plus size={13} /> {t('budgets.addGroup')}
              </Button>
            ) : undefined
          }
        />
        {comparison && comparison.length > 0 ? (
          <table className="w-full">
            <BudgetTableHead nameLabel={t('budgets.groupName')} limitLabel={t('budgets.groupCap')} />
            <tbody>
              {comparison.map((group) => (
                <BudgetRow
                  key={group.id}
                  testId="budget-group"
                  icon={
                    <div className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-primary/10 text-primary">
                      <Layers size={14} />
                    </div>
                  }
                  name={group.name}
                  extra={<MemberChip ids={group.category_ids} displayCategories={displayCategories} />}
                  isRecurring={group.is_recurring}
                  endMonth={group.end_month}
                  spent={Number(group.projected_amount)}
                  limit={Number(group.amount)}
                  pct={group.percentage_used}
                  inForecast={forecastChoices.hasGroup(group.id)}
                  onForecastChange={(on) => forecastChoices.setGroup(group.id, on)}
                  forecastDisabled={forecastChoices.pending}
                  onEdit={() => { setEditing(group); setDialogOpen(true) }}
                  onDelete={() => setDeleting(group)}
                />
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('budgets.groupsEmpty')}</p>
        )}
      </SectionCard>

      <Dialog open={dialogOpen} onOpenChange={close}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('budgets.editGroup') : t('budgets.addGroup')}</DialogTitle>
          </DialogHeader>
          <BudgetGroupForm
            key={editing?.id ?? 'new'}
            group={editing}
            month={month}
            selectedMonth={selectedMonth}
            categories={categories}
            groups={groups}
            loading={saveMutation.isPending}
            onCancel={close}
            onSave={(data) => saveMutation.mutate(data)}
          />
        </DialogContent>
      </Dialog>

      <DeleteConfirmationDialog
        open={!!deleting}
        title={t('budgets.confirmDeleteGroupTitle')}
        description={t('budgets.confirmDeleteGroupDescription', { name: deleting?.name ?? '' })}
        isPending={deleteMutation.isPending}
        onClose={() => setDeleting(null)}
        onConfirm={() => deleting && deleteMutation.mutate(deleting.id)}
      />
    </div>
  )
}

function BudgetGroupForm({
  group,
  month,
  selectedMonth,
  categories,
  groups,
  loading,
  onCancel,
  onSave,
}: {
  group: BudgetGroupVsActual | null
  month: string
  selectedMonth: string
  categories: Category[]
  groups: CategoryGroup[]
  loading: boolean
  onCancel: () => void
  onSave: (data: Parameters<typeof budgetGroupsApi.update>[1]) => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(group?.name ?? '')
  const [amount, setAmount] = useState(group ? String(Number(group.amount)) : '')
  const [isRecurring, setIsRecurring] = useState(group?.is_recurring ?? true)
  const [endMonth, setEndMonth] = useState(toMonthInput(group?.end_month))
  const [chosen, setChosen] = useState<string[]>(group?.category_ids ?? [])
  // Fork: expense categories only: not transfers, ignored or hidden ones.
  const spending = useMemo(
    () => categories.filter((c) => !c.treat_as_transfer && !c.is_ignored && !c.is_hidden),
    [categories],
  )
  const sections = [
    ...groups.map((g) => ({ key: g.id, label: g.name, items: spending.filter((c) => c.group_id === g.id) })),
    { key: 'none', label: '', items: spending.filter((c) => !c.group_id || !groups.some((g) => g.id === c.group_id)) },
  ].filter((section) => section.items.length > 0)
  // Fork: a group made a single month while viewing a later one becomes that
  // month's, not its first month's (where it would vanish from view).
  const start = group && group.is_recurring && !isRecurring ? month : group?.month ?? month

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (chosen.length === 0) {
          toast.error(t('budgets.pickCategories'))
          return
        }
        const cap = parseFloat(amount)
        // Fork: the cap shown is this month's. Unchanged, it is not sent (it
        // would overwrite the group's first cap); new from a later month, it
        // starts in that month and the months before keep theirs (as a
        // budget's amount does).
        const capChanged = !group || cap !== Number(group.amount)
        const fromHere = group && group.is_recurring && isRecurring && capChanged
          && toMonthInput(group.month) < selectedMonth
        onSave({
          name,
          ...(capChanged ? { amount: cap } : {}),
          month: start,
          is_recurring: isRecurring,
          end_month: isRecurring ? fromMonthInput(endMonth) : null,
          category_ids: chosen,
          ...(fromHere ? { effective_month: month } : {}),
        })
      }}
    >
      <div className="space-y-2">
        <Label htmlFor="group-name">{t('budgets.groupName')}</Label>
        <Input id="group-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} required />
      </div>
      <div className="space-y-2">
        <Label htmlFor="group-cap">{t('budgets.groupCap')}</Label>
        <Input id="group-cap" type="number" step="0.01" min="0" value={amount} onChange={(e) => setAmount(e.target.value)} required />
      </div>
      <div className="space-y-2">
        <Label>{t('budgets.groupCategories')}</Label>
        <div className="max-h-48 overflow-y-auto rounded-lg border border-border p-2 space-y-2" data-testid="group-categories">
          {sections.map((section) => (
            <div key={section.key} className="space-y-1">
              {section.label && <p className="text-[11px] font-semibold text-muted-foreground">{section.label}</p>}
              {section.items.map((c) => (
                <label key={c.id} className="flex items-center gap-2 text-sm text-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    className="rounded border-border"
                    checked={chosen.includes(c.id)}
                    onChange={() =>
                      setChosen((ids) => (ids.includes(c.id) ? ids.filter((id) => id !== c.id) : [...ids, c.id]))
                    }
                  />
                  {c.name}
                </label>
              ))}
            </div>
          ))}
        </div>
      </div>
      <label className="flex items-center gap-2 cursor-pointer">
        <input
          type="checkbox"
          className="rounded border-border"
          checked={isRecurring}
          onChange={(e) => setIsRecurring(e.target.checked)}
        />
        <span className="text-sm text-foreground">{t('budgets.repeatEveryMonth')}</span>
      </label>
      {isRecurring && (
        <div className="space-y-2">
          <Label htmlFor="group-end-month">{t('budgets.untilOptional')}</Label>
          <MonthField
            id="group-end-month"
            value={endMonth}
            onChange={setEndMonth}
            min={toMonthInput(start) || selectedMonth}
          />
        </div>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel}>{t('common.cancel')}</Button>
        <Button type="submit" disabled={loading}>{t('common.save')}</Button>
      </DialogFooter>
    </form>
  )
}
