import { createElement, type ReactNode } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SortableAccountList } from './sortable-account-list'

// Drag and drop itself is dnd-kit's; jsdom has no layout to drag in. The
// provider is replaced by buttons that end a drag the way dnd-kit reports
// one, so these tests cover what the list does with the result. The real
// `move` helper from @dnd-kit/helpers turns the event into the new order.

type DragEnd = (event: unknown) => void

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string, values?: { name?: string }) => (values?.name ? `${key}:${values.name}` : key) }),
}))
vi.mock('@dnd-kit/react/sortable', () => ({
  useSortable: () => ({ ref: () => {}, handleRef: () => {}, isDragging: false }),
}))
vi.mock('@dnd-kit/react', () => ({
  DragDropProvider: ({ children, onDragEnd }: { children: ReactNode; onDragEnd: DragEnd }) =>
    createElement(
      'div',
      {},
      // Sortable reports the dragged item as both source and target once it
      // has projected the new index.
      createElement('button', {
        onClick: () => onDragEnd({
          canceled: false,
          operation: { source: { id: 'cash', initialIndex: 2, index: 0 }, target: { id: 'cash', index: 0 }, canceled: false },
        }),
      }, 'drop cash first'),
      createElement('button', {
        onClick: () => onDragEnd({
          canceled: true,
          operation: { source: { id: 'cash', initialIndex: 2, index: 0 }, target: { id: 'cash', index: 0 }, canceled: true },
        }),
      }, 'cancel drag'),
      createElement('button', {
        onClick: () => onDragEnd({
          canceled: false,
          operation: { source: { id: 'wallet', initialIndex: 0, index: 0 }, target: { id: 'wallet', index: 0 }, canceled: false },
        }),
      }, 'drop in place'),
      children,
    ),
}))

const accounts = [
  { id: 'wallet', name: 'Wallet' },
  { id: 'savings', name: 'Savings jar' },
  { id: 'cash', name: 'Cash box' },
]

const onReorder = vi.fn()

function renderList(enabled = true, list = accounts) {
  return render(
    <SortableAccountList
      accounts={list}
      enabled={enabled}
      onReorder={onReorder}
      label={(a) => a.name}
      renderRow={(account, controls) => (
        <div>
          {controls.handle}
          <span>{account.name}</span>
          {controls.reorderable && (
            <>
              <button type="button" disabled={!controls.moveUp} onClick={controls.moveUp}>{`up ${account.name}`}</button>
              <button type="button" disabled={!controls.moveDown} onClick={controls.moveDown}>{`down ${account.name}`}</button>
            </>
          )}
        </div>
      )}
    />,
  )
}

beforeEach(() => onReorder.mockClear())

describe('SortableAccountList', () => {
  it('saves the order a drop leaves', () => {
    renderList()
    fireEvent.click(screen.getByRole('button', { name: 'drop cash first' }))
    expect(onReorder).toHaveBeenCalledWith(['cash', 'wallet', 'savings'])
  })

  it('saves nothing for a cancelled drag or a drop in place', () => {
    renderList()
    fireEvent.click(screen.getByRole('button', { name: 'cancel drag' }))
    fireEvent.click(screen.getByRole('button', { name: 'drop in place' }))
    expect(onReorder).not.toHaveBeenCalled()
  })

  it('moves one place with the move buttons, disabled at the ends', () => {
    renderList()
    fireEvent.click(screen.getByRole('button', { name: 'up Savings jar' }))
    expect(onReorder).toHaveBeenLastCalledWith(['savings', 'wallet', 'cash'])
    fireEvent.click(screen.getByRole('button', { name: 'down Savings jar' }))
    expect(onReorder).toHaveBeenLastCalledWith(['wallet', 'cash', 'savings'])
    expect(screen.getByRole('button', { name: 'up Wallet' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'down Cash box' })).toBeDisabled()
  })

  it('labels each drag handle with the account', () => {
    renderList()
    expect(screen.getByRole('button', { name: 'accounts.dragToReorder:Wallet' })).toBeInTheDocument()
  })

  it('offers no reordering when disabled', () => {
    renderList(false)
    fireEvent.click(screen.getByRole('button', { name: 'drop cash first' }))
    expect(onReorder).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /accounts.dragToReorder/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^up / })).not.toBeInTheDocument()
  })

  it('offers no reordering for a group of one', () => {
    renderList(true, accounts.slice(0, 1))
    expect(screen.queryByRole('button', { name: /accounts.dragToReorder/ })).not.toBeInTheDocument()
  })
})
