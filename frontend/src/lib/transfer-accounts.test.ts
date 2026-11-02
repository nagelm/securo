import { describe, expect, it } from 'vitest'
import { resolveTransferAccounts } from './transfer-accounts'

const current = { id: 'acc-current', name: 'Joint Everyday' }
const loan = { id: 'acc-loan', name: 'Home Loan B' }
const accounts = new Map([current, loan].map((a) => [a.id, a]))

const leg = (over: Partial<Parameters<typeof resolveTransferAccounts>[0]>) => ({
  type: 'debit' as const,
  account_id: current.id,
  transfer_pair_id: 'pair-1',
  transfer_counterpart_account_id: loan.id,
  ...over,
})

describe('resolveTransferAccounts', () => {
  it('reads the debit leg as from its own account to the other', () => {
    expect(resolveTransferAccounts(leg({}), accounts)).toEqual({ from: current, to: loan })
  })

  it('reads the credit leg the same way round: from the other account to its own', () => {
    const credit = leg({ type: 'credit', account_id: loan.id, transfer_counterpart_account_id: current.id })
    expect(resolveTransferAccounts(credit, accounts)).toEqual({ from: current, to: loan })
  })

  it('is null for a row that is not a paired leg', () => {
    expect(resolveTransferAccounts(leg({ transfer_pair_id: null }), accounts)).toBeNull()
  })

  it('is null when the other leg is unknown', () => {
    expect(resolveTransferAccounts(leg({ transfer_counterpart_account_id: null }), accounts)).toBeNull()
    expect(resolveTransferAccounts(leg({ transfer_counterpart_account_id: undefined }), accounts)).toBeNull()
  })

  it('is null when either account is not in the loaded list', () => {
    expect(resolveTransferAccounts(leg({ transfer_counterpart_account_id: 'acc-closed' }), accounts)).toBeNull()
    expect(resolveTransferAccounts(leg({ account_id: 'acc-closed' }), accounts)).toBeNull()
  })
})

describe('resolveTransferAccounts with a closed account', () => {
  const closed = {
    id: 'acc-closed',
    name: 'Old Savings',
    display_name: null,
    type: 'savings',
    institution_logo_url: null,
    is_closed: true,
  }

  it('falls back to the summary the API sent for the other leg', () => {
    const debit = leg({ transfer_counterpart_account_id: closed.id, transfer_counterpart_account: closed })
    expect(resolveTransferAccounts(debit, accounts)).toEqual({ from: current, to: closed })
  })

  it('falls back to the summary the API sent for the own account of the row', () => {
    const credit = leg({
      type: 'credit',
      account_id: closed.id,
      transfer_account: closed,
      transfer_counterpart_account_id: current.id,
    })
    expect(resolveTransferAccounts(credit, accounts)).toEqual({ from: current, to: closed })
  })

  it('prefers the loaded account over the summary', () => {
    const stale = { ...closed, id: loan.id, name: 'Stale name' }
    expect(resolveTransferAccounts(leg({ transfer_counterpart_account: stale }), accounts)?.to).toBe(loan)
  })
})
