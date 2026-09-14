/**
 * Mic capture worklet: float32 in, 16-bit PCM + frame energy out.
 *
 * Lives in public/ because an AudioWorklet module is fetched by URL at runtime,
 * not bundled — Vite would rewrite it into a chunk the worklet can't load.
 *
 * The AudioContext is created at 16 kHz by the caller, so there's no resampling
 * to do here. RMS is computed alongside the conversion because the VAD needs it
 * every frame and doing it here keeps that work off the main thread.
 */
class PCMWorklet extends AudioWorkletProcessor {
  constructor() {
    super()
    this.running = true
    this.port.onmessage = (e) => {
      if (e.data === 'stop') this.running = false
    }
  }

  process(inputs) {
    if (!this.running) return false
    const channel = inputs[0]?.[0]
    if (!channel) return true

    const pcm = new Int16Array(channel.length)
    let sumSquares = 0
    for (let i = 0; i < channel.length; i++) {
      const s = Math.max(-1, Math.min(1, channel[i]))
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff
      sumSquares += s * s
    }
    const rms = Math.sqrt(sumSquares / channel.length)

    // Transfer the buffer rather than copying it; this fires ~every 2.7ms.
    this.port.postMessage({ pcm: pcm.buffer, rms }, [pcm.buffer])
    return true
  }
}

registerProcessor('pcm-worklet', PCMWorklet)
