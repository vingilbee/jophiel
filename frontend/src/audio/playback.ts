/**
 * Gapless playback of streamed PCM, with instant cancellation for barge-in.
 *
 * Chunks arrive from the server faster than real time (measured RTF ~0.63), so
 * the queue builds a buffer and playback never starves. Each chunk is scheduled
 * against a running cursor rather than played on arrival — scheduling by wall
 * clock would leave audible seams between chunks.
 *
 * `stop()` has to be immediate and total: when the user talks over the
 * assistant, every already-scheduled buffer must be killed, not just future
 * ones. That's why sources are tracked individually.
 */

export interface PcmPlayer {
  /**
   * Queue a chunk of 16-bit mono PCM at `sampleRate`. Returns how many seconds
   * from now it is scheduled to start (0 = immediately), or null if the chunk
   * was empty. Excludes the output device's own latency.
   */
  enqueue: (pcm: Int16Array, sampleRate: number) => number | null
  /** Kill everything queued and playing. Used for barge-in. */
  stop: () => void
  /**
   * Freeze playback where it is, keeping everything queued (chunks enqueued
   * meanwhile queue up behind it). Used to hold the reply while the user
   * talks over it, so a mere "I see" can resume from the same word.
   */
  pause: () => void
  /** Continue after `pause()`. */
  resume: () => void
  /**
   * Chunks enqueued from now on speak characters [textStart, textEnd) of the
   * reply. Segment 0 starts a new reply and forgets the previous one's.
   */
  beginSegment: (segment: number, textStart: number, textEnd: number) => void
  /**
   * How many characters of the reply have been heard so far: whole segments
   * that finished playing, plus the played fraction of the one playing now.
   * Null when the audio carried no segment labels. Read it *before* stop().
   */
  heardTextEnd: () => number | null
  /** Resolves once the queue has drained (or immediately after stop()). */
  waitUntilDone: () => Promise<void>
  close: () => void
  readonly playing: boolean
  /** RMS of what's coming out of the speakers right now (0 when silent). */
  readonly level: number
}

export function createPlayer(): PcmPlayer {
  const ctx = new AudioContext()
  let cursor = 0
  let live: AudioBufferSourceNode[] = []
  let doneResolvers: Array<() => void> = []
  // Suspending the context freezes its clock, so every scheduled source and
  // the cursor stay valid across a pause without any rescheduling.
  let paused = false
  // Which span of reply text each stretch of scheduled audio speaks, on the
  // context's clock, so an interruption can tell how much was heard.
  type Segment = { textStart: number; textEnd: number; startAt: number | null; endAt: number }
  let segments: Segment[] = []
  let current: Segment | null = null

  // Everything is routed through an analyser so the UI can visualise the
  // assistant's voice. The mic can't be used for that: capture runs with echo
  // cancellation on, which is exactly what removes the assistant from it.
  const analyser = ctx.createAnalyser()
  analyser.fftSize = 1024
  analyser.smoothingTimeConstant = 0.6
  analyser.connect(ctx.destination)
  const scratch = new Float32Array(analyser.fftSize)

  const settle = () => {
    if (live.length === 0) {
      doneResolvers.forEach((r) => r())
      doneResolvers = []
    }
  }

  return {
    get playing() {
      return live.length > 0
    },

    get level() {
      if (live.length === 0 || paused) return 0
      analyser.getFloatTimeDomainData(scratch)
      let sumSquares = 0
      for (let i = 0; i < scratch.length; i++) sumSquares += scratch[i] * scratch[i]
      return Math.sqrt(sumSquares / scratch.length)
    },

    enqueue(pcm, sampleRate) {
      if (pcm.length === 0) return null
      // Autoplay policy can leave the context suspended until a gesture; the
      // mic button counts, but resume defensively.
      if (ctx.state === 'suspended' && !paused) void ctx.resume()

      const buffer = ctx.createBuffer(1, pcm.length, sampleRate)
      const channel = buffer.getChannelData(0)
      for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 0x8000

      const src = ctx.createBufferSource()
      src.buffer = buffer
      src.connect(analyser)

      // Never schedule in the past: if the queue drained while we waited for
      // the next chunk, restart the cursor from now.
      const startAt = Math.max(cursor, ctx.currentTime)
      src.start(startAt)
      cursor = startAt + buffer.duration
      if (current) {
        current.startAt ??= startAt
        current.endAt = cursor
      }

      live.push(src)
      src.onended = () => {
        live = live.filter((s) => s !== src)
        settle()
      }
      return startAt - ctx.currentTime
    },

    stop() {
      const stopping = live
      live = []
      stopping.forEach((s) => {
        s.onended = null
        try {
          s.stop()
        } catch {
          // Already ended; nothing to cancel.
        }
        s.disconnect()
      })
      cursor = 0
      segments = []
      current = null
      settle()
      if (paused) {
        paused = false
        void ctx.resume()
      }
    },

    pause() {
      if (paused) return
      paused = true
      void ctx.suspend()
    },

    resume() {
      if (!paused) return
      paused = false
      void ctx.resume()
    },

    beginSegment(segment, textStart, textEnd) {
      if (segment === 0) segments = []
      current = { textStart, textEnd, startAt: null, endAt: 0 }
      segments.push(current)
    },

    heardTextEnd() {
      if (segments.length === 0) return null
      // Frozen while paused, so a held reply reports where it was held.
      const now = ctx.currentTime
      let heard = 0
      for (const s of segments) {
        if (s.startAt === null || now <= s.startAt) break
        if (now >= s.endAt) {
          heard = s.textEnd
          continue
        }
        // No word timings from the voice engine: assume the text is spread
        // evenly over the clause's audio. The server snaps to a word.
        const fraction = (now - s.startAt) / (s.endAt - s.startAt)
        heard = s.textStart + Math.floor(fraction * (s.textEnd - s.textStart))
        break
      }
      return heard
    },

    waitUntilDone() {
      if (live.length === 0) return Promise.resolve()
      return new Promise<void>((resolve) => doneResolvers.push(resolve))
    },

    close() {
      this.stop()
      void ctx.close()
    },
  }
}
