import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { TransferAccountsPill } from '@/components/transfer-accounts-pill'
import { renderWithProviders } from '@/test/utils'
import type { Account } from '@/types'

const LOGO = 'https://logo.example/bank.png'

const current = {
  id: 'acc-current',
  name: 'CHQ 00-1234',
  display_name: 'Joint Everyday',
  type: 'checking',
  institution_logo_url: LOGO,
} as Account
const loan = {
  id: 'acc-loan',
  name: 'Home Loan B',
  display_name: null,
  type: 'loan',
  institution_logo_url: null,
} as Account

describe('TransferAccountsPill', () => {
  it('names both accounts by their display name, falling back to the name', () => {
    renderWithProviders(<TransferAccountsPill from={current} to={loan} />)

    expect(screen.getByText('Joint Everyday')).toBeInTheDocument()
    expect(screen.getByText('Home Loan B')).toBeInTheDocument()
    expect(screen.queryByText('CHQ 00-1234')).toBeNull()
  })

  it('reads as one sentence to a screen reader', () => {
    renderWithProviders(<TransferAccountsPill from={current} to={loan} />)

    expect(screen.getByText('Transfer from Joint Everyday to Home Loan B')).toHaveClass('sr-only')
    expect(screen.getByText('Joint Everyday')).toHaveAttribute('aria-hidden', 'true')
  })

  it('shows each account thumbnail', () => {
    const { container } = renderWithProviders(<TransferAccountsPill from={current} to={loan} />)

    expect(container.querySelector('img')).toHaveAttribute('src', LOGO)
  })

  it('keeps the description and the transfer hint in the tooltip', () => {
    const { container } = renderWithProviders(
      <TransferAccountsPill from={current} to={loan} description="LOAN PAYMENT 991" />,
    )

    const title = container.firstElementChild?.getAttribute('title') ?? ''
    expect(title).toContain('Transfer from Joint Everyday to Home Loan B')
    expect(title).toContain('LOAN PAYMENT 991')
    expect(title).toContain('Transfers are excluded from report and dashboard totals')
  })
})
