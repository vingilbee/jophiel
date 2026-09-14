import { useCallback, useEffect, useRef, useState } from 'react'
import { uploadImage } from '../api/images'
import { startScreenSense, type ScreenSense } from '../senses/screen'

/**
 * The eye toggle: owns the screen-sharing stream and the id of the newest frame.
 *
 * Deliberately not started automatically, ever. `getDisplayMedia` requires a
 * user gesture and shows the browser's own picker, and that is the consent
 * step — the app should not be able to route around it, and this hook doesn't
 * try to.
 *
 * The upload is fire-and-forget per frame: a frame that fails to reach the
 * server is simply skipped, because another one is along in a couple of
 * seconds and a stale screen is worse than a missing one.
 */

export type EyeStatus = 'closed' | 'opening' | 'open'

export interface ScreenSenseState {
  status: EyeStatus
  /** Id of the newest frame in the image store, or null before the first one. */
  imageId: string | null
  /** What is being shared, from the track label, when the browser says. */
  label: string | null
  error: string | null
  /** Needs a user gesture — call from a click handler, not an effect. */
  open: () => Promise<void>
  close: () => void
  toggle: () => void
}

export function useScreenSense(): ScreenSenseState {
  const [status, setStatus] = useState<EyeStatus>('closed')
  const [imageId, setImageId] = useState<string | null>(null)
  const [label, setLabel] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const senseRef = useRef<ScreenSense | null>(null)

  const close = useCallback(() => {
    senseRef.current?.stop()
    senseRef.current = null
    setStatus('closed')
    setImageId(null)
    setLabel(null)
  }, [])

  const open = useCallback(async () => {
    if (senseRef.current) return
    setError(null)
    setStatus('opening')
    try {
      const sense = await startScreenSense({
        onFrame: ({ blob }) => {
          uploadImage(blob, 'screen')
            .then((stored) => setImageId(stored.image_id))
            .catch(() => {
              /* Next frame is 2s away; a dropped one isn't worth surfacing. */
            })
        },
        // The browser's own "Stop sharing" button, not our toggle. The UI has
        // to follow it — an open eye drawn over a dead stream would be a lie.
        onEnded: () => close(),
        onError: (detail) => setError(detail),
      })
      senseRef.current = sense
      setLabel(sense.label)
      setStatus('open')
    } catch (e) {
      // Cancelling the picker throws NotAllowedError just like a policy block
      // does. Dismissing a dialog is not an error worth a red banner.
      const denied = e instanceof DOMException && e.name === 'NotAllowedError'
      setError(denied ? null : 'Could not start screen sharing.')
      setStatus('closed')
    }
  }, [close])

  const toggle = useCallback(() => {
    if (senseRef.current) close()
    else void open()
  }, [close, open])

  // Never leave a capture running behind a navigation — the browser's sharing
  // indicator would stay up with nothing consuming the stream.
  useEffect(() => close, [close])

  return { status, imageId, label, error, open, close, toggle }
}
