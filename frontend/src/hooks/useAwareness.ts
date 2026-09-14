import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import type { SenseActivity, SenseReport } from '../types'

/**
 * The client half of the agent's awareness: report our own senses upward.
 *
 * The server owns the clock and the diary, but it cannot know the three things
 * only the browser can see — which time zone the user is actually in, where
 * they are in the app, and whether the microphone is live. This hook watches
 * those and pushes a `sense` frame whenever they change.
 *
 * Two properties keep it cheap enough to leave running everywhere:
 *   - It is *change-driven*, not polled. There is no client-side timer at all;
 *     the server already pushes derived state on its own heartbeat, and
 *     `useChatSocket` replays the last report after a reconnect, so a periodic
 *     resend from here would be pure noise.
 *   - It carries no payload. A sense frame is a few dozen bytes of flags. The
 *     expensive senses (pixels, audio) never travel this path.
 */

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
    })
  }, [
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
