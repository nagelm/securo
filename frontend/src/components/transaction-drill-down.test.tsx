import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor } from '@testing-library/react'

import { TransactionDrillDown } from '@/components/transaction-drill-down'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { admin, dashboard, transactions } from '@/lib/api'
import { renderWithProviders, t } from '@/test/utils'

vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))

vi.mock('@/lib/api', () => ({
  transactions: { list: vi.fn() },
  dashboard: { projectedTransactions: vi.fn() },
  admin: { accountingMode: vi.fn() },
}))

const filter = { title: 'Uncategorized', uncategorized: true }

function renderPanel({ dialogOpen }: { dialogOpen: boolean }) {
  const onClose = vi.fn()
  const result = renderWithProviders(
    <>
      <TransactionDrillDown filter={filter} onClose={onClose} />
      <Dialog open={dialogOpen}>
        <DialogContent>
          <DialogTitle>Edit transaction</DialogTitle>
          <button type="button">Category</button>
        </DialogContent>
      </Dialog>
    </>,
  )
  return { ...result, onClose }
}

// The outside-click listener is attached after a short delay so the click
// that opened the panel does not close it.
async function waitForOutsideClickListener() {
  await new Promise(resolve => setTimeout(resolve, 150))
}

describe('TransactionDrillDown', () => {
  beforeEach(() => {
    vi.mocked(transactions.list).mockResolvedValue({ items: [], total: 0 } as never)
    vi.mocked(dashboard.projectedTransactions).mockResolvedValue([])
    vi.mocked(admin.accountingMode).mockResolvedValue({ mode: 'cash' } as never)
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it('closes on a click outside the panel', async () => {
    const { onClose } = renderPanel({ dialogOpen: false })
    await waitForOutsideClickListener()

    fireEvent.mouseDown(document.body)

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('stays open while the user clicks inside a dialog opened from it', async () => {
    const { onClose } = renderPanel({ dialogOpen: true })
    await waitForOutsideClickListener()

    fireEvent.mouseDown(await screen.findByRole('button', { name: 'Category' }))

    expect(onClose).not.toHaveBeenCalled()
  })

  it('leaves Escape to the open dialog', async () => {
    const { onClose } = renderPanel({ dialogOpen: true })
    await screen.findByRole('dialog')

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(onClose).not.toHaveBeenCalled()
  })

  it('closes on Escape when no dialog is open', async () => {
    const { onClose } = renderPanel({ dialogOpen: false })

    fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1))
  })
})

describe('TransactionDrillDown with a recurring loan payment', () => {
  // Made-up figures: a 2,000 payment of which 1,200 is interest.
  const loanPayment = {
    recurring_id: 'rec-1',
    account_id: 'chk-1',
    description: 'Mortgage payment',
    amount: 2000,
    amount_primary: null,
    currency: 'USD',
    type: 'debit' as const,
    date: '2026-03-20',
    category_id: null,
    category_name: null,
    category_icon: null,
    category_color: null,
    kind: 'loan_payment' as const,
    transfer_account_id: 'loan-1',
    interest_amount: 1200,
    interest_amount_primary: null,
    interest_category_id: 'cat-interest',
    interest_category_name: 'Loan interest',
    interest_category_icon: 'percent',
    interest_category_color: '#f43f5e',
  }

  beforeEach(() => {
    vi.mocked(transactions.list).mockResolvedValue({ items: [], total: 0 } as never)
    vi.mocked(dashboard.projectedTransactions).mockResolvedValue([loanPayment])
    vi.mocked(admin.accountingMode).mockResolvedValue({ mode: 'cash' } as never)
  })

  afterEach(() => {
    vi.resetAllMocks()
  })

  it('lists only the interest, in the interest category', async () => {
    renderWithProviders(
      <TransactionDrillDown
        filter={{ title: 'Expenses', type: 'debit', from: '2026-03-01', to: '2026-03-31' }}
        onClose={vi.fn()}
      />,
    )

    expect(await screen.findByText('Mortgage payment')).toBeInTheDocument()
    expect(screen.getByText(/Loan interest/)).toBeInTheDocument()
    expect(screen.getAllByText(/1,200\.00/).length).toBeGreaterThan(0)
    expect(screen.queryByText(/2,000\.00/)).not.toBeInTheDocument()
  })

  it('never shows the payment as an uncategorised expense', async () => {
    vi.mocked(dashboard.projectedTransactions).mockResolvedValue([
      loanPayment,
      { ...loanPayment, recurring_id: 'rec-2', description: 'Interest-free loan', interest_amount: 0 },
    ])
    renderWithProviders(
      <TransactionDrillDown
        filter={{ title: 'Uncategorized', uncategorized: true, from: '2026-03-01', to: '2026-03-31' }}
        onClose={vi.fn()}
      />,
    )

    await waitFor(() => expect(dashboard.projectedTransactions).toHaveBeenCalled())
    expect(await screen.findByText(t('dashboard.drillDownEmpty'))).toBeInTheDocument()
    expect(screen.queryByText('Mortgage payment')).not.toBeInTheDocument()
    expect(screen.queryByText('Interest-free loan')).not.toBeInTheDocument()
  })
})
