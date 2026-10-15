import { useState } from 'react'

/**
 * Which series of a chart the user has switched off by clicking its legend.
 *
 * `keys` is the chart's current series, in any order. Hidden keys that are no
 * longer among them are dropped when the series change, so a hide never
 * outlives the series it was for. The last visible series can't be hidden:
 * a chart with nothing on it has nothing to click back on but the legend.
 */
export interface SeriesToggle {
  /** True when `key` is switched off. */
  isHidden: (key: string) => boolean
  /** Switch `key` off or back on. Does nothing for an unknown key or the last visible one. */
  toggle: (key: string) => void
  /** False when `key` is the only series still shown. */
  canHide: (key: string) => boolean
  /** The keys still shown, in the order they were given. */
  visibleKeys: string[]
  /** True when at least one series is switched off. */
  anyHidden: boolean
}

const EMPTY: ReadonlySet<string> = new Set()

interface State {
  signature: string
  hidden: ReadonlySet<string>
}

export function useSeriesToggle(keys: readonly string[]): SeriesToggle {
  const signature = keys.join('\u0000')
  const [state, setState] = useState<State>(() => ({ signature, hidden: EMPTY }))

  let hidden = state.hidden
  if (state.signature !== signature) {
    // The series changed: keep the hides that still apply, and never carry
    // over a set that would hide everything.
    const kept = new Set(keys.filter((k) => state.hidden.has(k)))
    hidden = kept.size === 0 || kept.size >= keys.length ? EMPTY : kept
    setState({ signature, hidden })
  }

  const visibleKeys = keys.filter((k) => !hidden.has(k))

  const toggle = (key: string) => {
    if (!keys.includes(key)) return
    setState((prev) => {
      const next = new Set(prev.hidden)
      if (next.has(key)) {
        next.delete(key)
      } else {
        const stillShown = keys.filter((k) => !next.has(k)).length
        if (stillShown <= 1) return prev
        next.add(key)
      }
      return { signature: prev.signature, hidden: next }
    })
  }

  return {
    isHidden: (key) => hidden.has(key),
    toggle,
    canHide: (key) => !hidden.has(key) && visibleKeys.length > 1,
    visibleKeys,
    anyHidden: hidden.size > 0,
  }
}
