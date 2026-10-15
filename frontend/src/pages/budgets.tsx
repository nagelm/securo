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
} from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DeleteConfirmationDialog } from '@/components/delete-confirmation-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import type { Budget, BudgetGroup, BudgetGroupVsActual, Category, CategoryGroup } from '@/types'
import { Pencil, Trash2, Plus, Repeat, CalendarIcon } from 'lucide-react'
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

function currentMonth() {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

const TH = 'text-xs font-medium text-muted-foreground py-3'
const selectClass = 'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

// Fork: an end month travels as the first day of the month; the form's
// month input speaks yyyy-MM.
const toMonthInput = (iso: string | null | undefined) => (iso ? iso.slice(0, 7) : '')
const fromMonthInput = (value: FormDataEntryValue | null) => (value ? `${value as string}-01` : null)

function SectionCard({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
      {children}
    </div>
  )
}
function SectionHeader({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <div className="px-4 sm:px-5 py-4 border-b border-border flex flex-wrap items-center justify-between gap-2">
      <p className="text-sm font-semibold text-foreground">{title}</p>
      {action}
    </div>
  )
}

export default function BudgetsPage() {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const [selectedMonth, setSelectedMonth] = useState(currentMonth)
  const [monthCalOpen, setMonthCalOpen] = useState(false)
  const dateFnsLocale = resolveDateFnsLocale(i18n.resolvedLanguage ?? i18n.language)
  const monthParam = `${selectedMonth}-01`
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Budget | null>(null)
  const [deletingBudget, setDeletingBudget] = useState<Budget | null>(null)
  const [newRecurring, setNewRecurring] = useState(false)

  const { data: budgetsList } = useQuery({
    queryKey: ['budgets', selectedMonth],
    queryFn: () => budgetsApi.list(monthParam),
  })

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

  const createMutation = useMutation({
    mutationFn: (data: {
      category_id: string
      amount: number
      month: string
      is_recurring?: boolean
      end_month?: string | null
    }) => budgetsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setDialogOpen(false)
      toast.success(t('budgets.created'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...changes }: { id: string; amount: number; end_month?: string | null }) =>
      budgetsApi.update(id, changes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('budgets.updated'))
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => budgetsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
      setDeletingBudget(null)
      toast.success(t('budgets.deleted'))
    },
    onError: (err: unknown) => {
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const displayCategories = allCategoriesList ?? categoriesList ?? []

  const getCategoryDisplay = (categoryId: string) => {
    const category = findCategoryReference(displayCategories, categoryId)
    if (!category) return <span>{categoryId}</span>
    return (
      <span className="flex items-center gap-2">
        <CategoryIcon icon={category.icon} color={category.color} size="sm" />
        <span>{category.name}</span>
      </span>
    )
  }

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
              <Button size="sm" className="gap-1.5 h-8" onClick={() => { setEditing(null); setNewRecurring(false); setDialogOpen(true) }}>
                <Plus size={13} /> {t('budgets.add')}
              </Button>
            ) : undefined
          }
        />
        {budgetsList && budgetsList.length > 0 ? (
          <table className="w-full">
            <thead>
              <tr className="border-b border-border">
                <th className={`${TH} pl-4 sm:pl-5 text-left`}>{t('budgets.category')}</th>
                <th className={`${TH} text-left w-36`}>{t('budgets.amount')}</th>
                {canWrite && <th className={`${TH} pr-4 sm:pr-5 text-right w-24`}>{t('budgets.actions')}</th>}
              </tr>
            </thead>
            <tbody>
              {budgetsList.map((budget) => (
                <tr key={budget.id} className="border-b border-border last:border-0 hover:bg-muted transition-colors">
                  <td className="py-3 pl-4 sm:pl-5 text-sm font-medium text-foreground">
                    <span className="flex items-center gap-1.5">
                      {getCategoryDisplay(budget.category_id)}
                      {budget.is_recurring && (
                        <span title={t('budgets.recurringLabel')} className="text-muted-foreground">
                          <Repeat size={12} />
                        </span>
                      )}
                      {budget.end_month && (
                        <span className="text-xs font-normal text-muted-foreground" data-testid="budget-until">
                          {t('budgets.untilLabel', { month: monthLabel(toMonthInput(budget.end_month), uiLocale) })}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="py-3 text-sm font-semibold tabular-nums text-foreground">{mask(formatCurrency(budget.amount, userCurrency, locale))}</td>
                  {canWrite && (
                    <td className="py-3 pr-4 sm:pr-5">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                          onClick={() => { setEditing(budget); setDialogOpen(true) }}
                          aria-label={t('common.edit')}
                          title={t('common.edit')}
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                          onClick={() => setDeletingBudget(budget)}
                          disabled={deleteMutation.isPending}
                          aria-label={t('common.delete')}
                          title={t('common.delete')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('budgets.empty')}</p>
        )}
      </SectionCard>

      <Dialog open={dialogOpen} onOpenChange={() => { setDialogOpen(false); setEditing(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('budgets.edit') : t('budgets.add')}</DialogTitle>
          </DialogHeader>
          <form
            key={editing?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              if (editing) {
                updateMutation.mutate({
                  id: editing.id,
                  amount: parseFloat(formData.get('amount') as string),
                  ...(editing.is_recurring ? { end_month: fromMonthInput(formData.get('end_month')) } : {}),
                })
              } else {
                const isRecurring = formData.get('is_recurring') === 'on'
                createMutation.mutate({
                  category_id: formData.get('category_id') as string,
                  amount: parseFloat(formData.get('amount') as string),
                  month: monthParam,
                  is_recurring: isRecurring,
                  ...(isRecurring ? { end_month: fromMonthInput(formData.get('end_month')) } : {}),
                })
              }
            }}
            className="space-y-4"
          >
            {!editing && (
              <>
                <div className="space-y-2">
                  <Label>{t('budgets.category')}</Label>
                  <select
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
                <Input
                  id="budget-end-month"
                  name="end_month"
                  type="month"
                  min={editing ? toMonthInput(editing.month) : selectedMonth}
                  defaultValue={toMonthInput(editing?.end_month)}
                />
              </div>
            )}
            <div className="space-y-2">
              <Label>{t('budgets.amount')}</Label>
              <Input
                name="amount"
                type="number"
                step="0.01"
                defaultValue={editing?.amount?.toString() ?? ''}
                required
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => { setDialogOpen(false); setEditing(null) }}>
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
        currency={userCurrency}
        categories={categoriesList ?? []}
        displayCategories={displayCategories}
        groups={groupsList ?? []}
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

function BudgetGroupsSection({
  month,
  selectedMonth,
  currency,
  categories,
  displayCategories,
  groups,
}: {
  month: string
  selectedMonth: string
  currency: string
  categories: Category[]
  displayCategories: Category[]
  groups: CategoryGroup[]
}) {
  const { t, i18n } = useTranslation()
  const { mask } = usePrivacyMode()
  const { canWrite } = useWorkspace()
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
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
    mutationFn: (data: Parameters<typeof budgetGroupsApi.create>[0]): Promise<BudgetGroup> =>
      editing ? budgetGroupsApi.update(editing.id, data) : budgetGroupsApi.create(data),
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
  const categoryName = (id: string) => findCategoryReference(displayCategories, id)?.name ?? '—'
  const money = (v: number | string) => mask(formatCurrency(Number(v), currency, locale))

  return (
    <div className="mt-6">
      <SectionCard>
        <SectionHeader
          title={t('budgets.groupsTitle')}
          action={
            canWrite ? (
              <Button size="sm" className="gap-1.5 h-8" onClick={() => { setEditing(null); setDialogOpen(true) }}>
                <Plus size={13} /> {t('budgets.addGroup')}
              </Button>
            ) : undefined
          }
        />
        <p className="px-4 sm:px-5 pt-3 text-xs text-muted-foreground">{t('budgets.groupsHint')}</p>
        {comparison && comparison.length > 0 ? (
          <ul className="divide-y divide-border">
            {comparison.map((group) => {
              const pct = Math.min(group.percentage_used ?? 0, 100)
              const over = (group.percentage_used ?? 0) > 100
              return (
                <li key={group.id} className="px-4 sm:px-5 py-3" data-testid="budget-group">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-foreground flex items-center gap-1.5">
                        {group.name}
                        {group.is_recurring && (
                          <span title={t('budgets.recurringLabel')} className="text-muted-foreground"><Repeat size={12} /></span>
                        )}
                        {group.end_month && (
                          <span className="text-xs font-normal text-muted-foreground">
                            {t('budgets.untilLabel', { month: monthLabel(toMonthInput(group.end_month), uiLocale) })}
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground truncate">
                        {group.category_ids.map(categoryName).join(', ')}
                      </p>
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <span className="text-sm tabular-nums text-foreground">
                        {t('budgets.spentOf', { spent: money(group.projected_amount), cap: money(group.amount) })}
                      </span>
                      {canWrite && (
                        <>
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                            onClick={() => { setEditing(group); setDialogOpen(true) }}
                            aria-label={t('common.edit')}
                            title={t('common.edit')}
                          >
                            <Pencil size={13} />
                          </button>
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 transition-colors"
                            onClick={() => setDeleting(group)}
                            aria-label={t('common.delete')}
                            title={t('common.delete')}
                          >
                            <Trash2 size={13} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="mt-2 h-1.5 rounded-full bg-muted overflow-hidden" role="progressbar" aria-label={group.name} aria-valuenow={group.percentage_used ?? 0}>
                    <div className={over ? 'h-full bg-rose-500' : 'h-full bg-primary'} style={{ width: `${pct}%` }} />
                  </div>
                </li>
              )
            })}
          </ul>
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
  onSave: (data: Parameters<typeof budgetGroupsApi.create>[0]) => void
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
  const start = group?.month ?? month

  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (chosen.length === 0) {
          toast.error(t('budgets.pickCategories'))
          return
        }
        onSave({
          name,
          amount: parseFloat(amount),
          month: start,
          is_recurring: isRecurring,
          end_month: isRecurring && endMonth ? `${endMonth}-01` : null,
          category_ids: chosen,
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
          <Input
            id="group-end-month"
            type="month"
            min={toMonthInput(start) || selectedMonth}
            value={endMonth}
            onChange={(e) => setEndMonth(e.target.value)}
            className={selectClass}
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
