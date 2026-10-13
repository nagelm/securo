import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useMutation, useQueries, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { goals as goalsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { getAccountName } from '@/lib/account-utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { CONTRIBUTION_PERIODS } from '@/lib/goal-contribution'
import type { Account, ContributionPeriod, Goal } from '@/types'

const SELECT_CLASS = 'border border-border rounded-lg px-2 py-1.5 text-sm bg-card text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

interface Row {
  selected: boolean
  name: string
  amount: string
  period: ContributionPeriod
  // Set once the user types in the row, so a late suggestion never overwrites it.
  touched: boolean
}

/**
 * Create one goal per picked account in a single request. Each goal tracks
 * its account; the regular target is prefilled from the recurring transaction
 * or the regular transfers that already pay into the account.
 */
export function GoalQuickSetupDialog({
  open,
  onOpenChange,
  candidates,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  candidates: Account[]
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [rows, setRows] = useState<Record<string, Row>>({})

  const suggestions = useQueries({
    queries: candidates.map((a) => ({
      queryKey: ['goals', 'contribution-suggestion', a.id],
      queryFn: () => goalsApi.contributionSuggestion(a.id),
      enabled: open,
      staleTime: 60_000,
    })),
  })

  const rowFor = (account: Account, index: number): Row => {
    const existing = rows[account.id]
    const suggestion = suggestions[index]?.data
    if (existing?.touched) return existing
    return {
      selected: existing?.selected ?? (account.type === 'savings' && !!suggestion),
      name: existing?.name ?? getAccountName(account),
      amount: suggestion ? String(suggestion.amount) : (existing?.amount ?? ''),
      period: suggestion?.period ?? existing?.period ?? 'weekly',
      touched: false,
    }
  }

  const update = (account: Account, index: number, patch: Partial<Row>) => {
    setRows((prev) => ({ ...prev, [account.id]: { ...rowFor(account, index), ...patch, touched: true } }))
  }

  const createMutation = useMutation({
    mutationFn: (items: Partial<Goal>[]) => goalsApi.createMany(items),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['goals'] })
      toast.success(t('goals.savings.quickSetupCreated'))
      setRows({})
      onOpenChange(false)
    },
    onError: (err) => toast.error(extractApiError(err, t('common.error'))),
  })

  const submit = () => {
    const items: Partial<Goal>[] = []
    for (const [index, account] of candidates.entries()) {
      const row = rowFor(account, index)
      if (!row.selected) continue
      const amount = parseFloat(row.amount)
      if (!row.name.trim() || !(amount > 0)) {
        toast.error(t('goals.savings.needTarget'))
        return
      }
      items.push({
        name: row.name.trim(),
        currency: account.currency,
        tracking_type: 'account',
        account_id: account.id,
        contribution_amount: amount,
        contribution_period: row.period,
        icon: 'piggy-bank',
        color: '#10B981',
      })
    }
    if (items.length === 0) {
      toast.error(t('goals.savings.quickSetupNoneSelected'))
      return
    }
    createMutation.mutate(items)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t('goals.savings.quickSetup')}</DialogTitle>
          <p className="text-sm text-muted-foreground">{t('goals.savings.quickSetupDesc')}</p>
        </DialogHeader>
        {candidates.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">{t('goals.savings.quickSetupEmpty')}</p>
        ) : (
          <div className="max-h-[60vh] overflow-y-auto divide-y divide-border rounded-lg border border-border">
            {candidates.map((account, index) => {
              const row = rowFor(account, index)
              return (
                <div key={account.id} className="flex flex-wrap items-center gap-2 p-3">
                  <input
                    type="checkbox"
                    aria-label={getAccountName(account)}
                    checked={row.selected}
                    onChange={(e) => update(account, index, { selected: e.target.checked })}
                    className="h-4 w-4 rounded border-border text-primary focus:ring-primary"
                  />
                  <Input
                    aria-label={t('goals.name')}
                    value={row.name}
                    onChange={(e) => update(account, index, { name: e.target.value })}
                    className="h-8 flex-1 min-w-[10rem]"
                  />
                  <Input
                    aria-label={t('goals.savings.contributionAmount')}
                    type="number"
                    step="0.01"
                    min="0"
                    value={row.amount}
                    onChange={(e) => update(account, index, { amount: e.target.value })}
                    className="h-8 w-28"
                  />
                  <span className="text-xs text-muted-foreground">{account.currency}</span>
                  <select
                    aria-label={t('goals.savings.contributionPeriod')}
                    value={row.period}
                    onChange={(e) => update(account, index, { period: e.target.value as ContributionPeriod })}
                    className={SELECT_CLASS}
                  >
                    {CONTRIBUTION_PERIODS.map((p) => (
                      <option key={p} value={p}>{t(`goals.savings.per.${p}`)}</option>
                    ))}
                  </select>
                </div>
              )
            })}
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            {t('common.cancel')}
          </Button>
          <Button type="button" onClick={submit} disabled={createMutation.isPending || candidates.length === 0}>
            {t('goals.savings.quickSetupCreate')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
