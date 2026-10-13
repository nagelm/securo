import { createElement, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAccountName, sortAccountsByPosition } from '@/lib/account-utils'
import { useTranslation } from 'react-i18next'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { goals as goalsApi, accounts as accountsApi, assets as assetsApi, assetGroups as assetGroupsApi, currencies as currenciesApi } from '@/lib/api'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from '@/components/ui/popover'
import type { Account, Asset, AssetGroup, ContributionPeriod, Goal } from '@/types'
import {
  Pencil, Trash2, Plus, Pause, Play, CheckCircle2, Archive, ArchiveRestore, Target,
  ChevronDown, PiggyBank,
} from 'lucide-react'
import { ContributionDots, ContributionLine } from '@/components/goal-contribution'
import { GoalQuickSetupDialog } from '@/components/goal-quick-setup-dialog'
import { CONTRIBUTION_PERIODS, periodPercentage, quickSetupCandidates } from '@/lib/goal-contribution'
import { ICON_MAP } from '@/lib/category-icons'
import { IconPicker } from '@/components/icon-picker'
import { PageHeader } from '@/components/page-header'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import { formatCurrency } from '@/lib/format'

function getGoalIcon(iconKey: string | null) {
  return (iconKey && ICON_MAP[iconKey]) || Target
}

const PRESET_COLORS = [
  '#3B82F6', '#10B981', '#F59E0B', '#EF4444',
  '#8B5CF6', '#EC4899', '#06B6D4', '#F97316',
]

const SELECT_CLASS = 'w-full border border-border rounded-lg px-3 py-2 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

function LinkedResourceSelect<T extends { id: string }>({
  name,
  label,
  placeholder,
  defaultValue,
  items,
  renderOption,
  hint,
  onChange,
}: {
  name: string
  label: string
  placeholder: string
  defaultValue: string | null | undefined
  items: T[] | undefined
  renderOption: (item: T) => React.ReactNode
  hint?: string
  onChange?: (value: string) => void
}) {
  return (
    <div className="space-y-2">
      <Label>{label}</Label>
      <select
        name={name}
        defaultValue={defaultValue ?? ''}
        className={SELECT_CLASS}
        required
        onChange={onChange ? (e) => onChange(e.target.value) : undefined}
      >
        <option value="">{placeholder}</option>
        {items?.map((item) => (
          <option key={item.id} value={item.id}>
            {renderOption(item)}
          </option>
        ))}
      </select>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

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

function OnTrackBadge({ status, t }: { status: string | null; t: (key: string) => string }) {
  if (!status) return null
  const config: Record<string, { bg: string; text: string; key: string }> = {
    ahead: { bg: 'bg-emerald-100 dark:bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-400', key: 'goals.onTrackAhead' },
    on_track: { bg: 'bg-blue-100 dark:bg-blue-500/20', text: 'text-blue-700 dark:text-blue-400', key: 'goals.onTrackOnTrack' },
    behind: { bg: 'bg-amber-100 dark:bg-amber-500/20', text: 'text-amber-700 dark:text-amber-400', key: 'goals.onTrackBehind' },
    overdue: { bg: 'bg-rose-100 dark:bg-rose-500/20', text: 'text-rose-700 dark:text-rose-400', key: 'goals.onTrackOverdue' },
    achieved: { bg: 'bg-emerald-100 dark:bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-400', key: 'goals.onTrackAchieved' },
  }
  const c = config[status]
  if (!c) return null
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${c.bg} ${c.text}`}>
      {t(c.key)}
    </span>
  )
}

function StatusBadge({ status, t }: { status: string; t: (key: string) => string }) {
  const config: Record<string, { bg: string; text: string; key: string }> = {
    active: { bg: 'bg-emerald-100 dark:bg-emerald-500/20', text: 'text-emerald-700 dark:text-emerald-400', key: 'goals.statusActive' },
    completed: { bg: 'bg-blue-100 dark:bg-blue-500/20', text: 'text-blue-700 dark:text-blue-400', key: 'goals.statusCompleted' },
    paused: { bg: 'bg-amber-100 dark:bg-amber-500/20', text: 'text-amber-700 dark:text-amber-400', key: 'goals.statusPaused' },
    archived: { bg: 'bg-muted', text: 'text-muted-foreground', key: 'goals.statusArchived' },
  }
  const c = config[status] ?? config.active
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold ${c.bg} ${c.text}`}>
      {t(c.key)}
    </span>
  )
}

function daysUntil(dateStr: string): number {
  const target = new Date(dateStr + 'T00:00:00')
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Math.ceil((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24))
}

export default function GoalsPage() {
  const { t } = useTranslation()
  const { mask } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const userCurrency = user?.preferences?.currency_display ?? 'USD'
  const locale = useDisplayLocale()
  const queryClient = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<Goal | null>(null)
  const [trackingType, setTrackingType] = useState('manual')
  const [statusFilter, setStatusFilter] = useState<string>('active')
  const [deletingGoal, setDeletingGoal] = useState<Goal | null>(null)
  const [selectedIcon, setSelectedIcon] = useState('target')
  const [selectedColor, setSelectedColor] = useState('#3B82F6')
  const [targetDate, setTargetDate] = useState('')
  const [contributionAmount, setContributionAmount] = useState('')
  const [contributionPeriod, setContributionPeriod] = useState<ContributionPeriod | ''>('')
  const [linkedAccountId, setLinkedAccountId] = useState('')
  const [prefillName, setPrefillName] = useState('')
  const [quickSetupOpen, setQuickSetupOpen] = useState(false)
  const [searchParams, setSearchParams] = useSearchParams()

  const { data: goalsList } = useQuery({
    queryKey: ['goals', statusFilter],
    queryFn: () => goalsApi.list(statusFilter || undefined),
  })

  const { data: accountsList } = useQuery({
    queryKey: ['accounts'],
    queryFn: () => accountsApi.list(),
  })

  const { data: assetsList } = useQuery({
    queryKey: ['assets'],
    queryFn: () => assetsApi.list(),
  })

  const { data: walletsList } = useQuery({
    queryKey: ['asset-groups'],
    queryFn: assetGroupsApi.list,
  })

  // Every goal, whatever the filter: quick setup skips accounts that already have one.
  const { data: allGoals } = useQuery({
    queryKey: ['goals', ''],
    queryFn: () => goalsApi.list(),
    enabled: quickSetupOpen,
  })

  // A regular target read from what already pays into the picked account.
  const { data: suggestion } = useQuery({
    queryKey: ['goals', 'contribution-suggestion', linkedAccountId],
    queryFn: () => goalsApi.contributionSuggestion(linkedAccountId),
    enabled: dialogOpen && trackingType === 'account' && !!linkedAccountId,
    staleTime: 60_000,
  })

  const { data: supportedCurrencies } = useQuery({
    queryKey: ['currencies'],
    queryFn: currenciesApi.list,
    staleTime: Infinity,
  })

  const createMutation = useMutation({
    mutationFn: (data: Partial<Goal>) => goalsApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      setDialogOpen(false)
      toast.success(t('goals.created'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, ...data }: Partial<Goal> & { id: string }) => goalsApi.update(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      setDialogOpen(false)
      setEditing(null)
      toast.success(t('goals.updated'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => goalsApi.delete(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      setDeletingGoal(null)
      toast.success(t('goals.deleted'))
    },
    onError: () => toast.error(t('common.error')),
  })

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => goalsApi.update(id, { status } as Partial<Goal>),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      toast.success(t('goals.updated'))
    },
  })

  const openCreateDialog = (account?: Account) => {
    setEditing(null)
    setTrackingType(account ? 'account' : 'manual')
    setSelectedIcon(account ? 'piggy-bank' : 'target')
    setSelectedColor(account ? '#10B981' : '#3B82F6')
    setTargetDate('')
    setContributionAmount('')
    setContributionPeriod('')
    setLinkedAccountId(account?.id ?? '')
    setPrefillName(account ? getAccountName(account) : '')
    setDialogOpen(true)
  }

  const openEditDialog = (goal: Goal) => {
    setEditing(goal)
    setTrackingType(goal.tracking_type)
    setSelectedIcon(goal.icon ?? 'target')
    setSelectedColor(goal.color ?? '#3B82F6')
    setTargetDate(goal.target_date ?? '')
    setContributionAmount(goal.contribution_amount != null ? String(goal.contribution_amount) : '')
    setContributionPeriod(goal.contribution_period ?? '')
    setLinkedAccountId(goal.account_id ?? '')
    setPrefillName('')
    setDialogOpen(true)
  }

  // `/goals?new=1` opens the form; `&account_id=` links it to that account
  // (the "Savings goal" button on an account's page).
  const wantsNew = searchParams.get('new') === '1'
  const wantedAccountId = searchParams.get('account_id')
  useEffect(() => {
    if (!wantsNew) return
    if (wantedAccountId && !accountsList) return
    const account = wantedAccountId ? accountsList?.find((a) => a.id === wantedAccountId) : undefined
    openCreateDialog(account)
    setSearchParams({}, { replace: true })
    // openCreateDialog only sets state; running once per request is the point.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wantsNew, wantedAccountId, accountsList])

  const money = (currency: string) => (amount: number) => mask(formatCurrency(amount, currency, locale))
  const shortDate = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString(locale, { day: 'numeric', month: 'short' })

  return (
    <div>
      <PageHeader
        section={t('goals.title')}
        title={t('goals.title')}
      />

      {/* Status filter */}
      <div className="flex items-center gap-2 mb-4">
        {['active', 'completed', 'paused', 'archived', ''].map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              statusFilter === s
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:text-foreground'
            }`}
          >
            {s ? t(`goals.status${s.charAt(0).toUpperCase() + s.slice(1)}`) : t('transactions.all')}
          </button>
        ))}
      </div>

      <SectionCard>
        <SectionHeader
          title={t('goals.title')}
          action={
            canWrite ? (
              <div className="flex items-center gap-2">
                <Button size="sm" variant="outline" className="gap-1.5 h-8" onClick={() => setQuickSetupOpen(true)}>
                  <PiggyBank size={13} /> {t('goals.savings.quickSetup')}
                </Button>
                <Button size="sm" className="gap-1.5 h-8" onClick={() => openCreateDialog()}>
                  <Plus size={13} /> {t('goals.add')}
                </Button>
              </div>
            ) : undefined
          }
        />
        {goalsList && goalsList.length > 0 ? (
          <div className="divide-y divide-border">
            {goalsList.map((goal) => {
              const days = goal.target_date ? daysUntil(goal.target_date) : null
              const progress = goal.contribution_progress
              // An ongoing goal has no total: its bar shows this period's target instead.
              const ongoing = goal.target_amount == null
              const barPercentage = ongoing
                ? (progress ? periodPercentage(Number(progress.current.contributed), Number(progress.current.target)) : 0)
                : goal.percentage
              const progressColor = barPercentage >= 100
                ? 'bg-emerald-500'
                : barPercentage >= 60
                  ? 'bg-blue-500'
                  : barPercentage >= 30
                    ? 'bg-amber-400'
                    : 'bg-muted-foreground/30'

              const goalMoney = money(goal.currency)
              const GoalIcon = getGoalIcon(goal.icon)
              return (
                <div key={goal.id} className="px-4 sm:px-5 py-4 hover:bg-muted/50 transition-colors">
                  <div className="flex items-start gap-4">
                    {/* Icon */}
                    <div
                      className="w-10 h-10 rounded-xl flex items-center justify-center shrink-0 text-white"
                      style={{ backgroundColor: goal.color ?? '#6B7280' }}
                    >
                      <GoalIcon size={18} />
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-sm font-semibold text-foreground truncate">{goal.name}</span>
                        <StatusBadge status={goal.status} t={t} />
                        <OnTrackBadge status={goal.on_track} t={t} />
                      </div>

                      {/* Progress bar */}
                      {(!ongoing || progress) && (
                        <div className="flex items-center gap-3 mb-1.5">
                          <div className="flex-1 h-2 bg-muted/60 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${progressColor}`}
                              style={{ width: `${Math.min(barPercentage, 100)}%` }}
                            />
                          </div>
                          <span className="text-xs font-bold tabular-nums text-foreground shrink-0">
                            {barPercentage.toFixed(0)}%
                          </span>
                        </div>
                      )}

                      {/* Regular contribution: target, this period, streak, recent periods */}
                      {goal.contribution_amount != null && goal.contribution_period && (
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-1.5 text-xs text-muted-foreground">
                          <ContributionLine
                            amount={Number(goal.contribution_amount)}
                            period={goal.contribution_period}
                            progress={progress}
                            money={goalMoney}
                            t={t}
                          />
                          {progress ? (
                            <ContributionDots progress={progress} money={goalMoney} t={t} formatDate={shortDate} />
                          ) : goal.tracking_type !== 'account' ? (
                            <span className="italic">{t('goals.savings.needsAccount')}</span>
                          ) : null}
                        </div>
                      )}

                      {/* Details row */}
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                        <span className="tabular-nums font-medium">
                          {ongoing ? (
                            <>
                              {t('goals.savings.balance', { amount: goalMoney(goal.current_amount) })}
                              {' · '}
                              {t('goals.savings.ongoing')}
                            </>
                          ) : (
                            <>
                              {goalMoney(goal.current_amount)}
                              {' / '}
                              {goalMoney(goal.target_amount ?? 0)}
                            </>
                          )}
                        </span>
                        {goal.monthly_contribution != null && goal.monthly_contribution > 0 && (
                          <span className="tabular-nums">
                            {mask(formatCurrency(goal.monthly_contribution, goal.currency, locale))}{t('goals.perMonth')}
                          </span>
                        )}
                        {days !== null && (
                          <span className={days < 0 ? 'text-rose-500' : ''}>
                            {days >= 0
                              ? t('goals.daysRemaining', { count: days })
                              : t('goals.daysOverdue', { count: Math.abs(days) })}
                          </span>
                        )}
                        {!goal.target_date && !ongoing && (
                          <span>{t('goals.noTargetDate')}</span>
                        )}
                        {goal.account_name && (
                          <span>{goal.account_name}</span>
                        )}
                        {goal.asset_name && (
                          <span>{goal.asset_name}</span>
                        )}
                        {goal.asset_group_name && (
                          <span>{goal.asset_group_name}</span>
                        )}
                      </div>
                    </div>

                    {/* Actions */}
                    {canWrite && (
                      <div className="flex items-center gap-1 shrink-0">
                        {goal.status === 'active' && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-amber-500 hover:bg-amber-50 dark:hover:bg-amber-500/10 transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'paused' })}
                            title={t('goals.pause')}
                          >
                            <Pause size={13} />
                          </button>
                        )}
                        {goal.status === 'paused' && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-emerald-500 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'active' })}
                            title={t('goals.resume')}
                          >
                            <Play size={13} />
                          </button>
                        )}
                        {(goal.status === 'active' || goal.status === 'paused') && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-blue-500 hover:bg-blue-50 dark:hover:bg-blue-500/10 transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'completed' })}
                            title={t('goals.complete')}
                          >
                            <CheckCircle2 size={13} />
                          </button>
                        )}
                        {goal.status !== 'archived' && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-muted-foreground/80 hover:bg-muted transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'archived' })}
                            title={t('goals.archive')}
                          >
                            <Archive size={13} />
                          </button>
                        )}
                        {(goal.status === 'completed' || goal.status === 'archived') && (
                          <button
                            className="p-1.5 rounded-md text-muted-foreground hover:text-emerald-500 hover:bg-emerald-50 dark:hover:bg-emerald-500/10 transition-colors"
                            onClick={() => statusMutation.mutate({ id: goal.id, status: 'active' })}
                            title={t('goals.reactivate')}
                          >
                            <ArchiveRestore size={13} />
                          </button>
                        )}
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-primary hover:bg-primary/5 transition-colors"
                          onClick={() => openEditDialog(goal)}
                          title={t('common.edit')}
                        >
                          <Pencil size={13} />
                        </button>
                        <button
                          className="p-1.5 rounded-md text-muted-foreground hover:text-rose-500 hover:bg-rose-50 dark:hover:bg-rose-500/10 transition-colors"
                          onClick={() => setDeletingGoal(goal)}
                          title={t('common.delete')}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground text-center py-10">{t('goals.empty')}</p>
        )}
      </SectionCard>

      {/* Create/Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={() => { setDialogOpen(false); setEditing(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t('goals.edit') : t('goals.add')}</DialogTitle>
          </DialogHeader>
          <form
            key={editing?.id ?? 'new'}
            onSubmit={(e) => {
              e.preventDefault()
              const formData = new FormData(e.currentTarget)
              const rawTarget = (formData.get('target_amount') as string) ?? ''
              const target = rawTarget.trim() === '' ? null : parseFloat(rawTarget)
              const contribution = contributionPeriod && contributionAmount.trim() !== ''
                ? parseFloat(contributionAmount)
                : null
              if ((target == null || Number.isNaN(target)) && !(contribution != null && contribution > 0)) {
                toast.error(t('goals.savings.needTarget'))
                return
              }
              const payload: Record<string, unknown> = {
                name: formData.get('name') as string,
                target_amount: target,
                contribution_amount: contribution,
                contribution_period: contribution != null ? contributionPeriod : null,
                currency: (formData.get('currency') as string) || userCurrency,
                tracking_type: formData.get('tracking_type') as string,
                target_date: targetDate || null,
                icon: selectedIcon || null,
                color: selectedColor || null,
              }

              const tt = formData.get('tracking_type') as string
              if (tt === 'manual') {
                payload.current_amount = parseFloat((formData.get('current_amount') as string) || '0')
              }
              if (tt === 'account') {
                payload.account_id = (formData.get('account_id') as string) || null
              }
              if (tt === 'asset') {
                payload.asset_id = (formData.get('asset_id') as string) || null
              }
              if (tt === 'asset_group') {
                payload.asset_group_id = (formData.get('asset_group_id') as string) || null
              }

              if (editing) {
                updateMutation.mutate({ id: editing.id, ...payload } as Partial<Goal> & { id: string })
              } else {
                createMutation.mutate(payload as Partial<Goal>)
              }
            }}
            className="space-y-4"
          >
            <div className="space-y-2">
              <Label>{t('goals.name')}</Label>
              <Input name="name" defaultValue={editing?.name ?? prefillName} required />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label>{t('goals.savings.totalTarget')}</Label>
                <Input
                  name="target_amount"
                  type="number"
                  step="0.01"
                  defaultValue={editing?.target_amount?.toString() ?? ''}
                />
              </div>
              <div className="space-y-2">
                <Label>{t('goals.currency')}</Label>
                <select
                  name="currency"
                  defaultValue={editing?.currency ?? userCurrency}
                  className={SELECT_CLASS}
                >
                  {supportedCurrencies?.map((c: { code: string; name: string; flag: string }) => (
                    <option key={c.code} value={c.code}>
                      {c.flag} {c.name} ({c.code})
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <p className="-mt-2 text-xs text-muted-foreground">{t('goals.savings.totalTargetHint')}</p>

            <div className="space-y-2">
              <Label>{t('goals.savings.contribution')}</Label>
              <div className="grid grid-cols-2 gap-4">
                <Input
                  aria-label={t('goals.savings.contributionAmount')}
                  placeholder={t('goals.savings.contributionAmount')}
                  type="number"
                  step="0.01"
                  min="0"
                  value={contributionAmount}
                  onChange={(e) => setContributionAmount(e.target.value)}
                  disabled={!contributionPeriod}
                />
                <select
                  aria-label={t('goals.savings.contributionPeriod')}
                  value={contributionPeriod}
                  onChange={(e) => setContributionPeriod(e.target.value as ContributionPeriod | '')}
                  className={SELECT_CLASS}
                >
                  <option value="">{t('goals.savings.periodNone')}</option>
                  {CONTRIBUTION_PERIODS.map((p) => (
                    <option key={p} value={p}>{t(`goals.savings.per.${p}`)}</option>
                  ))}
                </select>
              </div>
              {contributionPeriod && (
                <p className="text-xs text-muted-foreground">{t('goals.savings.contributionHint')}</p>
              )}
              {suggestion && trackingType === 'account' && (
                <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs">
                  <span className="text-muted-foreground">
                    {t(
                      suggestion.source === 'recurring'
                        ? 'goals.savings.suggestionRecurring'
                        : 'goals.savings.suggestionHistory',
                      {
                        description: suggestion.description ?? '',
                        amount: formatCurrency(suggestion.amount, suggestion.currency, locale),
                        period: t(`goals.savings.per.${suggestion.period}`),
                      },
                    )}
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    className="h-7"
                    onClick={() => {
                      setContributionAmount(String(suggestion.amount))
                      setContributionPeriod(suggestion.period)
                    }}
                  >
                    {t('goals.savings.useSuggestion')}
                  </Button>
                </div>
              )}
            </div>

            <div className="space-y-2">
              <Label>{t('goals.targetDate')}</Label>
              <DatePickerInput
                value={targetDate}
                onChange={setTargetDate}
                className="w-full justify-start"
              />
            </div>

            <div className="space-y-2">
              <Label>{t('goals.trackingType')}</Label>
              <select
                name="tracking_type"
                value={trackingType}
                onChange={(e) => setTrackingType(e.target.value)}
                className={SELECT_CLASS}
              >
                <option value="manual">{t('goals.trackingManual')}</option>
                <option value="account">{t('goals.trackingAccount')}</option>
                <option value="asset">{t('goals.trackingAsset')}</option>
                <option value="asset_group">{t('goals.trackingWallet')}</option>
                <option value="net_worth">{t('goals.trackingNetWorth')}</option>
              </select>
            </div>

            {trackingType === 'manual' && (
              <div className="space-y-2">
                <Label>{t('goals.currentAmount')}</Label>
                <Input
                  name="current_amount"
                  type="number"
                  step="0.01"
                  defaultValue={editing?.tracking_type === 'manual' ? editing?.current_amount?.toString() : '0'}
                />
              </div>
            )}

            {trackingType === 'account' && (
              <LinkedResourceSelect<Account>
                name="account_id"
                label={t('goals.account')}
                placeholder={t('goals.selectAccount')}
                defaultValue={editing?.account_id ?? linkedAccountId}
                items={sortAccountsByPosition(accountsList ?? [])}
                renderOption={(acc) => `${getAccountName(acc)} (${acc.currency})`}
                onChange={setLinkedAccountId}
              />
            )}

            {trackingType === 'asset' && (
              <LinkedResourceSelect<Asset>
                name="asset_id"
                label={t('goals.asset')}
                placeholder={t('goals.selectAsset')}
                defaultValue={editing?.asset_id}
                items={assetsList}
                renderOption={(asset) => `${asset.name} (${asset.currency})`}
              />
            )}

            {trackingType === 'asset_group' && (
              <LinkedResourceSelect<AssetGroup>
                name="asset_group_id"
                label={t('goals.wallet')}
                placeholder={t('goals.selectWallet')}
                defaultValue={editing?.asset_group_id}
                items={walletsList}
                renderOption={(wallet) => wallet.name}
                hint={t('goals.walletHint')}
              />
            )}

            {/* Icon & Color */}
            <div className="space-y-2">
              <Label>{t('goals.icon')}</Label>
              <Popover>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className="w-full flex items-center gap-3 border border-border rounded-lg px-3 py-2 text-sm bg-card hover:bg-muted/50 transition-colors text-left"
                  >
                    <div
                      className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0 text-white"
                      style={{ backgroundColor: selectedColor }}
                    >
                      {createElement(getGoalIcon(selectedIcon), { size: 18 })}
                    </div>
                    <span className="flex-1 text-muted-foreground">{t('goals.chooseIconColor')}</span>
                    <ChevronDown size={14} className="text-muted-foreground" />
                  </button>
                </PopoverTrigger>
                <PopoverContent align="start" className="w-80 p-3 space-y-3">
                  {/* Color presets */}
                  <div className="flex items-center gap-1.5">
                    {PRESET_COLORS.map((c) => (
                      <button
                        key={c}
                        type="button"
                        aria-label={c}
                        title={c}
                        onClick={() => setSelectedColor(c)}
                        className={`w-7 h-7 rounded-full transition-all ${
                          selectedColor === c ? 'ring-2 ring-offset-1 ring-primary scale-110' : 'hover:scale-110'
                        }`}
                        style={{ backgroundColor: c }}
                      />
                    ))}
                    <input
                      type="color"
                      value={selectedColor}
                      onChange={(e) => setSelectedColor(e.target.value)}
                      className="w-7 h-7 rounded-full cursor-pointer border-0 p-0"
                    />
                  </div>
                  {/* Icon picker */}
                  <IconPicker value={selectedIcon} color={selectedColor} onChange={setSelectedIcon} />
                </PopoverContent>
              </Popover>
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

      <GoalQuickSetupDialog
        open={quickSetupOpen}
        onOpenChange={setQuickSetupOpen}
        candidates={quickSetupOpen && allGoals ? quickSetupCandidates(accountsList ?? [], allGoals) : []}
      />

      {/* Confirm delete dialog */}
      <Dialog open={!!deletingGoal} onOpenChange={() => setDeletingGoal(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('goals.confirmDeleteTitle')}</DialogTitle>
          </DialogHeader>
          <p className="text-sm text-muted-foreground">
            {t('goals.confirmDeleteDesc', { name: deletingGoal?.name })}
          </p>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeletingGoal(null)}>
              {t('common.cancel')}
            </Button>
            <Button
              variant="destructive"
              onClick={() => deletingGoal && deleteMutation.mutate(deletingGoal.id)}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? t('common.loading') : t('common.delete')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
