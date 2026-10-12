import { describe, expect, it } from 'vitest'
import { fireEvent } from '@testing-library/react'

import { PayeeIcon } from '@/components/payee-icon'
import { renderWithProviders } from '@/test/utils'

const LOGO = 'https://logo.example/shop.png'

describe('PayeeIcon', () => {
  it('shows the picked icon over the logo', () => {
    const { container } = renderWithProviders(
      <PayeeIcon icon="tv" logoUrl={LOGO} fallbackIcon="shopping-cart" />,
    )

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).toHaveClass('lucide-tv')
  })

  it('shows the logo when no icon was picked', () => {
    const { container } = renderWithProviders(
      <PayeeIcon logoUrl={LOGO} fallbackIcon="shopping-cart" />,
    )

    expect(container.querySelector('img')).toHaveAttribute('src', LOGO)
  })

  it('falls back to the category icon when the logo fails to load', () => {
    const { container } = renderWithProviders(
      <PayeeIcon logoUrl={LOGO} fallbackIcon="shopping-cart" />,
    )

    fireEvent.error(container.querySelector('img')!)

    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('svg')).toHaveClass('lucide-shopping-cart')
  })

  it('shows the category icon when the payee has neither', () => {
    const { container } = renderWithProviders(<PayeeIcon fallbackIcon="shopping-cart" />)

    expect(container.querySelector('svg')).toHaveClass('lucide-shopping-cart')
  })
})
