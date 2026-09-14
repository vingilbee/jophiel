/**
 * Wake-word detection slot.
 *
 * Detection runs in the browser on purpose: the alternative is streaming the
 * microphone to the server around the clock, which is worse for bandwidth,
 * server CPU, and privacy. Nothing leaves the machine until the word fires.
 *
 * STATUS: the "columbina" model does not exist yet. openWakeWord ships
 * pretrained models for a handful of phrases ("hey jarvis", "alexa", ...) and
 * custom words require training your own through their synthetic-data pipeline
 * (Piper TTS -> thousands of samples -> train -> export ONNX). Until that
 * artifact exists, `NullWakeWord` is used and activation is push-to-talk.
 *
 * To finish it, either:
 *   1. Train columbina.onnx via openWakeWord's notebook, drop it in
 *      public/audio/, and implement OnnxWakeWord below against onnxruntime-web.
 *   2. Or generate a Porcupine .ppn keyword (Picovoice console, ~1 minute,
 *      free personal tier) and implement a PorcupineWakeWord here instead.
 *
 * Either way the rest of the voice pipeline is unchanged — it only depends on
 * this interface.
 */

export interface WakeWordDetector {
  /** Feed a captured frame. Calls the callback when the wake word fires. */
  push: (pcm: Int16Array) => void
  reset: () => void
  close: () => void
  readonly available: boolean
}

/** Stand-in used while no trained model is present. Never fires. */
export function createNullWakeWord(): WakeWordDetector {
  return {
    push: () => {},
    reset: () => {},
    close: () => {},
    available: false,
  }
}

/**
 * Load the real detector if a model has been dropped in, else the null one.
 * Callers should treat `available: false` as "push-to-talk only" and surface
 * that in the UI rather than silently appearing to listen.
 */
export async function createWakeWord(
  _onDetected: () => void,
): Promise<WakeWordDetector> {
  const res = await fetch('/audio/columbina.onnx', { method: 'HEAD' }).catch(
    () => null,
  )
  if (!res?.ok) return createNullWakeWord()

  // Model is present but the ONNX runtime binding isn't written yet; falling
  // back keeps the app working rather than throwing on a half-finished path.
  console.warn(
    '[wakeword] columbina.onnx found but the ONNX detector is not implemented yet; using push-to-talk.',
  )
  return createNullWakeWord()
}
