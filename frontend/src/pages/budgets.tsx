import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { AlertCircle, CalendarIcon, Copy as CopyIcon, Layers, Pencil, Plus, Repeat, Sparkles, Trash2 } from 'lucide-react'
import { format } from 'date-fns'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { monthLabel, monthRange, shiftMonth } from '@/lib/month-utils'
import {
  categories as categoriesApi,
  categoryGroups as groupsApi,
  budgets as budgetsApi,
  budgetGroups as budgetGroupsApi,
  dashboard,
  forecast as forecastApi,
  type BudgetGroupInput,
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Popover, PopoverTrigger, PopoverContent } from '@/components/ui/popover'
import { MonthPicker } from '@/components/ui/monthpicker'
import { Skeleton } from '@/components/ui/skeleton'
import { PageHeader } from '@/components/page-header'
import { BudgetSpendingProgress } from '@/components/budget-spending-progress'
import { BudgetCopyDialog } from '@/components/budget-copy-dialog'
import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { CategoryIcon } from '@/components/category-icon'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useCollectionFilter } from '@/contexts/collection-filter-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { useSidebarState } from '@/contexts/sidebar-state-context'
import { cn } from '@/lib/utils'
import { useEffectiveTimezone } from '@/hooks/use-timezone'
import { resolveDateFnsLocale } from '@/lib/date-fns-locale'
import { parseBudgetDraft, planBudgetChange, type BudgetOperation } from '@/lib/budget-settings-utils'
import { todayInTimezone } from '@/lib/date-utils'
import { formatAmountInput, formatCurrency } from '@/lib/format'
import { findCategoryReference } from '@/lib/category-reference-utils'
import type { Budget, BudgetGroup, BudgetGroupVsActual, BudgetSuggestion, Category, CategoryGroup, ForecastBudgetChoices } from '@/types'

/** Months of history shown per category; suggestion amounts come from the backend average. */
const HISTORY_MONTHS = 6

const STEP_BUTTON = 'h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:border-border hover:text-foreground transition-all text-base'

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

function SectionHeader({ title, description, action }: { title: string; description?: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-foreground">{title}</p>
        {description && <p className="text-xs text-muted-foreground mt-0.5">{description}</p>}
      </div>
      {action}
    </div>
  )
}

/** Six small bars, oldest first, so a category's usual spend is visible at a glance. */
function SpendHistory({ values, labels }: { values: number[]; labels: string[] }) {
  const max = Math.max(...values, 1)
  return (
    <div className="hidden md:flex md:col-start-2 md:row-start-1 h-6 items-end gap-0.5" aria-hidden>
      {values.map((value, index) => (
        <span
          key={index}
          title={labels[index]}
          className={`w-1.5 rounded-sm ${index === values.length - 1 ? 'bg-muted-foreground/60' : 'bg-muted-foreground/25'}`}
          style={{ height: `${Math.max(8, (value / max) * 100)}%` }}
        />
      ))}
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

/**
 * Fork: the month a repeating category budget stops after, on its row. It
 * saves at once, like the forecast switch; the amount still goes through Save.
 */
function EndMonthChip({
  budget,
  categoryName,
  disabled,
  onChange,
}: {
  budget: Budget
  categoryName: string
  disabled: boolean
  onChange: (endMonth: string | null) => void
}) {
  const { t, i18n } = useTranslation()
  const [open, setOpen] = useState(false)
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const value = toMonthInput(budget.end_month)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          disabled={disabled}
          aria-label={`${t('budgets.untilOptional')}: ${categoryName}`}
          data-testid="budget-until"
          className={cn(
            'mt-0.5 inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-60',
            value ? 'bg-muted text-muted-foreground hover:bg-muted-foreground/15' : 'text-muted-foreground/70 hover:text-foreground hover:bg-muted',
          )}
        >
          <CalendarIcon className="size-3 shrink-0" />
          {value ? t('budgets.untilLabel', { month: monthLabel(value, uiLocale) }) : t('budgets.noEnd')}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-72 p-3 space-y-2">
        <Label htmlFor={`budget-end-${budget.id}`}>{t('budgets.untilOptional')}</Label>
        <MonthField
          id={`budget-end-${budget.id}`}
          value={value}
          min={toMonthInput(budget.month)}
          onChange={(next) => {
            onChange(fromMonthInput(next))
            setOpen(false)
          }}
        />
      </PopoverContent>
    </Popover>
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

/** Fork: one row of the Budget groups table. */
function BudgetGroupRow({
  group,
  displayCategories,
  inForecast,
  onForecastChange,
  forecastDisabled,
  onEdit,
  onDelete,
}: {
  group: BudgetGroupVsActual
  displayCategories: Category[]
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
  const limit = Number(group.amount)
  const bar = <SpentBar spent={Number(group.projected_amount)} limit={limit} pct={group.percentage_used} label={group.name} />
  return (
    <tr className="border-b border-border last:border-0 hover:bg-muted transition-colors" data-testid="budget-group">
      <td className="py-3 pl-4 sm:pl-5 pr-2 text-sm font-medium text-foreground w-full max-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 min-w-0">
          <div className="w-6 h-6 rounded-md flex items-center justify-center shrink-0 bg-primary/10 text-primary">
            <Layers size={14} />
          </div>
          <span className="truncate min-w-0">{group.name}</span>
          {group.is_recurring && (
            <span title={t('budgets.recurringLabel')} className="text-muted-foreground shrink-0">
              <Repeat size={12} />
            </span>
          )}
          {group.end_month && (
            <span
              className="shrink-0 text-[11px] font-medium text-muted-foreground bg-muted px-1.5 py-0.5 rounded-full"
              data-testid="budget-until"
            >
              {t('budgets.untilLabel', { month: monthLabel(toMonthInput(group.end_month), uiLocale) })}
            </span>
          )}
          <MemberChip ids={group.category_ids} displayCategories={displayCategories} />
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
          aria-label={t('budgets.includeInForecastFor', { name: group.name })}
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
  const { mask, privacyMode } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const { activeAccountIds } = useCollectionFilter()
  const noAccounts = activeAccountIds !== null && activeAccountIds.length === 0
  const { collapsed: sidebarCollapsed } = useSidebarState()
  const timeZone = useEffectiveTimezone()
  const today = todayInTimezone(timeZone)
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedMonth = searchParams.get('month')
  const validRequestedMonth = requestedMonth && /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
    ? requestedMonth
    : null
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const [monthOverride, setMonthOverride] = useState<string | null>(() => validRequestedMonth)
  const selectedMonth = monthOverride ?? today.slice(0, 7)
  const [monthCalOpen, setMonthCalOpen] = useState(false)
  const [drillDownCategoryId, setDrillDownCategoryId] = useState<string | null>(null)
  const [focusedCategoryId, setFocusedCategoryId] = useState<string | null>(null)
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [copyOpen, setCopyOpen] = useState(false)
  const [suggestMonths, setSuggestMonths] = useState(3)
  const [suggestRecurring, setSuggestRecurring] = useState(false)
  const [suggestEdits, setSuggestEdits] = useState<Record<string, { checked: boolean; amount: string }>>({})
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const dateFnsLocale = resolveDateFnsLocale(uiLocale)
  const monthParam = `${selectedMonth}-01`
  const monthTitle = monthLabel(selectedMonth, uiLocale).replace(/^\p{L}/u, (letter) => letter.toLocaleUpperCase(uiLocale))
  const selectMonth = (nextMonth: string) => {
    setMonthOverride(nextMonth)
    const next = new URLSearchParams(searchParams)
    next.set('month', nextMonth)
    setSearchParams(next)
  }
  // Drafts are per month: switching month starts from what is saved there.
  type DraftState = { month: string; drafts: Record<string, string>; recurring: Record<string, boolean> }
  const emptyDrafts = (): DraftState => ({ month: selectedMonth, drafts: {}, recurring: {} })
  const [draftState, setDraftState] = useState<DraftState>(emptyDrafts)
  const current = draftState.month === selectedMonth ? draftState : emptyDrafts()
  const drafts = current.drafts

  const fmt = (amount: number) => mask(formatCurrency(amount, userCurrency, locale))
  const forecastChoices = useForecastChoices()
  // Fork: a budget's months feed the Forecast too.
  const invalidateBudgets = () => {
    queryClient.invalidateQueries({ queryKey: ['budgets'] })
    queryClient.invalidateQueries({ queryKey: ['forecast'] })
  }
  const endMonthMutation = useMutation({
    mutationFn: ({ id, endMonth }: { id: string; endMonth: string | null }) =>
      budgetsApi.update(id, { end_month: endMonth }),
    onSuccess: () => toast.success(t('budgets.updated')),
    onError: (error: unknown) => toast.error(extractApiError(error, t('common.error'))),
    onSettled: invalidateBudgets,
  })

  const { data: budgetsList, isLoading: budgetsLoading, isFetching: budgetsFetching, isError: budgetsError, error: budgetsLoadError, refetch: refetchBudgets } = useQuery({
    queryKey: ['budgets', selectedMonth],
    queryFn: () => budgetsApi.list(monthParam),
  })
  const { data: spending, isPending: spendingPending, isError: spendingError, refetch: refetchSpending } = useQuery({
    queryKey: ['dashboard', 'spending', selectedMonth, activeAccountIds],
    queryFn: () => dashboard.spendingByCategory(monthParam, activeAccountIds ?? undefined),
    enabled: !noAccounts,
  })
  const spendingAvailable = noAccounts || (!spendingPending && !spendingError)
  const spendingByCategory = new Map(
    (noAccounts ? [] : spending ?? []).map((item) => [item.category_id, item.projected_total]),
  )
  const { data: budgetSeries } = useQuery({
    queryKey: ['budgets', 'series'],
    queryFn: () => budgetsApi.list(),
  })
  const { data: categoriesList, isLoading: categoriesLoading, isError: categoriesError, error: categoriesLoadError, refetch: refetchCategories } = useQuery({
    queryKey: ['categories'],
    queryFn: categoriesApi.listIncludingHidden,
  })
  const { data: groupsList, isLoading: groupsLoading, isError: groupsError, error: groupsLoadError, refetch: refetchGroups } = useQuery({
    queryKey: ['category-groups'],
    queryFn: groupsApi.listIncludingHidden,
  })
  const { data: suggestionData, isPending: suggestionsPending, isError: suggestionsError } = useQuery({
    queryKey: ['budgets', 'suggestions', monthParam, suggestMonths],
    queryFn: () => budgetsApi.suggestions(monthParam, suggestMonths),
  })
  const historyMonthKeys = useMemo(
    () => Array.from({ length: HISTORY_MONTHS }, (_, index) => shiftMonth(selectedMonth, -(index + 1))),
    [selectedMonth],
  )
  // Same query as the budget overview's history, so the two pages share the cache.
  const { data: history } = useQuery({
    queryKey: ['budgets', 'history', selectedMonth],
    queryFn: async () => Promise.all(historyMonthKeys.map((key) => budgetsApi.comparison(`${key}-01`))),
  })

  const budgetByCategory = useMemo(
    () => new Map((budgetsList ?? []).map((budget) => [budget.category_id, budget])),
    [budgetsList],
  )
  const savedAmount = (categoryId: string) => Number(budgetByCategory.get(categoryId)?.amount ?? 0)
  const draftFor = (categoryId: string) => drafts[categoryId] ?? (savedAmount(categoryId) > 0 ? formatAmountInput(savedAmount(categoryId), locale) : '')
  // New budgets repeat every month unless switched off, like the old add dialog's default.
  const recurringFor = (categoryId: string) => current.recurring[categoryId] ?? budgetByCategory.get(categoryId)?.is_recurring ?? true
  const parsedDraftFor = (categoryId: string) => parseBudgetDraft(draftFor(categoryId), locale)
  const planFor = (categoryId: string) => {
    const amount = parsedDraftFor(categoryId)
    if (amount === null) return []
    return planBudgetChange({
      categoryId,
      amount,
      recurring: recurringFor(categoryId),
      current: budgetByCategory.get(categoryId),
      series: (budgetSeries ?? []).filter((budget) => budget.category_id === categoryId),
      month: monthParam,
    })
  }
  // Include hidden categories/groups because existing budgets still count in reports.
  // Transfers and ignored categories never get a budget.
  const sections = useMemo(() => {
    const budgetable = (category: Category) => !category.treat_as_transfer && !category.is_ignored
    const grouped = (groupsList ?? [])
      .map((group) => ({ id: group.id, name: group.name, categories: group.categories.filter(budgetable) }))
      .filter((group) => group.categories.length > 0)
    const groupedCategoryIds = new Set(grouped.flatMap((group) => group.categories.map((category) => category.id)))
    const ungrouped = (categoriesList ?? []).filter((category) => !groupedCategoryIds.has(category.id) && budgetable(category))
    return ungrouped.length ? [...grouped, { id: 'ungrouped', name: t('budgets.otherCategories'), categories: ungrouped }] : grouped
  }, [groupsList, categoriesList, t])
  const allCategories = sections.flatMap((section) => section.categories)

  // Only plan categories the user edited in this month. Merely opening a
  // month must never turn stored zero tombstones into delete operations.
  const dirtyCategoryIds = new Set([...Object.keys(drafts), ...Object.keys(current.recurring)])
  const operations = allCategories.flatMap((category) => dirtyCategoryIds.has(category.id) ? planFor(category.id) : [])
  const changedCategories = new Set(allCategories.filter((category) => dirtyCategoryIds.has(category.id) && planFor(category.id).length > 0).map((category) => category.id))
  const invalidCategories = new Set(allCategories.filter((category) => parsedDraftFor(category.id) === null).map((category) => category.id))
  const totalBudget = allCategories.reduce((sum, category) => sum + (parsedDraftFor(category.id) ?? 0), 0)
  const rowFor = (suggestion: BudgetSuggestion) =>
    suggestEdits[suggestion.category_id] ?? { checked: !suggestion.existing_budget, amount: String(suggestion.suggested_amount) }
  const suggestionByCategory = new Map((suggestionData?.suggestions ?? []).map((suggestion) => [suggestion.category_id, suggestion]))
  const suggestable = (suggestionData?.suggestions ?? []).filter((suggestion) => !suggestion.existing_budget)
  const batchItems = suggestable.filter((suggestion) => {
    const row = rowFor(suggestion)
    return row.checked && Number(row.amount) > 0
  })

  const updateDrafts = (change: (state: DraftState) => Partial<DraftState>) =>
    setDraftState((state) => {
      const base = state.month === selectedMonth ? state : emptyDrafts()
      return { ...base, ...change(base) }
    })
  const setDraft = (categoryId: string, value: string) =>
    updateDrafts((state) => ({ drafts: { ...state.drafts, [categoryId]: value } }))
  const toggleRecurring = (categoryId: string) =>
    updateDrafts((state) => ({ recurring: { ...state.recurring, [categoryId]: !recurringFor(categoryId) } }))

  const saveMutation = useMutation({
    mutationFn: async (planned: BudgetOperation[]) => {
      // Sequential: a delete must land before the create that replaces it.
      for (const operation of planned) {
        if (operation.type === 'create') await budgetsApi.create(operation.data)
        else if (operation.type === 'update') await budgetsApi.update(operation.id, operation.data)
        else await budgetsApi.delete(operation.id)
      }
    },
    onSuccess: () => {
      setDraftState(emptyDrafts())
      toast.success(t('budgets.saved'))
    },
    onError: (error: unknown) => toast.error(extractApiError(error, t('common.error'))),
    // Even a partly failed save changed some budgets; show what is stored now.
    onSettled: invalidateBudgets,
  })

  const batchMutation = useMutation({
    mutationFn: () => budgetsApi.createBatch({
      month: monthParam,
      is_recurring: suggestRecurring,
      items: batchItems.map((suggestion) => ({
        category_id: suggestion.category_id,
        amount: Number(rowFor(suggestion).amount),
      })),
    }),
    onSuccess: (created) => {
      invalidateBudgets()
      setSuggestOpen(false)
      toast.success(t('budgets.suggestCreated', { count: created.length }))
    },
    onError: (error: unknown) => toast.error(extractApiError(error, t('common.error'))),
  })

  const historyLabels = [...historyMonthKeys].reverse().map((key) => monthLabel(key, uiLocale))

  return (
    <div className={canWrite ? 'pb-28' : undefined}>
      <PageHeader
        section={t('nav.groupSetup')}
        title={t('budgets.settingsTitle')}
        action={
          <div className="flex flex-wrap items-center gap-2">
            {canWrite && (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 h-8"
                // Without the month's budgets the dialog cannot tell which
                // categories are missing, so it would overstate the copy —
                // and a refetch still in flight is just as stale.
                disabled={budgetsLoading || budgetsFetching || budgetsError || !budgetsList}
                onClick={() => setCopyOpen(true)}
              >
                <CopyIcon size={13} /> {t('budgets.copyAction')}
              </Button>
            )}
            <div className="flex flex-wrap items-center gap-1">
              <button type="button" className={STEP_BUTTON} aria-label={t('budgets.previousMonth')} onClick={() => selectMonth(shiftMonth(selectedMonth, -1))}>‹</button>
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
                      selectMonth(format(date, 'yyyy-MM'))
                      setMonthCalOpen(false)
                    }}
                  />
                </PopoverContent>
              </Popover>
              <button type="button" className={STEP_BUTTON} aria-label={t('budgets.nextMonth')} onClick={() => selectMonth(shiftMonth(selectedMonth, 1))}>›</button>
            </div>
          </div>
        }
      />

      <SectionCard>
        <SectionHeader
          title={t('budgets.monthBudgets', { month: monthTitle })}
          description={t('budgets.spendingDescription')}
          action={canWrite ? (
            <Button variant="outline" size="sm" className="gap-1.5 h-8" onClick={() => { setSuggestEdits({}); setSuggestOpen(true) }}>
              <Sparkles size={13} /> {t('budgets.suggest')}
            </Button>
          ) : undefined}
        />

        {canWrite && !budgetsFetching && budgetsList?.length === 0 && allCategories.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-muted/20 px-4 py-3 sm:px-5">
            <div className="min-w-0">
              <p className="text-sm font-medium text-foreground">{t('budgets.empty')}</p>
              <p className="text-xs text-muted-foreground">{t('budgets.copyEmptyHint')}</p>
            </div>
            <Button variant="outline" size="sm" className="gap-1.5 h-8" onClick={() => setCopyOpen(true)}>
              <CopyIcon size={13} /> {t('budgets.copyAction')}
            </Button>
          </div>
        )}
        {spendingError && !noAccounts && (
          <div role="alert" className="flex items-center justify-between gap-3 px-4 sm:px-5 py-3 text-sm text-destructive">
            <span>{t('budgets.spendingError')}</span>
            <Button variant="outline" size="sm" onClick={() => { void refetchSpending() }}>{t('common.retry')}</Button>
          </div>
        )}
        {budgetsError || categoriesError || groupsError ? (
          <div role="alert" className="flex items-center justify-between gap-3 px-4 sm:px-5 py-4 text-sm text-rose-700 dark:text-rose-300">
            <span className="flex items-center gap-2"><AlertCircle size={16} />{extractApiError(budgetsLoadError ?? categoriesLoadError ?? groupsLoadError, t('common.error'))}</span>
            <Button variant="outline" size="sm" onClick={() => { void Promise.all([refetchBudgets(), refetchCategories(), refetchGroups()]) }}>{t('common.retry')}</Button>
          </div>
        ) : budgetsLoading || categoriesLoading || groupsLoading || !budgetsList || !categoriesList || !groupsList ? (
          <div className="p-5 space-y-3">{Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-10 w-full" />)}</div>
        ) : allCategories.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-10">{t('budgets.noCategories')}</p>
        ) : (
          sections.map((section) => (
            <div key={section.id}>
              <p className="bg-muted/30 px-4 sm:px-5 py-2 text-xs font-medium text-muted-foreground border-b border-border">{section.name}</p>
              <div className="divide-y divide-border border-b border-border">
                {section.categories.map((category) => {
                  const draft = draftFor(category.id)
                  const suggestion = suggestionByCategory.get(category.id)
                  const values = history ? [...history].reverse().map((rows) => rows.find((row) => row.category_id === category.id)?.actual_amount ?? 0) : []
                  const changed = changedCategories.has(category.id)
                  const recurring = recurringFor(category.id)
                  // Fork: the saved budget this month uses; end month and forecast act on it.
                  const saved = budgetByCategory.get(category.id)
                  const hasSaved = saved !== undefined && Number(saved.amount) > 0
                  const spent = spendingByCategory.get(category.id) ?? 0
                  // Preview the valid draft limit, just like the live total below.
                  // No edit is persisted until the user presses Save.
                  const limit = parsedDraftFor(category.id)
                  const percentage = limit !== null && limit > 0 ? Math.round(spent / limit * 1000) / 10 : 0
                  return (
                    <div key={category.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] md:grid-cols-[minmax(0,1fr)_auto_minmax(8rem,12rem)_auto_8rem] items-center gap-x-2 gap-y-2 md:gap-x-3 px-4 sm:px-5 py-2.5">
                      <div className="col-start-1 row-start-1 flex min-w-0 items-center gap-3">
                        <CategoryIcon icon={category.icon} color={category.color} size="md" />
                        <div className="min-w-0 flex-1">
                          <button
                            type="button"
                            onClick={(event) => {
                              event.currentTarget.focus()
                              setDrillDownCategoryId(category.id)
                            }}
                            aria-label={t('dashboard.drillDownCategory', { category: category.name, month: monthLabel(selectedMonth, uiLocale) })}
                            className="max-w-full break-words text-start text-sm font-medium text-foreground hover:text-primary rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                          >
                            {category.name}
                          </button>
                          {category.is_hidden && <span className="block text-[11px] text-muted-foreground">{t('budgets.hiddenCategory')}</span>}
                          {saved && hasSaved && saved.is_recurring && (
                            <EndMonthChip
                              budget={saved}
                              categoryName={category.name}
                              disabled={!canWrite || changed || endMonthMutation.isPending}
                              onChange={(endMonth) => endMonthMutation.mutate({ id: saved.id, endMonth })}
                            />
                          )}
                          {suggestion && <p className="text-[11px] text-muted-foreground tabular-nums">{t('budgets.average', { amount: fmt(Number(suggestion.suggested_amount)) })}</p>}
                          {invalidCategories.has(category.id) && <p id={`budget-error-${category.id}`} role="alert" className="text-[11px] text-destructive">{t('budgets.invalidAmount')}</p>}
                        </div>
                      </div>
                      {values.length > 0 && (
                        <SpendHistory values={values} labels={values.map((value, index) => `${historyLabels[index]}: ${fmt(value)}`)} />
                      )}
                      <div className="col-span-3 row-start-2 min-w-0 ps-11 md:col-span-1 md:col-start-3 md:row-start-1 md:ps-0">
                        <p className="mb-1 text-xs text-muted-foreground">
                          {t('budgets.spending')}{' '}
                          <span className="text-sm font-normal tabular-nums text-foreground break-words">{spendingAvailable ? fmt(spent) : '—'}</span>
                        </p>
                        {spendingAvailable && limit !== null && limit > 0 && (
                          <BudgetSpendingProgress categoryName={category.name} percentage={percentage} budgetAmount={limit} currency={userCurrency} showBudgetLabel={false} />
                        )}
                      </div>
                      <div className="col-start-2 row-start-1 md:col-start-4 flex items-center gap-1.5">
                        <button
                          type="button"
                          aria-pressed={recurring}
                          aria-label={t('budgets.repeatEveryMonthFor', { category: category.name })}
                          title={t('budgets.repeatEveryMonth')}
                          disabled={!canWrite}
                          onClick={() => toggleRecurring(category.id)}
                          className={`p-1.5 rounded-md transition-colors disabled:opacity-60 ${recurring ? 'text-primary bg-primary/10 hover:bg-primary/15' : 'text-muted-foreground/60 hover:text-foreground hover:bg-muted'}`}
                        >
                          <Repeat size={13} />
                        </button>
                        {/* Fork: Include in forecast, for a saved budget. */}
                        {hasSaved && (
                          <span title={t('budgets.forecastHint')} className="inline-flex">
                            <Switch
                              checked={forecastChoices.hasCategory(category.id)}
                              onCheckedChange={(on) => forecastChoices.setCategory(category.id, on)}
                              disabled={!canWrite || forecastChoices.pending}
                              aria-label={t('budgets.includeInForecastFor', { name: category.name })}
                            />
                          </span>
                        )}
                      </div>
                      <div className={`col-start-3 row-start-1 md:col-start-5 flex w-24 md:w-32 min-w-0 items-center rounded-lg border bg-card px-2.5 focus-within:ring-2 focus-within:ring-primary ${invalidCategories.has(category.id) ? 'border-destructive' : changed ? 'border-primary/60' : 'border-border'}`}>
                        <span className="text-xs text-muted-foreground">{formatCurrency(0, userCurrency, locale).replace(/[\d.,\s]/g, '')}</span>
                        <input
                          type="text"
                          inputMode="decimal"
                          aria-label={t('budgets.budgetFor', { category: category.name })}
                          aria-invalid={invalidCategories.has(category.id)}
                          aria-describedby={invalidCategories.has(category.id) ? `budget-error-${category.id}` : undefined}
                          placeholder="0"
                          value={privacyMode && focusedCategoryId !== category.id && draft ? mask(draft) : draft}
                          disabled={!canWrite}
                          onFocus={() => setFocusedCategoryId(category.id)}
                          onBlur={() => setFocusedCategoryId(null)}
                          onChange={(event) => setDraft(category.id, event.target.value)}
                          className="h-8 w-full min-w-0 bg-transparent text-end text-sm font-semibold tabular-nums text-foreground outline-none placeholder:text-muted-foreground/50 disabled:opacity-60"
                        />
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          ))
        )}
      </SectionCard>

      <BudgetGroupsSection
        month={monthParam}
        selectedMonth={selectedMonth}
        categories={categoriesList ?? []}
        groups={groupsList ?? []}
        forecastChoices={forecastChoices}
      />

      <Dialog open={suggestOpen} onOpenChange={(open) => { setSuggestOpen(open); if (!open) setSuggestEdits({}) }}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('budgets.suggestTitle')}</DialogTitle></DialogHeader>
          <div className="space-y-4">
            <div className="flex items-center gap-4">
              <div className="space-y-1">
                <Label>{t('budgets.suggestPeriod')}</Label>
                <select
                  value={suggestMonths}
                  onChange={(event) => { setSuggestMonths(Number(event.target.value)); setSuggestEdits({}) }}
                  className="w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {[3, 6, 12].map((months) => <option key={months} value={months}>{months} {t('budgets.suggestMonths')}</option>)}
                </select>
              </div>
              <label className="flex items-center gap-2 cursor-pointer mt-5">
                <input type="checkbox" checked={suggestRecurring} onChange={(event) => setSuggestRecurring(event.target.checked)} className="rounded border-border" />
                <span className="text-sm text-foreground">{t('budgets.repeatEveryMonth')}</span>
              </label>
            </div>
            <p className="text-sm text-muted-foreground">{t('budgets.suggestDescription', { months: suggestMonths })}</p>
            {suggestionsPending ? (
              <p className="text-sm text-muted-foreground text-center py-8">{t('common.loading')}</p>
            ) : suggestionsError ? (
              <p role="alert" className="text-sm text-destructive text-center py-8">{t('budgets.suggestError')}</p>
            ) : suggestable.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">{t('budgets.suggestEmpty')}</p>
            ) : (
              <div className="max-h-72 overflow-y-auto rounded-lg border border-border">
                {suggestable.map((suggestion) => {
                  const row = rowFor(suggestion)
                  return (
                    <div key={suggestion.category_id} className="flex items-center gap-3 border-b border-border last:border-0 px-3 py-2">
                      <input
                        type="checkbox"
                        checked={row.checked}
                        onChange={(event) => setSuggestEdits((previous) => ({ ...previous, [suggestion.category_id]: { ...rowFor(suggestion), checked: event.target.checked } }))}
                        className="rounded border-border"
                        aria-label={suggestion.category_name}
                      />
                      <span className="flex-1 text-sm font-medium text-foreground truncate">{suggestion.category_name}</span>
                      <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">{t('budgets.suggestSpent', { amount: fmt(Number(suggestion.total_spent)) })}</span>
                      <Input
                        type="number"
                        step="0.01"
                        min="0"
                        value={row.amount}
                        onChange={(event) => setSuggestEdits((previous) => ({ ...previous, [suggestion.category_id]: { ...rowFor(suggestion), amount: event.target.value } }))}
                        className="w-24 h-8"
                        aria-label={`${suggestion.category_name} ${t('budgets.amount')}`}
                      />
                    </div>
                  )
                })}
              </div>
            )}
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setSuggestOpen(false)}>{t('common.cancel')}</Button>
            <Button type="button" disabled={batchMutation.isPending || batchItems.length === 0} onClick={() => batchMutation.mutate()}>{t('budgets.suggestCreate')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <BudgetCopyDialog
        // A fresh mount per opening resets the source month and the strategy.
        key={copyOpen ? selectedMonth : 'closed'}
        open={copyOpen}
        onOpenChange={setCopyOpen}
        targetMonth={selectedMonth}
        targetBudgets={budgetsList ?? []}
        targetRefreshing={budgetsFetching}
      />

      {/* Save bar: same fixed placement as the transactions bulk-action bar, so it
          stays in reach while editing a long list. */}
      {canWrite && allCategories.length > 0 && !budgetsLoading && !categoriesLoading && !groupsLoading && !budgetsError && !categoriesError && !groupsError && budgetsList && budgetSeries && categoriesList && groupsList && (
        <div className={cn(
          'fixed bottom-0 start-0 end-0 z-40',
          'transition-[left] duration-300 ease-in-out motion-reduce:transition-none',
          sidebarCollapsed ? 'lg:start-16' : 'lg:start-60',
        )}>
          <div className="mx-auto max-w-7xl px-3 md:px-6 pb-4 md:pb-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between bg-card border border-border shadow-xl rounded-2xl px-4 py-3">
              <div className="text-sm tabular-nums">
                <span className="font-semibold text-foreground">{t('budgets.total', { amount: fmt(totalBudget) })}</span>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="outline" size="sm" disabled={operations.length === 0 || saveMutation.isPending} onClick={() => setDraftState(emptyDrafts())}>
                  {t('common.cancel')}
                </Button>
                <Button size="sm" disabled={operations.length === 0 || invalidCategories.size > 0 || saveMutation.isPending} onClick={() => saveMutation.mutate(operations)}>
                  {t('common.save')}
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Render after the fixed Save bar so its backdrop also covers those controls. */}
      {drillDownCategoryId !== null && (
        <TransactionDrillDown
          useViewerShare={activeAccountIds === null}
          filter={{
            title: t('dashboard.drillDownCategory', {
              category: allCategories.find((category) => category.id === drillDownCategoryId)?.name ?? drillDownCategoryId,
              month: monthLabel(selectedMonth, uiLocale),
            }),
            category_id: drillDownCategoryId,
            type: 'debit',
            ...monthRange(selectedMonth),
            account_ids: activeAccountIds ?? undefined,
          }}
          onClose={() => setDrillDownCategoryId(null)}
        />
      )}
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
  groups,
  forecastChoices,
}: {
  month: string
  selectedMonth: string
  categories: Category[]
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
          description={t('budgets.groupsHint')}
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
            <thead>
              <tr className="border-b border-border">
                <th className={`${TH} pl-4 sm:pl-5 text-left`}>{t('budgets.groupName')}</th>
                <th className={`${TH} text-left w-56 hidden sm:table-cell`}>{t('budgets.spent')}</th>
                <th className={`${TH} text-left w-24 sm:w-28`}>{t('budgets.groupCap')}</th>
                <th className={`${TH} text-center w-20 sm:w-24`} title={t('budgets.forecastHint')}>{t('budgets.inForecast')}</th>
                {canWrite && <th className={`${TH} pr-4 sm:pr-5 text-right w-20 sm:w-24`}>{t('budgets.actions')}</th>}
              </tr>
            </thead>
            <tbody>
              {comparison.map((group) => (
                <BudgetGroupRow
                  key={group.id}
                  group={group}
                  displayCategories={categories}
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
