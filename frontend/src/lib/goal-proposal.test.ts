import { describe, expect, it } from 'vitest'

import { goalProposalPayload } from '@/lib/goal-proposal'

describe('goalProposalPayload', () => {
  it('sends the proposal as the goal API body', () => {
    expect(
      goalProposalPayload({
        name: 'Pet fund',
        target_amount: null,
        contribution_amount: 15,
        contribution_period: 'biweekly',
        currency: 'NZD',
        target_date: '2026-12-01',
        tracking_type: 'account',
        account_id: 'acc-1',
        current_amount: 0,
        icon: 'piggy-bank',
        color: '#3B82F6',
      }),
    ).toEqual({
      name: 'Pet fund',
      target_amount: null,
      contribution_amount: 15,
      contribution_period: 'biweekly',
      currency: 'NZD',
      target_date: '2026-12-01',
      tracking_type: 'account',
      account_id: 'acc-1',
      current_amount: 0,
      icon: 'piggy-bank',
      color: '#3B82F6',
    })
  })

  it('reads an older proposal: deadline is the target date, initial_amount what is saved', () => {
    const body = goalProposalPayload({
      name: 'Laptop',
      target_amount: 2000,
      currency: 'BRL',
      deadline: '2026-12-31',
      initial_amount: 300,
    })
    expect(body).toMatchObject({
      target_amount: 2000,
      target_date: '2026-12-31',
      tracking_type: 'manual',
      account_id: null,
      current_amount: 300,
      contribution_amount: null,
      contribution_period: null,
    })
    expect(body).not.toHaveProperty('deadline')
    expect(body).not.toHaveProperty('initial_amount')
  })
})
