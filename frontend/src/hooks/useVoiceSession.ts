import { useCallback, useEffect, useRef, useState } from 'react'
import { CAPTURE_SAMPLE_RATE, startCapture, type MicCapture } from '../audio/capture'
import { createPlayer, type PcmPlayer } from '../audio/playback'
import { createVad } from '../audio/vad'
import { createWakeWord, type WakeWordDetector } from '../audio/wakeword'

/**
 * The conversational loop: wake -> listen -> think -> speak -> re-arm.
 *
 * On the timings. The original sketch was a flat 20-second listening window;
 * these are the changes that make it feel like an assistant instead of a
 * recorder:
 *
 *  - The end of your turn is decided by silence (VAD), not a timer, so you're
 *    never cut off mid-sentence and there's no dead air after you finish.
 *  - The window only guards the case where you say *nothing at all*, and 7s is
 *    plenty for that — 20s of an open mic reads as broken.
 *  - Re-arming after the assistant speaks (the instinct behind the original
 *    "count another 20 seconds") is exactly right, and it's the FOLLOW_UP
 *    state. It just wants ~7s, not 20.
 *  - Barge-in matters more than any of the above: talking over the assistant
 *    cuts it off instantly, which is most of what makes Siri feel responsive.
 */

export type VoiceState =
  | 'off'
  | 'idle' // armed, waiting for the wake word
  | 'listening' // mic open, capturing an utterance
  | 'thinking' // utterance sent, awaiting the reply
  | 'speaking' // reply audio playing

/** No speech at all within this long → stand down. */
// const NO_SPEECH_TIMEOUT_MS = 7000
/** Hard cap on a single utterance, so a stuck mic can't stream forever. */
// const MAX_UTTERANCE_MS = 30000

export interface VoiceSession {
  state: VoiceState
  /** False when no trained wake-word model is present (push-to-talk only). */
  wakeWordAvailable: boolean
  error: string | null
  /** Turn voice mode on (needs a user gesture — mic permission + autoplay). */
  enable: () => Promise<void>
  disable: () => void
  /** Start an utterance now, bypassing the wake word. */
  pushToTalk: () => void
  /** End the current utterance immediately instead of waiting for silence. */
  endTurn: () => void
  /** Cut the assistant off and start listening again (button barge-in). */
  interrupt: () => void
  /**
   * Current audio level, 0–1: the mic while listening, the speakers while
   * speaking. Read from an animation loop — it's a getter, not state, so a
   * 60 Hz visualiser doesn't re-render the page 60 times a second.
   */
  getLevel: () => number
  /** Feed streamed reply audio in. Wire to the socket's onAudio. */
  handleAudio: (pcm: Int16Array, sampleRate: number) => void
  /** Reply audio finished streaming. Wire to the socket's onAudioEnd. */
  handleAudioEnd: () => void
}

interface Options {
  conversationId: string | null
  onToken: (chunk: string) => void
  onReplyComplete: (conversationId: string, reply: string) => void
  socket: {
    startVoice: (conversationId: string | null, sampleRate: number) => boolean
    sendAudio: (pcm: Int16Array) => void
    endVoice: (
      conversationId: string | null,
      onToken: (c: string) => void,
    ) => Promise<{ conversationId: string; reply: string }>
    cancelVoice: () => void
    stop: () => void
  }
}

export function useVoiceSession(opts: Options): VoiceSession {
  const [state, setState] = useState<VoiceState>('off')
  const [wakeWordAvailable, setWakeWordAvailable] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const captureRef = useRef<MicCapture | null>(null)
  const playerRef = useRef<PcmPlayer | null>(null)
  const wakeRef = useRef<WakeWordDetector | null>(null)
  const vadRef = useRef<ReturnType<typeof createVad> | null>(null)
  const timerRef = useRef<number | null>(null)
  const micLevelRef = useRef(0)
  // Do not submit room tone (or residual speaker echo) to Whisper. Short empty
  // recordings are prone to confident hallucinations such as "you", which can
  // otherwise start a self-sustaining voice loop.
  const speechDetectedRef = useRef(false)
  // State is read from callbacks that outlive a render; mirror it in a ref.
  const stateRef = useRef<VoiceState>('off')
  const optsRef = useRef(opts)
  optsRef.current = opts

  const audioArrivedRef = useRef(false)
  // `audio_end` is sent before the server's final `done` frame. Re-opening the
  // mic after playback alone can therefore cancel the still-finishing turn and
  // leave useChatSocket's pending response stuck forever. A follow-up is safe
  // only after both halves of the voice reply have completed.
  const replyDoneRef = useRef(false)
  const playbackDoneRef = useRef(false)
  const audioGraceTimerRef = useRef<number | null>(null)

  const clearAudioGraceTimer = useCallback(() => {
    if (audioGraceTimerRef.current !== null) {
      window.clearTimeout(audioGraceTimerRef.current)
      audioGraceTimerRef.current = null
    }
  }, [])

  const setPhase = useCallback((next: VoiceState) => {
    stateRef.current = next
    setState(next)
  }, [])

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  /** Open an utterance and start streaming mic audio to the server. */
  const beginUtterance = useCallback(() => {
    const { conversationId, socket } = optsRef.current
    if (!socket.startVoice(conversationId, CAPTURE_SAMPLE_RATE)) {
      setError('Not connected.')
      return
    }
    vadRef.current?.reset()
    speechDetectedRef.current = false
    setPhase('listening')
    clearTimer()
    // timerRef.current = window.setTimeout(() => {
    //   // Armed but nobody said anything — stand down rather than sit open.
    //   if (stateRef.current === 'listening' && !vadRef.current?.speaking) {
    //     optsRef.current.socket.cancelVoice()
    //     setPhase('idle')
    //   }
    // }, NO_SPEECH_TIMEOUT_MS)
  }, [clearTimer, setPhase])

  /** Close the utterance, run the turn, then re-arm for a follow-up. */
  const finishUtterance = useCallback(async () => {
    clearTimer()
    setPhase('thinking')
    audioArrivedRef.current = false
    replyDoneRef.current = false
    playbackDoneRef.current = false
    const { conversationId, socket, onToken, onReplyComplete } = optsRef.current
    try {
      const result = await socket.endVoice(conversationId, onToken)
      replyDoneRef.current = true
      onReplyComplete(result.conversationId, result.reply)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Voice turn failed.')
      if (stateRef.current === 'thinking') beginUtterance()
      return
    }
    if (audioArrivedRef.current && playbackDoneRef.current) {
      beginUtterance()
      return
    }
    // Don't decide synchronously here — the first audio chunk may not have
    // arrived yet even on a normal turn (measured first-chunk latency is
    // ~1.9s). Give it a grace window; if nothing shows up, TTS is genuinely
    // down for this turn, so re-arm ourselves instead of stalling forever.
    if (stateRef.current === 'thinking' && !audioArrivedRef.current) {
      clearAudioGraceTimer()
      audioGraceTimerRef.current = window.setTimeout(() => {
        if (stateRef.current === 'thinking' && !audioArrivedRef.current) {
          beginUtterance()
        }
      }, 4000)
    }
  }, [beginUtterance, clearAudioGraceTimer, clearTimer, setPhase])

  /**
   * Stop the assistant mid-reply and open a new utterance. Shared by the
   * barge-in path (user talked over it) and the interrupt button.
   */
  const interrupt = useCallback(() => {
    const phase = stateRef.current
    if (phase !== 'speaking' && phase !== 'thinking') return
    playerRef.current?.stop()
    optsRef.current.socket.stop()
    beginUtterance()
  }, [beginUtterance])

  const handleFrame = useCallback(
    ({ pcm, rms }: { pcm: Int16Array; rms: number }) => {
      const phase = stateRef.current
      if (phase === 'off') return

      // Smoothed so the visualiser doesn't strobe on single 8ms frames.
      micLevelRef.current = micLevelRef.current * 0.7 + rms * 0.3
      wakeRef.current?.push(pcm)
      vadRef.current?.push(rms, pcm.length)

      if (phase === 'listening') {
        optsRef.current.socket.sendAudio(pcm)
      }
    },
    [],
  )

  const handleVad = useCallback(
    (e: 'speech-start' | 'speech-end') => {
      const phase = stateRef.current

      if (e === 'speech-start') {
        // Barge-in: talking over the assistant cuts it off immediately.
        if (phase === 'speaking' || phase === 'thinking') {
          interrupt()
          return
        }
        if (phase === 'listening') speechDetectedRef.current = true
        // if (phase === 'listening') {
        //   clearTimer()
        //   timerRef.current = window.setTimeout(() => {
        //     if (stateRef.current === 'listening') void finishUtterance()
        //   }, MAX_UTTERANCE_MS)
        // }
        return
      }

      // speech-end: the VAD hangover elapsed, so the turn is genuinely over.
      if (phase === 'listening') void finishUtterance()
    },
    [finishUtterance, interrupt],
  )

  const enable = useCallback(async () => {
    if (stateRef.current !== 'off') return
    setError(null)
    try {
      playerRef.current = createPlayer()
      vadRef.current = createVad(CAPTURE_SAMPLE_RATE, handleVad)
      wakeRef.current = await createWakeWord(() => {
        if (stateRef.current === 'idle') beginUtterance()
      })
      setWakeWordAvailable(wakeRef.current.available)
      captureRef.current = await startCapture(handleFrame)
      setPhase('idle')
    } catch (e) {
      setError(
        e instanceof Error && e.name === 'NotAllowedError'
          ? 'Microphone permission denied.'
          : 'Could not start the microphone.',
      )
      setPhase('off')
    }
  }, [beginUtterance, handleFrame, handleVad, setPhase])

  const disable = useCallback(() => {
    clearTimer()
    clearAudioGraceTimer()
    optsRef.current.socket.cancelVoice()
    captureRef.current?.stop()
    captureRef.current = null
    playerRef.current?.close()
    playerRef.current = null
    wakeRef.current?.close()
    wakeRef.current = null
    vadRef.current = null
    micLevelRef.current = 0
    setPhase('off')
  }, [clearAudioGraceTimer, clearTimer, setPhase])

  const pushToTalk = useCallback(() => {
    if (stateRef.current === 'idle') beginUtterance()
  }, [beginUtterance])

  const endTurn = useCallback(() => {
    if (stateRef.current !== 'listening') return
    if (!speechDetectedRef.current) {
      optsRef.current.socket.cancelVoice()
      setPhase('idle')
      return
    }
    void finishUtterance()
  }, [finishUtterance, setPhase])

  /**
   * Normalised for display, not analysis: the square root spreads out the
   * bottom of the range, where speech actually lives, so the orb moves with
   * normal talking instead of only with shouting.
   */
  const getLevel = useCallback(() => {
    const phase = stateRef.current
    if (phase === 'off' || phase === 'thinking') return 0
    const raw =
      phase === 'speaking' ? (playerRef.current?.level ?? 0) : micLevelRef.current
    const level = Math.min(1, Math.sqrt(raw / 0.2))
    // Armed-but-not-listening shouldn't look like an open mic; damp room noise.
    return phase === 'idle' ? level * 0.35 : level
  }, [])

  const handleAudio = useCallback(
    (pcm: Int16Array, sampleRate: number) => {
      audioArrivedRef.current = true
      clearAudioGraceTimer()
      // 'thinking' is a reply to something we asked. 'idle' can only be the
      // assistant speaking first — it still has to show as speaking, or the
      // orb sits still while a voice comes out of the machine.
      const phase = stateRef.current
      if (phase === 'thinking' || phase === 'idle') setPhase('speaking')
      playerRef.current?.enqueue(pcm, sampleRate)
    },
    [clearAudioGraceTimer, setPhase],
  )

  const handleAudioEnd = useCallback(() => {
    // The server has finished *sending*; the queue is still draining. Re-arm
    // only once it's actually silent, or the follow-up window would open while
    // the assistant is still talking and immediately trip on its own voice.
    void playerRef.current?.waitUntilDone().then(() => {
      playbackDoneRef.current = true
      const phase = stateRef.current
      if ((phase === 'speaking' || phase === 'thinking') && replyDoneRef.current) {
        beginUtterance()
      }
    })
  }, [beginUtterance])

  useEffect(() => disable, [disable])

  // A browser may suspend Web Audio while a tab/window is backgrounded. Keep
  // an already-authorized session alive where the browser permits it, and
  // recover the context when the document becomes active again.
  useEffect(() => {
    const recoverCapture = () => {
      // Resuming Web Audio is commonly rejected while the page is still
      // hidden. Capture is allowed to continue there; recovery is for return.
      if (document.hidden) return
      const capture = captureRef.current
      if (!capture || stateRef.current === 'off') return
      void capture.resume().then((running) => {
        if (!running && !capture.active) {
          setError('Microphone was stopped by the browser. Return here and restart voice.')
        }
      }).catch(() => {
        setError('Microphone was suspended. Return here and restart voice.')
      })
    }
    document.addEventListener('visibilitychange', recoverCapture)
    window.addEventListener('focus', recoverCapture)
    window.addEventListener('pageshow', recoverCapture)
    return () => {
      document.removeEventListener('visibilitychange', recoverCapture)
      window.removeEventListener('focus', recoverCapture)
      window.removeEventListener('pageshow', recoverCapture)
    }
  }, [])

  return {
    state,
    wakeWordAvailable,
    error,
    enable,
    disable,
    pushToTalk,
    endTurn,
    interrupt,
    getLevel,
    handleAudio,
    handleAudioEnd,
  }
}
