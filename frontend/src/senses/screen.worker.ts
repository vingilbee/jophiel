/**
 * Screen-frame triage, off the main thread.
 *
 * Every captured frame arrives here as an ImageBitmap and most of them die
 * here. That is the whole economics of an always-on eye: a screen the user is
 * reading rather than changing produces nothing — no encode, no upload, no
 * tokens — and only a frame that genuinely differs from the last one is worth
 * anything downstream.
 *
 * All three steps run in the worker, including the JPEG encode. Putting only
 * the cheap diff here and leaving the expensive encode on the main thread
 * would be exactly backwards.
 */

// The project's tsconfig uses the DOM lib rather than WebWorker (the two
// conflict), so name the handful of worker globals we touch instead of
// pulling in a second lib.
interface WorkerScope {
  postMessage(message: unknown, transfer?: Transferable[]): void
  addEventListener(type: 'message', listener: (event: MessageEvent) => void): void
}
const ctx = self as unknown as WorkerScope

export interface CaptureRequest {
  bitmap: ImageBitmap
  /** Send the frame even if it looks unchanged (first frame, or heartbeat). */
  force: boolean
}

export type CaptureResult =
  | { type: 'frame'; blob: Blob; width: number; height: number; change: number }
  | { type: 'skip'; change: number }
  | { type: 'error'; detail: string }

/**
 * Diff resolution. 64×64 is coarse enough that a blinking cursor or an
 * antialiasing shimmer averages away, and fine enough that a single edited
 * line of code still moves a few cells — at 1080p one cell is ~30×17 px.
 */
const GRID = 64
/** Per-cell 0–255 delta that counts as "this cell changed". */
const CELL_DELTA = 10
/** Fraction of cells that must change before the frame is worth uploading. */
const CHANGE_RATIO = 0.005

/**
 * Long edge of the uploaded frame. Anthropic downscales anything larger to
 * ~1568px anyway, and 1280 keeps UI text legible at roughly 1.1k tokens a
 * look. Going higher costs tokens without the model seeing more.
 */
const MAX_EDGE = 1280
const JPEG_QUALITY = 0.72

let previous: Float32Array | null = null
const diffCanvas = new OffscreenCanvas(GRID, GRID)
const diffCtx = diffCanvas.getContext('2d', { willReadFrequently: true })

/** Mean luma per cell, as a GRID×GRID grayscale thumbnail. */
function luma(bitmap: ImageBitmap): Float32Array | null {
  if (!diffCtx) return null
  diffCtx.drawImage(bitmap, 0, 0, GRID, GRID)
  const { data } = diffCtx.getImageData(0, 0, GRID, GRID)
  const cells = new Float32Array(GRID * GRID)
  for (let i = 0; i < cells.length; i++) {
    const p = i * 4
    // Rec. 601 luma: matches how a viewer perceives a change, so a dark-mode
    // editor and a white page are compared on the same footing.
    cells[i] = (data[p] * 299 + data[p + 1] * 587 + data[p + 2] * 114) / 1000
  }
  return cells
}

/** Fraction of cells that moved by more than `CELL_DELTA`. */
function changedFraction(next: Float32Array, prev: Float32Array): number {
  let moved = 0
  for (let i = 0; i < next.length; i++) {
    if (Math.abs(next[i] - prev[i]) > CELL_DELTA) moved++
  }
  return moved / next.length
}

async function encode(bitmap: ImageBitmap): Promise<Blob | null> {
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height))
  const width = Math.max(1, Math.round(bitmap.width * scale))
  const height = Math.max(1, Math.round(bitmap.height * scale))

  const canvas = new OffscreenCanvas(width, height)
  const context = canvas.getContext('2d')
  if (!context) return null
  context.drawImage(bitmap, 0, 0, width, height)
  return canvas.convertToBlob({ type: 'image/jpeg', quality: JPEG_QUALITY })
}

ctx.addEventListener('message', (event: MessageEvent) => {
  const { bitmap, force } = event.data as CaptureRequest
  void handle(bitmap, force)
})

async function handle(bitmap: ImageBitmap, force: boolean): Promise<void> {
  try {
    const cells = luma(bitmap)
    if (!cells) {
      ctx.postMessage({ type: 'error', detail: 'No 2D context in worker.' })
      return
    }

    const change = previous ? changedFraction(cells, previous) : 1
    const send = force || !previous || change > CHANGE_RATIO
    // Only advance the reference on a frame we actually sent. Otherwise a
    // screen drifting slowly — a page fading in, a video — would never trip
    // the threshold against its own immediately-previous frame and the eye
    // would go permanently blind to gradual change.
    if (send) previous = cells

    if (!send) {
      ctx.postMessage({ type: 'skip', change })
      return
    }

    const blob = await encode(bitmap)
    if (!blob) {
      ctx.postMessage({ type: 'error', detail: 'Could not encode the frame.' })
      return
    }
    ctx.postMessage({
      type: 'frame',
      blob,
      width: bitmap.width,
      height: bitmap.height,
      change,
    })
  } catch (e) {
    ctx.postMessage({
      type: 'error',
      detail: e instanceof Error ? e.message : 'Frame processing failed.',
    })
  } finally {
    // Bitmaps hold decoded pixels; leaking one per second is a real leak.
    bitmap.close()
  }
}
