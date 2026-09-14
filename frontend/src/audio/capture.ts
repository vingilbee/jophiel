/**
 * Microphone capture at 16 kHz, 16-bit mono PCM.
 *
 * Requests the AudioContext at 16 kHz directly so the browser's own resampler
 * does the rate conversion — cheaper and better than doing it in JS, and it's
 * the rate Whisper wants.
 *
 * `echoCancellation` is not optional here. The mic stays open while the
 * assistant is speaking so barge-in works, and without AEC the assistant's own
 * voice coming out of the speakers trips the VAD and it interrupts itself.
 */

export const CAPTURE_SAMPLE_RATE = 16000

export interface CaptureFrame {
  pcm: Int16Array
  rms: number
}

export interface MicCapture {
  stop: () => void
  resume: () => Promise<boolean>
  readonly sampleRate: number
  readonly active: boolean
}

export async function startCapture(
  onFrame: (frame: CaptureFrame) => void,
): Promise<MicCapture> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    },
  })

  const ctx = new AudioContext({ sampleRate: CAPTURE_SAMPLE_RATE })
  try {
    await ctx.audioWorklet.addModule('/audio/pcm-worklet.js')
  } catch (err) {
    stream.getTracks().forEach((t) => t.stop())
    await ctx.close()
    throw err
  }

  const source = ctx.createMediaStreamSource(stream)
  const node = new AudioWorkletNode(ctx, 'pcm-worklet')

  node.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; rms: number }>) => {
    onFrame({ pcm: new Int16Array(e.data.pcm), rms: e.data.rms })
  }

  source.connect(node)
  // A worklet with no downstream connection gets culled in some browsers.
  // Route through a muted gain node so it keeps getting pulled without the mic
  // being echoed back out of the speakers.
  const mute = ctx.createGain()
  mute.gain.value = 0
  node.connect(mute).connect(ctx.destination)

  let stopped = false
  return {
    sampleRate: ctx.sampleRate,
    get active() {
      return !stopped && stream.active && stream.getAudioTracks().some((t) => t.readyState === 'live')
    },
    async resume() {
      if (stopped || !stream.active) return false
      if (ctx.state === 'suspended') await ctx.resume()
      return ctx.state === 'running'
    },
    stop() {
      if (stopped) return
      stopped = true
      node.port.postMessage('stop')
      node.port.onmessage = null
      source.disconnect()
      node.disconnect()
      mute.disconnect()
      stream.getTracks().forEach((t) => t.stop())
      void ctx.close()
    },
  }
}
