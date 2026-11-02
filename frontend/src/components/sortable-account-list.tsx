import type { ReactNode } from 'react'
import { DragDropProvider } from '@dnd-kit/react'
import { useSortable } from '@dnd-kit/react/sortable'
import { move } from '@dnd-kit/helpers'
import { GripVertical } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { moveId } from '@/lib/account-utils'

/** What a row needs to offer reordering: a drag handle and move up/down. */
export type AccountReorderControls = {
  /** Drag handle to place at the start of the row; null when reordering is off. */
  handle: ReactNode
  /** Move one place up/down; undefined at that end of the list or when reordering is off. */
  moveUp?: () => void
  moveDown?: () => void
  /** True when the row can be moved at all (shows the move buttons, disabled at the ends). */
  reorderable: boolean
}

type SortableAccountListProps<T extends { id: string }> = {
  accounts: readonly T[]
  /** Off for read-only members and while a reorder is being saved. */
  enabled: boolean
  /** The list's ids in their new order. */
  onReorder: (ids: string[]) => void
  /** The name the user sees, for the handle's label. */
  label: (account: T) => string
  renderRow: (account: T, controls: AccountReorderControls) => ReactNode
}

function SortableRow({ id, index, disabled, children }: {
  id: string
  index: number
  disabled: boolean
  children: (handleRef: (element: Element | null) => void) => ReactNode
}) {
  const { ref, handleRef, isDragging } = useSortable({ id, index, disabled })
  return (
    <div ref={ref} className={isDragging ? 'relative z-10 bg-card opacity-70 shadow-md' : undefined}>
      {children(handleRef)}
    </div>
  )
}

/**
 * One group of accounts the user can put in their own order: drag a row by its
 * handle (mouse, touch, or the handle's keyboard controls) or use the row's
 * move up/down buttons. Rows never leave the group, and only the group's ids
 * are sent, so other groups keep their places.
 */
export function SortableAccountList<T extends { id: string }>({
  accounts,
  enabled,
  onReorder,
  label,
  renderRow,
}: SortableAccountListProps<T>) {
  const { t } = useTranslation()
  const ids = accounts.map((account) => account.id)
  const reorderable = enabled && accounts.length > 1

  const reorder = (next: string[]) => {
    if (next.some((id, i) => id !== ids[i])) onReorder(next)
  }

  return (
    <DragDropProvider
      onDragEnd={(event) => {
        if (event.canceled || !reorderable) return
        reorder(move(ids, event))
      }}
    >
      {accounts.map((account, index) => (
        <SortableRow key={account.id} id={account.id} index={index} disabled={!reorderable}>
          {(handleRef) =>
            renderRow(account, {
              reorderable,
              handle: reorderable ? (
                <button
                  ref={handleRef}
                  type="button"
                  className="mr-1 -ml-2 shrink-0 cursor-grab touch-none rounded p-1 text-muted-foreground/60 hover:text-foreground active:cursor-grabbing"
                  aria-label={t('accounts.dragToReorder', { name: label(account) })}
                  title={t('accounts.dragToReorder', { name: label(account) })}
                >
                  <GripVertical size={14} />
                </button>
              ) : null,
              moveUp: reorderable && index > 0 ? () => reorder(moveId(ids, index, index - 1)) : undefined,
              moveDown:
                reorderable && index < accounts.length - 1
                  ? () => reorder(moveId(ids, index, index + 1))
                  : undefined,
            })
          }
        </SortableRow>
      ))}
    </DragDropProvider>
  )
}
