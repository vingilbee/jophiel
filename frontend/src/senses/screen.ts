import type { CaptureResult } from './screen.worker'

/**
 * The eye: a screen-capture stream, sampled and triaged into occasional frames.
 *
 * The browser is the only place this can start from — `getDisplayMedia` needs
 * a user gesture and shows its own picker, which is the consent step and is
 * deliberately not something the app can fake or skip.
 *
 * What leaves the machine is heavily filtered. We sample every couple of
 * seconds, hand each frame to a worker that drops it unless the screen really
 * changed, and let a slow heartbeat through so a static screen still has a
 * bounded staleness. A user reading a page produces roughly one frame a
 * minute; a user switching windows produces one per switch.
 */

/** How often a frame is sampled and diffed. Not how often one is sent. */
const SAMPLE_INTERVAL_MS = 2000
/**
 * Send an unchanged frame at least this often. Without it, "the screen hasn't
 * changed in 40 minutes" and "the eye broke 40 minutes ago" look identical to
 * the server.
 */
const HEARTBEAT_MS = 60_000

export interface ScreenFrame {
  blob: Blob
  width: number
  height: number
  /** Fraction of the frame that changed, 0–1. 1 for the first frame. */
  change: number
}

export interface ScreenSense {
  stop: () => void
  /** Label of the surface being shared, e.g. a window title, when exposed. */
  readonly label: string
}

interface Options {
  onFrame: (frame: ScreenFrame) => void
  /** The user stopped sharing from the browser's own indicator, not our UI. */
  onEnded: () => void
  onError: (message: string) => void
}

/** `getDisplayMedia` options newer than the DOM lib in this project's TS. */
interface DisplayMediaOptions extends DisplayMediaStreamOptions {
  /** Keep our own tab out of the picker — otherwise it films itself. */
  selfBrowserSurface?: 'include' | 'exclude'
  /** Let the user swap which window is shared without re-granting. */
  surfaceSwitching?: 'include' | 'exclude'
  monitorTypeSurfaces?: 'include' | 'exclude'
}

export async function startScreenSense({
  onFrame,
  onEnded,
  onError,
}: Options): Promise<ScreenSense> {
  const options: DisplayMediaOptions = {
    // A low frame rate is a hint to the browser, not our sampling rate: it
    // lets the compositor skip work we would only throw away.
    video: { frameRate: 2 },
    audio: false,
    selfBrowserSurface: 'exclude',
    surfaceSwitching: 'include',
    monitorTypeSurfaces: 'include',
  }

  const stream = await navigator.mediaDevices.getDisplayMedia(options)
  const track = stream.getVideoTracks()[0]

  // A <video> is the only way to get decodable frames out of a MediaStream.
  // It is never attached to the document — nothing renders it.
  const video = document.createElement('video')
  video.srcObject = stream
  video.muted = true
  video.playsInline = true
  await video.play().catch(() => {
    /* Autoplay is allowed for a muted, user-initiated capture; ignore. */
  })

  const worker = new Worker(new URL('./screen.worker.ts', import.meta.url), {
    type: 'module',
  })

  let stopped = false
  let lastSentAt = 0
  // One frame in flight at a time. A slow encode or a slow upload must queue
  // behind itself rather than pile up a backlog of stale screens.
  let busy = false

  worker.onmessage = (event: MessageEvent<CaptureResult>) => {
    const result = event.data
    busy = false
    if (stopped) return
    if (result.type === 'frame') {
      lastSentAt = performance.now()
      onFrame(result)
    } else if (result.type === 'error') {
      onError(result.detail)
    }
  }

  const timer = window.setInterval(() => {
    if (stopped || busy) return
    if (video.readyState < 2 || video.videoWidth === 0) return
    busy = true
    createImageBitmap(video)
      .then((bitmap) => {
        if (stopped) {
          bitmap.close()
          busy = false
          return
        }
        worker.postMessage(
          { bitmap, force: performance.now() - lastSentAt > HEARTBEAT_MS },
          [bitmap],
        )
      })
      .catch(() => {
        busy = false
      })
  }, SAMPLE_INTERVAL_MS)

  // The user can revoke sharing from the browser's own bar at any time, and
  // that has to switch the eye off in our UI too — an indicator still showing
  // an open eye over a dead stream is the one thing we must never do.
  track.addEventListener('ended', () => {
    if (!stopped) onEnded()
  })

  const stop = () => {
    if (stopped) return
    stopped = true
    window.clearInterval(timer)
    worker.terminate()
    stream.getTracks().forEach((t) => t.stop())
    video.srcObject = null
  }

  return {
    stop,
    get label() {
      return track.label || 'your screen'
    },
  }
}
