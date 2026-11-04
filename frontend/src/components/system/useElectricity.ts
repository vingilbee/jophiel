import { useEffect, useRef } from 'react'
import type { PartState } from '../../types/system'
import { PERIOD } from './nodeStates'
import { burst, reformArcs } from './sparks'

const TICK_MS = 110

function reducedMotion() {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** A node's electricity: living arcs that re-form every tick, a burst on each discharge of its beat (in step
 * with the ring's CSS animation, which shares ``delay``) and one each time ``hits`` goes up (a pulse arrived).
 * Returns the two groups to render, arcs under the ring and bursts above the core. */
export function useElectricity(o: {
  state: PartState | undefined
  color: string
  r: number
  ringR: number
  line: number
  delay: number
  hits: number
  paused: boolean
}) {
  const arcs = useRef<SVGGElement>(null)
  const bursts = useRef<SVGGElement>(null)
  const latest = useRef(o)
  useEffect(() => {
    latest.current = o
  })

  useEffect(() => {
    if (o.paused) {
      arcs.current?.replaceChildren()
      return
    }
    if (reducedMotion()) {
      // Still: one set of arcs that stays, so the node still reads as electric.
      const { state, color, r, ringR, line } = latest.current
      if (arcs.current) reformArcs(arcs.current, state, color, r, ringR, line)
      return
    }
    const start = performance.now()
    let last = -1
    const id = window.setInterval(() => {
      const { state, color, r, ringR, line, delay } = latest.current
      if (arcs.current) reformArcs(arcs.current, state, color, r, ringR, line)
      const period = state ? PERIOD[state] : undefined
      if (!period || !bursts.current) return
      const phase = ((((performance.now() - start) / 1000 + delay) % period) + period) % period / period
      if (last >= 0 && last < 0.86 && phase >= 0.86) burst(bursts.current, state, color, r, ringR, line)
      last = phase
    }, TICK_MS)
    return () => window.clearInterval(id)
  }, [o.paused, o.state])

  useEffect(() => {
    const { state, color, r, ringR, line, paused } = latest.current
    if (o.hits > 0 && bursts.current && !paused && !reducedMotion()) burst(bursts.current, state, color, r, ringR, line)
  }, [o.hits])

  return { arcs, bursts }
}
