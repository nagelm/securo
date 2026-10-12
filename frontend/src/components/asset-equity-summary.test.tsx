import { describe, expect, it } from 'vitest'
import { screen } from '@testing-library/react'

import { AssetEquityLine, AssetEquitySummary } from '@/components/asset-equity-summary'
import { renderWithProviders, t } from '@/test/utils'
import type { Asset } from '@/types'

// Made-up figures: house 500,000, mortgage owing 300,000.
function house(overrides: Partial<Asset> = {}): Asset {
  return {
    id: 'asset-1',
    user_id: 'user-1',
    name: 'Test house',
    type: 'real_estate',
    currency: 'USD',
    units: null,
    valuation_method: 'manual',
    purchase_date: null,
    purchase_price: null,
    sell_date: null,
    sell_price: null,
    growth_type: null,
    growth_rate: null,
    growth_frequency: null,
    growth_start_date: null,
    is_archived: false,
    position: 0,
    current_value: 500000,
    current_value_primary: null,
    gain_loss: null,
    gain_loss_primary: null,
    value_count: 1,
    source: 'manual',
    connection_id: null,
    isin: null,
    maturity_date: null,
    group_id: null,
    ticker: null,
    ticker_exchange: null,
    last_price: null,
    last_price_at: null,
    logo_url: null,
    average_price: null,
    total_invested: null,
    realized_gain: null,
    transaction_count: 0,
    linked_loan_ids: ['loan-1'],
    linked_loans: [
      { id: 'loan-1', name: 'Test mortgage', currency: 'USD', balance: -300000, debt: 300000 },
    ],
    linked_debt: 300000,
    linked_debt_primary: null,
    equity: 200000,
    equity_primary: null,
    ...overrides,
  }
}

const unlinked = {
  linked_loan_ids: [],
  linked_loans: [],
  linked_debt: null,
  equity: null,
}

describe('AssetEquityLine', () => {
  it('shows value, debt and equity from the backend figures', () => {
    renderWithProviders(<AssetEquityLine asset={house()} locale="en-US" />)

    const line = screen.getByTestId('asset-equity-line')
    expect(line).toHaveTextContent('Value $500,000.00')
    expect(line).toHaveTextContent('Debt $300,000.00')
    expect(line).toHaveTextContent('Equity $200,000.00')
  })

  it('renders nothing for an asset with no linked loan', () => {
    renderWithProviders(<AssetEquityLine asset={house(unlinked)} locale="en-US" />)

    expect(screen.queryByTestId('asset-equity-line')).not.toBeInTheDocument()
  })

  it('shows a dash when the backend has no equity (no value, archived or sold)', () => {
    renderWithProviders(
      <AssetEquityLine asset={house({ current_value: null, equity: null })} locale="en-US" />,
    )

    expect(screen.getByTestId('asset-equity-line')).toHaveTextContent('Equity —')
  })

  it('masks amounts in privacy mode', () => {
    renderWithProviders(
      <AssetEquityLine asset={house()} locale="en-US" mask={() => '•••'} />,
    )

    const line = screen.getByTestId('asset-equity-line')
    expect(line).not.toHaveTextContent('300,000')
    expect(line).toHaveTextContent('Debt •••')
  })
})

describe('AssetEquitySummary', () => {
  it('links each loan to its account page with its debt', () => {
    renderWithProviders(
      <AssetEquitySummary
        asset={house({
          linked_loan_ids: ['loan-1', 'loan-2'],
          linked_loans: [
            { id: 'loan-1', name: 'Test mortgage', currency: 'USD', balance: -250000, debt: 250000 },
            { id: 'loan-2', name: 'Test top-up', currency: 'USD', balance: -50000, debt: 50000 },
          ],
        })}
        locale="en-US"
      />,
    )

    expect(screen.getByRole('region', { name: 'Equity' })).toBeInTheDocument()
    expect(screen.getByText('Loans secured against this asset')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Test mortgage' })).toHaveAttribute('href', '/accounts/loan-1')
    expect(screen.getByRole('link', { name: 'Test top-up' })).toHaveAttribute('href', '/accounts/loan-2')
    expect(screen.getByText('$250,000.00')).toBeInTheDocument()
    expect(screen.getByText('$50,000.00')).toBeInTheDocument()
    expect(screen.getByText('$200,000.00')).toBeInTheDocument()
    expect(screen.getByText(t('assets.equity.note'))).toBeInTheDocument()
  })

  it('renders nothing for an asset with no linked loan', () => {
    renderWithProviders(<AssetEquitySummary asset={house(unlinked)} locale="en-US" />)

    expect(screen.queryByRole('region')).not.toBeInTheDocument()
  })
})

describe('assets.equity copy', () => {
  it('ships English strings for the delete warning', () => {
    expect(t('assets.equity.deleteUnlinksLoans', { count: 1 })).toBe(
      '1 loan is secured against this asset. Deleting the asset unlinks it; the loan and its balance stay.',
    )
    expect(t('assets.equity.deleteUnlinksLoans', { count: 2 })).toMatch(/^2 loans are secured/)
  })
})
