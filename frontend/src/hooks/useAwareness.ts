import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import type { SenseActivity, SenseReport } from '../types'

/**
 * The client half of the agent's awareness: report our own senses upward.
 *
 * The server owns the clock and the diary, but it cannot know the things
 * only the browser can see — which time zone the user is actually in, where
 * they are in the app, whether the microphone is live, and whether the user is
 * here at all (tab on screen, last input: presence, for phone pushes). This hook watches
 * those and pushes a `sense` frame whenever they change.
 *
 * Two properties keep it cheap enough to leave running everywhere:
 *   - It is *change-driven*, not polled. There is no client-side timer at all
 *     (input only moves `last_active_at` once per 30 s);
 *     the server already pushes derived state on its own heartbeat, and
 *     `useChatSocket` replays the last report after a reconnect, so a periodic
 *     resend from here would be pure noise.
 *   - It carries no payload. A sense frame is a few dozen bytes of flags. The
 *     expensive senses (pixels, audio) never travel this path.
 */

// Input is reported at most this often: enough for "used in the last two minutes" (services/presence.py).
const ACTIVE_REPORT_MS = 30_000
const INPUT_EVENTS = ['pointerdown', 'pointermove', 'keydown', 'wheel', 'touchstart'] as const

/**
 * Presence: whether this tab is on screen, and when the user last touched it. The backend sends her messages
 * to the phone only when the user is at no app, so an open tab nobody is at must not count.
 */
function usePresence() {
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible')
  const [lastActiveAt, setLastActiveAt] = useState(() => new Date().toISOString())
  const lastReported = useRef(Date.now())

  useEffect(() => {
    const onVisibility = () => {
      const nowVisible = document.visibilityState === 'visible'
      setVisible(nowVisible)
      if (nowVisible) {
        lastReported.current = Date.now()
        setLastActiveAt(new Date().toISOString())
      }
    }
    const onInput = () => {
      const now = Date.now()
      if (now - lastReported.current < ACTIVE_REPORT_MS) return
      lastReported.current = now
      setLastActiveAt(new Date(now).toISOString())
    }
    document.addEventListener('visibilitychange', onVisibility)
    for (const name of INPUT_EVENTS) window.addEventListener(name, onInput, { passive: true })
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      for (const name of INPUT_EVENTS) window.removeEventListener(name, onInput)
    }
  }, [])

  return { visible, lastActiveAt }
}

interface Options {
  sendSense: (report: SenseReport) => void
  /** What the user is visibly doing right now. */
  activity?: SenseActivity
  hearingEnabled?: boolean
  wakeArmed?: boolean
  voiceState?: SenseReport['voice_state']
  /** Screen sharing is on (the eye toggle). */
  visionEnabled?: boolean
  /** Newest screen frame in the image store — an id, never pixels. */
  screenImageId?: string | null
}

export function useAwareness({
  sendSense,
  activity = 'idle',
  hearingEnabled = false,
  wakeArmed = false,
  voiceState = null,
  visionEnabled = false,
  screenImageId = null,
}: Options) {
  const { pathname } = useLocation()
  const { visible, lastActiveAt } = usePresence()
  // `sendSense` is stable, but taking it through a ref keeps it out of the dep
  // list so the effect fires on sense changes only.
  const sendRef = useRef(sendSense)
  sendRef.current = sendSense

  useEffect(() => {
    sendRef.current({
      // Read at report time, not once on mount: it's a cheap lookup, and it's
      // the only way a laptop that crossed a time zone mid-session corrects
      // itself.
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone ?? null,
      route: pathname,
      activity,
      hearing_enabled: hearingEnabled,
      wake_armed: wakeArmed,
      voice_state: voiceState,
      vision_enabled: visionEnabled,
      screen_image_id: screenImageId,
      visible,
      last_active_at: lastActiveAt,
    })
  }, [
    visible,
    lastActiveAt,
    pathname,
    activity,
    hearingEnabled,
    wakeArmed,
    voiceState,
    visionEnabled,
    // Every new frame is a new id, so this is what drives the eye's updates —
    // roughly once per real change on screen, not once per captured frame.
    screenImageId,
  ])
}
