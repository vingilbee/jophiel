/**
 * Voice activity detection and utterance endpointing.
 *
 * This is what replaces a fixed listening window. A 20-second timer either cuts
 * the user off mid-sentence or leaves the mic open through 18 seconds of
 * silence; neither feels like talking to something. Instead: speech ends when
 * the user actually stops talking, detected as a run of quiet frames.
 *
 * Deliberately energy-based rather than a neural VAD (Silero et al). With
 * browser noise suppression already upstream, RMS plus an adaptive noise floor
 * is accurate enough for a push-to-talk-adjacent flow, and it costs no model
 * download and no inference on the audio thread. If it proves too eager in
 * noisy rooms, this module is the seam to swap Silero in behind.
 */

export interface VadConfig {
  /** Speech must exceed the noise floor by this ratio to count. */
  speechRatio: number
  /** Absolute floor, so a silent room can't make the threshold ~0. */
  minThreshold: number
  /** Quiet time after speech before the utterance is considered over. */
  hangoverMs: number
  /** Speech must last this long to count as a real onset (rejects clicks). */
  minSpeechMs: number
}

export const DEFAULT_VAD: VadConfig = {
  speechRatio: 2.5,
  minThreshold: 0.012,
  // ~800ms is the sweet spot: shorter clips people who pause mid-thought,
  // longer and the assistant feels slow to respond.
  hangoverMs: 800,
  minSpeechMs: 150,
}

export type VadEvent = 'speech-start' | 'speech-end'

export function createVad(
  sampleRate: number,
  onEvent: (e: VadEvent) => void,
  config: VadConfig = DEFAULT_VAD,
) {
  let noiseFloor = config.minThreshold
  let speaking = false
  let speechMs = 0
  let quietMs = 0

  return {
    /** Feed one captured frame. `samples` is the frame length. */
    push(rms: number, samples: number) {
      const frameMs = (samples / sampleRate) * 1000
      const threshold = Math.max(noiseFloor * config.speechRatio, config.minThreshold)
      const loud = rms > threshold

      if (loud) {
        quietMs = 0
        speechMs += frameMs
        if (!speaking && speechMs >= config.minSpeechMs) {
          speaking = true
          onEvent('speech-start')
        }
      } else {
        // Only adapt the noise floor while nobody is talking, or the user's own
        // voice drags the threshold up until they get cut off.
        noiseFloor = noiseFloor * 0.97 + rms * 0.03
        if (speaking) {
          quietMs += frameMs
          if (quietMs >= config.hangoverMs) {
            speaking = false
            speechMs = 0
            quietMs = 0
            onEvent('speech-end')
          }
        } else {
          speechMs = 0
        }
      }
    },
    get speaking() {
      return speaking
    },
    reset() {
      // Each follow-up starts after audio has played through the speakers.
      // Even with echo cancellation, that residual output can raise the
      // adaptive floor enough that normal speech is missed on the next turn.
      // Start endpointing the new utterance from the configured baseline.
      noiseFloor = config.minThreshold
      speaking = false
      speechMs = 0
      quietMs = 0
    },
  }
}
