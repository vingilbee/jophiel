import { useEffect, useRef, useState } from 'react'
import { systemApi } from '../api/system'
import type { Signal } from '../types/system'

const KEEP = 60
/** On opening the page, what happened in this window is replayed quickly, so the field isn't still. */
const REPLAY_SECONDS = 90
const REPLAY_GAP_MS = 160

/** Hand-offs between her parts: a short replay of the last minute and a half, then the live stream. */
export function useSignals() {
  const [signals, setSignals] = useState<Signal[]>([])
  const [live, setLive] = useState(false)
  const timers = useRef<number[]>([])

  useEffect(() => {
    let closed = false
    const add = (s: Signal) => setSignals((list) => (list.some((x) => x.id === s.id) ? list : [...list, s].slice(-KEEP)))

    void systemApi.signals(KEEP).then(
      (recent) => {
        if (closed) return
        const now = Date.now() / 1000
        recent
          .filter((s) => now - s.at < REPLAY_SECONDS)
          .forEach((s, i) => timers.current.push(window.setTimeout(() => !closed && add(s), 400 + i * REPLAY_GAP_MS)))
      },
      () => {},
    )

    const source = new EventSource(systemApi.signalStreamUrl, { withCredentials: true })
    source.onopen = () => setLive(true)
    source.onerror = () => setLive(false)
    source.onmessage = (e) => {
      try {
        add(JSON.parse(e.data) as Signal)
      } catch {
        // a malformed event is skipped; the next one still arrives
      }
    }
    return () => {
      closed = true
      source.close()
      timers.current.forEach((t) => window.clearTimeout(t))
      timers.current = []
    }
  }, [])

  return { signals, live }
}
