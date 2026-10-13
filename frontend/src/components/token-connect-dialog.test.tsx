import { screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { renderWithProviders } from '@/test/utils'
import { TokenConnectDialog } from './token-connect-dialog'

vi.mock('@/lib/api', () => ({ connections: { handleCallback: vi.fn() } }))

it('points an Akahu reconnect at the Akahu connections page', () => {
  renderWithProviders(<TokenConnectDialog open provider="akahu" reconnectConnectionId="synthetic" onClose={vi.fn()} />)
  expect(screen.getByText('Reconnect Akahu')).toBeInTheDocument()
  expect(screen.getByText(/lost access to one of your banks/)).toBeInTheDocument()
  const link = screen.getByRole('link', { name: 'Open your Akahu connections' })
  expect(link).toHaveAttribute('href', 'https://my.akahu.nz/connections')
  expect(screen.getByRole('link', { name: 'Open the Akahu Developers page' })).toBeInTheDocument()
})

it('offers no connections link when first connecting Akahu', () => {
  renderWithProviders(<TokenConnectDialog open provider="akahu" onClose={vi.fn()} />)
  expect(screen.getByText('Connect via Akahu')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Open your Akahu connections' })).not.toBeInTheDocument()
})

it('keeps the generic reconnect text and no connections link for SimpleFIN', () => {
  renderWithProviders(<TokenConnectDialog open provider="simplefin" reconnectConnectionId="synthetic" onClose={vi.fn()} />)
  expect(screen.getByText('Reconnect with a fresh token')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: /Akahu connections/ })).not.toBeInTheDocument()
})
