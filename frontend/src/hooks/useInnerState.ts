import { useEffect, useState } from 'react'
import { mindApi } from '../api/mind'
import type { InnerState } from '../types'

// Her inner state (docs/COLUMBINA_INNER_STATE_PLAN.md step 5.2), polled. Null until the first load,
// and when the request fails (her inner state is always on).
const POLL_MS = 15000

export function useInnerState(): InnerState | null {
  const [state, setState] = useState<InnerState | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = () => mindApi.innerState()
      .then((s) => { if (!cancelled) setState(s) })
      .catch(() => {})
    load()
    const timer = window.setInterval(load, POLL_MS)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [])

  return state
}
