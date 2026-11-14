import { useEffect, useState } from 'react'
import { mindApi } from '../api/mind'
import type { MindState } from '../types'

// Her mind's continuity, polled. Null until the first load, and when the request fails.
const POLL_MS = 15000

// ``enabled``: only for the owner (the route refuses everyone else).
export function useMindState(enabled = true): MindState | null {
  const [state, setState] = useState<MindState | null>(null)

  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    const load = () => mindApi.state()
      .then((s) => { if (!cancelled) setState(s) })
      .catch(() => {})
    load()
    const timer = window.setInterval(load, POLL_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [enabled])

  return state
}
