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
  /** Queue a chunk of 16-bit mono PCM at `sampleRate`. */
  enqueue: (pcm: Int16Array, sampleRate: number) => void
  /** Kill everything queued and playing. Used for barge-in. */
  stop: () => void
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
      if (live.length === 0) return 0
      analyser.getFloatTimeDomainData(scratch)
      let sumSquares = 0
      for (let i = 0; i < scratch.length; i++) sumSquares += scratch[i] * scratch[i]
      return Math.sqrt(sumSquares / scratch.length)
    },

    enqueue(pcm, sampleRate) {
      if (pcm.length === 0) return
      // Autoplay policy can leave the context suspended until a gesture; the
      // mic button counts, but resume defensively.
      if (ctx.state === 'suspended') void ctx.resume()

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

      live.push(src)
      src.onended = () => {
        live = live.filter((s) => s !== src)
        settle()
      }
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
      settle()
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
