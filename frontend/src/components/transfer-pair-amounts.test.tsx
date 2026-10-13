import { describe, expect, it, vi } from 'vitest'
import { screen } from '@testing-library/react'

import { MobileTransactionRow } from '@/components/mobile-transaction-row'
import { TransferPairAmounts } from '@/components/transfer-pair-amounts'
import { renderWithProviders } from '@/test/utils'
import { resolveTransferAccounts } from '@/lib/transfer-accounts'
import type { Account, Transaction } from '@/types'

const everyday = {
  id: 'acc-everyday',
  name: 'Everyday 001',
  display_name: 'Everyday',
  type: 'checking',
  institution_logo_url: null,
} as Account
const savings = {
  id: 'acc-savings',
  name: 'Savings 002',
  display_name: null,
  type: 'savings',
  institution_logo_url: null,
} as Account

const leg = (overrides: Partial<Transaction>): Transaction => ({
  id: 'leg',
  account_id: everyday.id,
  description: 'TRANSFER',
  amount: 250,
  currency: 'USD',
  type: 'debit',
  date: '2026-03-10',
  transfer_pair_id: 'pair-1',
  amount_primary: null,
  is_ignored: false,
  splits: [],
  ...overrides,
} as unknown as Transaction)

const outLeg = leg({ id: 'tx-out', account_id: everyday.id, type: 'debit', transfer_counterpart_account_id: savings.id })
const inLeg = leg({ id: 'tx-in', account_id: savings.id, type: 'credit', transfer_counterpart_account_id: everyday.id })

describe('TransferPairAmounts', () => {
  it('shows the amount out of the source and into the destination', () => {
    renderWithProviders(
      <TransferPairAmounts out={outLeg} in={inLeg} fromName="Everyday" toName="Savings 002" locale="en-US" dateLocale="en-US" />,
    )

    const outLine = screen.getByText('−$250.00')
    const inLine = screen.getByText('+$250.00')
    expect(outLine.closest('[title]')).toHaveClass('text-rose-500')
    expect(inLine.closest('[title]')).toHaveClass('text-emerald-600')
  })

  it('names each leg for tooltips and screen readers', () => {
    renderWithProviders(
      <TransferPairAmounts out={outLeg} in={inLeg} fromName="Everyday" toName="Savings 002" locale="en-US" dateLocale="en-US" />,
    )

    expect(screen.getByTitle('Out of Everyday')).toHaveTextContent('Out of Everyday: −$250.00')
    expect(screen.getByTitle('Into Savings 002')).toHaveTextContent('Into Savings 002: +$250.00')
    expect(screen.getByText('Out of Everyday:', { exact: false })).toHaveClass('sr-only')
  })

  it('shows each currency of a cross-currency transfer', () => {
    renderWithProviders(
      <TransferPairAmounts
        out={{ ...outLeg, amount: 100, currency: 'USD' }}
        in={{ ...inLeg, amount: 92.5, currency: 'EUR' }}
        fromName="Everyday"
        toName="Savings 002"
        locale="en-US"
        dateLocale="en-US"
      />,
    )

    expect(screen.getByText('−$100.00')).toBeInTheDocument()
    expect(screen.getByText('+€92.50')).toBeInTheDocument()
  })

  it('says when the incoming leg landed on another day', () => {
    renderWithProviders(
      <TransferPairAmounts
        out={outLeg}
        in={{ ...inLeg, date: '2026-03-12' }}
        fromName="Everyday"
        toName="Savings 002"
        locale="en-US"
        dateLocale="en-US"
      />,
    )

    expect(screen.getByTitle('Into Savings 002 · Received 3/12/2026')).toBeInTheDocument()
  })
})

describe('MobileTransactionRow drawing a whole transfer', () => {
  const accounts = new Map([[everyday.id, everyday], [savings.id, savings]])

  const renderRow = (props: { transferPairIn: Transaction | null; onSelect?: (id: string) => void; onClick?: (tx: Transaction) => void }) =>
    renderWithProviders(
      <MobileTransactionRow
        tx={outLeg}
        account={everyday}
        transferAccounts={resolveTransferAccounts(outLeg, accounts)}
        transferPairIn={props.transferPairIn}
        groupName={undefined}
        selected={false}
        selectable
        canWrite
        highlighted={false}
        locale="en-US"
        userCurrency="USD"
        onSelect={props.onSelect ?? (() => {})}
        onClick={props.onClick ?? (() => {})}
      />,
    )

  it('shows the pill and both amounts', () => {
    renderRow({ transferPairIn: inLeg })

    expect(screen.getByText('Transfer from Everyday to Savings 002')).toBeInTheDocument()
    expect(screen.getByTitle('Out of Everyday')).toHaveTextContent('−$250.00')
    expect(screen.getByTitle('Into Savings 002')).toHaveTextContent('+$250.00')
  })

  it('labels the checkbox as selecting both sides and reports the outgoing leg', async () => {
    const onSelect = vi.fn()
    const onClick = vi.fn()
    const { user } = renderRow({ transferPairIn: inLeg, onSelect, onClick })

    await user.click(screen.getByRole('checkbox', { name: 'Select both sides of this transfer' }))
    expect(onSelect).toHaveBeenCalledWith('tx-out', false)

    await user.click(screen.getByText('Transfer from Everyday to Savings 002'))
    expect(onClick).toHaveBeenCalledWith(outLeg)
  })

  it('keeps the single-leg amount when the other leg is not loaded', () => {
    renderRow({ transferPairIn: null })

    expect(screen.getByText('−$250.00')).toBeInTheDocument()
    expect(screen.queryByText('+$250.00')).toBeNull()
    expect(screen.queryByRole('checkbox', { name: 'Select both sides of this transfer' })).toBeNull()
  })
})
