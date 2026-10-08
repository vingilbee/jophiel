import { useCallback, useEffect, useRef, useState } from 'react'
import { CAPTURE_SAMPLE_RATE, startCapture, type MicCapture } from '../audio/capture'
import { createPcmRingBuffer, type PcmRingBuffer } from '../audio/pcmRingBuffer'
import { createPlayer, type PcmPlayer } from '../audio/playback'
import { createVad, DEFAULT_VAD, speechOnsetPosition } from '../audio/vad'
import { createWakeWord, type WakeWordDetector } from '../audio/wakeword'
import type { BargeAction, VoiceLatencyReport } from './useChatSocket'

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
/**
 * Speech needed to hold the assistant while she is talking. A bit above the
 * normal onset (150ms) to skip clicks and her own echo; kept low because a
 * false trigger now only pauses her briefly (the hold resumes on noise).
 */
// How long 'thinking' may last with no reply and no audio before giving up.
const NO_ANSWER_TIMEOUT_MS = 60_000
const BARGE_IN_MIN_SPEECH_MS = 250
/** Noise-floor multiple that counts as speech while she talks (2.5 otherwise). */
const BARGE_IN_SPEECH_RATIO = 2.0
/**
 * Mic audio kept locally while not streaming, for barge-in pre-roll. Covers
 * the barge-in confirmation delay plus padding with room to spare.
 */
const PREROLL_RING_MS = 800
/** Lead-in before the estimated onset, so a soft first consonant survives. */
const PREROLL_PADDING_MS = 150
/**
 * While the reply is held, this much quiet ends the utterance (vs 800ms
 * normally): an "I see" should get her talking again quickly.
 */
const HOLD_HANGOVER_MS = 400
/**
 * This much actual (loud) speech over a held reply is clearly a real turn,
 * not "mm-hm": cancel the reply now instead of waiting for the user to
 * finish. Counted from the VAD's loud frames only — wall time would also
 * count the hangover after they stop, which pushed "I see" past the limit.
 */
const HOLD_COMMIT_SPEECH_MS = 1000
/** No decision from the server in this long → carry on with the reply. */
const HOLD_DECISION_TIMEOUT_MS = 8000

export interface VoiceSession {
  state: VoiceState
  /**
   * The user is talking over a reply that is paused, not yet cancelled (shown
   * as 'listening'). If it turns out to be "I see", the reply resumes.
   */
  holding: boolean
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
  /**
   * She's about to speak first (unprompted). If the mic is open but nobody has
   * started talking, close that empty utterance so her audio plays, then
   * listen again afterwards. If the user is mid-sentence, do nothing: her
   * words still show as text, and she never talks over them.
   */
  beginUnpromptedSpeech: () => void
  /** Feed streamed reply audio in. Wire to the socket's onAudio. */
  handleAudio: (pcm: Int16Array, sampleRate: number) => void
  /** Reply audio finished streaming. Wire to the socket's onAudioEnd. */
  handleAudioEnd: () => void
  /** The server decided a held interruption. Wire to onBargeResolved. */
  handleBargeResolved: (action: BargeAction) => void
  /** Labels the reply audio that follows. Wire to onAudioSegment. */
  handleAudioSegment: (segment: number, textStart: number, textEnd: number) => void
}

interface Options {
  conversationId: string | null
  /** Whether the shared socket is open; clears a stale "Not connected." */
  connected: boolean
  onToken: (chunk: string) => void
  onSticker?: (name: string) => void
  onReplyComplete: (conversationId: string, reply: string) => void
  /** A reply was cut off after the user heard `heardChars` of its text. */
  onHeard?: (turnId: string, heardChars: number) => void
  socket: {
    startVoice: (conversationId: string | null, sampleRate: number) => boolean
    sendAudio: (pcm: Int16Array) => void
    endVoice: (
      conversationId: string | null,
      onToken: (c: string) => void,
      onSticker?: (name: string) => void,
    ) => Promise<{ conversationId: string; reply: string }>
    cancelVoice: () => void
    stop: () => void
    /**
     * Button barge-in: stop and settle the in-flight turn now, fencing its
     * late events, and open a new utterance in the same frame.
     */
    interruptVoiceStart: (conversationId: string | null, sampleRate: number) => boolean
    /** Spoken barge-in: open an utterance over the reply without cancelling it. */
    holdVoice: (conversationId: string | null, sampleRate: number) => boolean
    commitHold: () => void
    endHold: () => boolean
    resumeHold: () => void
    stopHold: () => void
    adoptHold: (
      conversationId: string | null,
      onToken: (c: string) => void,
      onSticker?: (name: string) => void,
    ) => Promise<{ conversationId: string; reply: string }>
    /** Report heard progress for the reply being spoken; returns its turn id. */
    reportHeard: (heardChars: number) => string | null
    /** Timing marks for the turn just ended (measurement only). */
    reportVoiceLatency?: (report: VoiceLatencyReport) => void
  }
}

const NOT_CONNECTED = 'Not connected.'

/** Wall-clock ms, comparable with the server's marks on the same machine. */
const wallNow = () => performance.timeOrigin + performance.now()

export function useVoiceSession(opts: Options): VoiceSession {
  const [state, setState] = useState<VoiceState>('off')
  const [wakeWordAvailable, setWakeWordAvailable] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [holding, setHolding] = useState(false)

  const captureRef = useRef<MicCapture | null>(null)
  const playerRef = useRef<PcmPlayer | null>(null)
  const wakeRef = useRef<WakeWordDetector | null>(null)
  const vadRef = useRef<ReturnType<typeof createVad> | null>(null)
  const ringRef = useRef<PcmRingBuffer | null>(null)
  const timerRef = useRef<number | null>(null)
  const micLevelRef = useRef(0)
  const vadDebugRef = useRef({ peak: 0, lastLog: 0 })
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
  // Bumped whenever the in-flight reply is abandoned (barge-in, disable).
  // Async continuations capture it and do nothing if it moved: a cancelled
  // turn settling late must not re-open the mic mid-way through the user's
  // interruption, which would discard the audio already streamed for it.
  const epochRef = useRef(0)
  // A held interruption. 'listening': the user is still talking and audio
  // streams; 'deciding': their utterance was sent and the server is working
  // out whether it was "I see", "stop", or a real turn.
  const holdRef = useRef<{
    stage: 'listening' | 'deciding'
    timer: number | null
  } | null>(null)
  // Timing marks for the in-flight voice turn, reported once when its first
  // audio is scheduled (or when it ends without any). See voice_latency.py.
  const vadEndRef = useRef<{ speechLastActive: number; vadEndpoint: number } | null>(null)
  const latencyRef = useRef<Omit<VoiceLatencyReport, 'outcome'> | null>(null)

  const reportLatency = useCallback((outcome: VoiceLatencyReport['outcome']) => {
    const trace = latencyRef.current
    if (!trace) return
    latencyRef.current = null
    optsRef.current.socket.reportVoiceLatency?.({ ...trace, outcome })
  }, [])

  const clearAudioGraceTimer = useCallback(() => {
    if (audioGraceTimerRef.current !== null) {
      window.clearTimeout(audioGraceTimerRef.current)
      audioGraceTimerRef.current = null
    }
  }, [])

  const setPhase = useCallback((next: VoiceState) => {
    stateRef.current = next
    setState(next)
    const speaking = next === 'speaking'
    vadRef.current?.setMinSpeechMs(speaking ? BARGE_IN_MIN_SPEECH_MS : DEFAULT_VAD.minSpeechMs)
    vadRef.current?.setSpeechRatio(speaking ? BARGE_IN_SPEECH_RATIO : DEFAULT_VAD.speechRatio)
  }, [])

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  /**
   * Switch to streaming mic audio into the utterance just opened. With
   * `speechInProgress` (a barge-in the VAD already confirmed) the VAD keeps
   * its state: resetting it would forget the speech under way, and that
   * utterance would then never see a speech-start nor be allowed to commit.
   */
  const enterListening = useCallback((speechInProgress: boolean) => {
    if (!speechInProgress) vadRef.current?.reset()
    speechDetectedRef.current = speechInProgress
    setPhase('listening')
    clearTimer()
  }, [clearTimer, setPhase])

  /** Open an utterance and start streaming mic audio to the server. */
  const beginUtterance = useCallback(() => {
    const { conversationId, socket } = optsRef.current
    if (!socket.startVoice(conversationId, CAPTURE_SAMPLE_RATE)) {
      // Stand down to idle rather than staying in whatever phase called us:
      // a turn that failed because the socket dropped would otherwise sit in
      // 'thinking' forever, even after the connection comes back.
      clearAudioGraceTimer()
      playerRef.current?.stop()
      setError(NOT_CONNECTED)
      setPhase('idle')
      return
    }
    setError((current) => (current === NOT_CONNECTED ? null : current))
    enterListening(false)
    // timerRef.current = window.setTimeout(() => {
    //   // Armed but nobody said anything — stand down rather than sit open.
    //   if (stateRef.current === 'listening' && !vadRef.current?.speaking) {
    //     optsRef.current.socket.cancelVoice()
    //     setPhase('idle')
    //   }
    // }, NO_SPEECH_TIMEOUT_MS)
  }, [clearAudioGraceTimer, enterListening, setPhase])

  /**
   * Wait for the reply to the utterance just closed, then re-arm for a
   * follow-up. `request` must be called before the first await: for an
   * adopted hold it registers the pending turn, and the reply's frames are
   * already arriving right behind the decision.
   */
  const awaitReply = useCallback(async (
    request: () => Promise<{ conversationId: string; reply: string }>,
  ) => {
    clearTimer()
    setPhase('thinking')
    audioArrivedRef.current = false
    // Safety net: if nothing at all comes back, don't think forever. Give up on
    // the turn (fencing any late reply), say so, and listen again.
    const turnEpoch = epochRef.current
    const noAnswer = window.setTimeout(() => {
      if (epochRef.current !== turnEpoch || stateRef.current !== 'thinking' || audioArrivedRef.current) return
      if (holdRef.current) return
      epochRef.current += 1
      const { conversationId, socket } = optsRef.current
      // Settles the stuck turn (its late events are fenced) and opens a new
      // utterance in the same frame.
      if (!socket.interruptVoiceStart(conversationId, CAPTURE_SAMPLE_RATE)) {
        setError(NOT_CONNECTED)
        setPhase('idle')
        return
      }
      setError('No answer came back. Please try again.')
      enterListening(false)
    }, NO_ANSWER_TIMEOUT_MS)
    replyDoneRef.current = false
    playbackDoneRef.current = false
    const { onReplyComplete } = optsRef.current
    const epoch = epochRef.current
    const vadEnd = vadEndRef.current
    vadEndRef.current = null
    latencyRef.current = {
      endpoint: vadEnd ? 'vad' : 'manual',
      marks: {
        ...(vadEnd
          ? { speech_last_active: vadEnd.speechLastActive, vad_endpoint: vadEnd.vadEndpoint }
          : {}),
        voice_end_sent: wallNow(),
      },
    }
    const reply = request()
    reply.finally(() => window.clearTimeout(noAnswer)).catch(() => {})
    try {
      const result = await reply
      if (epochRef.current !== epoch) {
        // Superseded by a barge-in. Keep the thread id, change nothing else.
        if (result.conversationId) onReplyComplete(result.conversationId, result.reply)
        return
      }
      replyDoneRef.current = true
      onReplyComplete(result.conversationId, result.reply)
      // Nothing to say (an empty transcript, or an interruption that was only
      // "stop"): no audio is coming, so don't sit out the 4s grace window.
      if (!result.reply.trim() && !audioArrivedRef.current) {
        reportLatency('no_audio')
        if (stateRef.current === 'thinking') beginUtterance()
        return
      }
    } catch (e) {
      if (epochRef.current !== epoch) return
      reportLatency('error')
      setError(e instanceof Error ? e.message : 'Voice turn failed.')
      if (stateRef.current === 'thinking') beginUtterance()
      return
    }
    if (audioArrivedRef.current && playbackDoneRef.current) {
      // During a hold the reply is paused; resuming re-checks this.
      if (!holdRef.current) beginUtterance()
      return
    }
    // Don't decide synchronously here — the first audio chunk may not have
    // arrived yet even on a normal turn (measured first-chunk latency is
    // ~1.9s). Give it a grace window; if nothing shows up, TTS is genuinely
    // down for this turn, so re-arm ourselves instead of stalling forever.
    if (stateRef.current === 'thinking' && !audioArrivedRef.current) {
      clearAudioGraceTimer()
      audioGraceTimerRef.current = window.setTimeout(() => {
        if (epochRef.current !== epoch) return
        if (stateRef.current === 'thinking' && !audioArrivedRef.current) {
          reportLatency('no_audio')
          beginUtterance()
        }
      }, 4000)
    }
  }, [beginUtterance, clearAudioGraceTimer, clearTimer, enterListening, reportLatency, setPhase])

  /** Close the utterance, run the turn, then re-arm for a follow-up. */
  const finishUtterance = useCallback(() => {
    const { conversationId, socket, onToken, onSticker } = optsRef.current
    return awaitReply(() => socket.endVoice(conversationId, onToken, onSticker))
  }, [awaitReply])

  /**
   * Cut the reply off for good: record how much of it was heard, then stop
   * audio and fence its late events. Not used when a hold resumes — the
   * user will hear the rest after all.
   */
  const abandonReply = useCallback(() => {
    reportLatency('interrupted')
    epochRef.current += 1
    clearAudioGraceTimer()
    const player = playerRef.current
    // Before stop(), which forgets what was scheduled.
    const heard = player?.heardTextEnd() ?? null
    if (heard !== null) {
      const { socket, onHeard } = optsRef.current
      const turnId = socket.reportHeard(heard)
      if (turnId) onHeard?.(turnId, heard)
    }
    player?.stop()
  }, [clearAudioGraceTimer, reportLatency])

  const handleAudioSegment = useCallback(
    (segment: number, textStart: number, textEnd: number) => {
      playerRef.current?.beginSegment(segment, textStart, textEnd)
    },
    [],
  )

  /**
   * Button barge-in: stop the assistant and open a new utterance in one
   * handoff. No pre-roll and a fresh VAD — there is no speech yet, and the
   * ring would only hold room tone and echo.
   */
  const hardInterrupt = useCallback(() => {
    const phase = stateRef.current
    if (phase !== 'speaking' && phase !== 'thinking') return
    abandonReply()
    const { conversationId, socket } = optsRef.current
    if (!socket.interruptVoiceStart(conversationId, CAPTURE_SAMPLE_RATE)) {
      setError(NOT_CONNECTED)
      setPhase('idle')
      return
    }
    enterListening(false)
  }, [abandonReply, enterListening, setPhase])

  const clearHold = useCallback(() => {
    const hold = holdRef.current
    if (hold?.timer != null) window.clearTimeout(hold.timer)
    holdRef.current = null
    setHolding(false)
    vadRef.current?.setHangoverMs(DEFAULT_VAD.hangoverMs)
  }, [])

  /**
   * Spoken barge-in: pause the reply (don't cancel it) and stream the user's
   * speech, starting from just before it began. `speechMs` is how much speech
   * the VAD needed to confirm it; the ring is sent from that far back, plus
   * padding, so the first word isn't clipped.
   */
  const startHold = useCallback(
    (speechMs: number) => {
      const phase = stateRef.current
      if (phase !== 'speaking' && phase !== 'thinking') return
      if (holdRef.current) return
      const { conversationId, socket } = optsRef.current
      // Not connected: she keeps talking, which beats silence that can't be
      // resolved by anyone.
      if (!socket.holdVoice(conversationId, CAPTURE_SAMPLE_RATE)) return
      playerRef.current?.pause()
      clearAudioGraceTimer()
      holdRef.current = { stage: 'listening', timer: null }
      setHolding(true)
      const ring = ringRef.current
      if (ring) {
        const onset = speechOnsetPosition(
          ring.endSamplePosition, speechMs, CAPTURE_SAMPLE_RATE, PREROLL_PADDING_MS,
        )
        // Sent once, here. The frame that confirmed the speech is already in
        // the ring, and handleFrame won't stream it again (see there).
        const preRoll = ring.snapshotFrom(onset)
        if (preRoll.length) socket.sendAudio(preRoll)
        if (import.meta.env.DEV) {
          console.debug(
            `[vad] hold in ${phase} after ${Math.round(speechMs)}ms: pre-roll ` +
              `${Math.round((preRoll.length / CAPTURE_SAMPLE_RATE) * 1000)}ms`,
          )
        }
      }
      // Keep the VAD's speech state: the utterance is already under way.
      enterListening(true)
      vadRef.current?.setHangoverMs(HOLD_HANGOVER_MS)
    },
    [clearAudioGraceTimer, enterListening],
  )

  /** Long enough to be a real turn: cancel the reply, keep listening. */
  const commitHold = useCallback(() => {
    if (holdRef.current?.stage !== 'listening') return
    if (import.meta.env.DEV) console.debug('[vad] hold committed: real turn')
    clearHold()
    abandonReply()
    optsRef.current.socket.commitHold()
  }, [abandonReply, clearHold])

  /** Return to the paused reply exactly where it left off. */
  const resumeAfterHold = useCallback(() => {
    if (audioArrivedRef.current) {
      setPhase('speaking')
      playerRef.current?.resume()
    } else {
      setPhase('thinking')
    }
    // The reply may have finished arriving while it was held.
    if (replyDoneRef.current && (!audioArrivedRef.current || playbackDoneRef.current)) {
      beginUtterance()
      return
    }
    // The user may have kept talking while the hold was being decided; the
    // VAD is still mid-speech, so no fresh speech-start will come for it.
    const vad = vadRef.current
    if (vad?.speaking) startHold(vad.stats.speechMs)
  }, [beginUtterance, setPhase, startHold])

  const resolveHold = useCallback(
    (action: BargeAction) => {
      if (!holdRef.current) return
      if (import.meta.env.DEV) console.debug(`[vad] hold resolved: ${action}`)
      clearHold()
      const { conversationId, socket, onToken, onSticker } = optsRef.current
      if (action === 'resume') {
        socket.resumeHold()
        resumeAfterHold()
        return
      }
      abandonReply()
      if (action === 'stop') {
        socket.stopHold()
        beginUtterance()
        return
      }
      void awaitReply(() => socket.adoptHold(conversationId, onToken, onSticker))
    },
    [abandonReply, awaitReply, beginUtterance, clearHold, resumeAfterHold],
  )

  /** The held utterance ended: send it off for a decision. */
  const endHold = useCallback(() => {
    const hold = holdRef.current
    if (hold?.stage !== 'listening') return
    if (!optsRef.current.socket.endHold()) {
      resolveHold('resume')
      return
    }
    hold.stage = 'deciding'
    vadRef.current?.setHangoverMs(DEFAULT_VAD.hangoverMs)
    hold.timer = window.setTimeout(() => resolveHold('resume'), HOLD_DECISION_TIMEOUT_MS)
  }, [resolveHold])

  /** The stop button: commits a hold, or cuts the reply off outright. */
  const interrupt = useCallback(() => {
    const hold = holdRef.current
    if (hold) {
      if (hold.stage === 'listening') commitHold()
      return
    }
    hardInterrupt()
  }, [commitHold, hardInterrupt])

  const handleFrame = useCallback(
    ({ pcm, rms }: { pcm: Int16Array; rms: number }) => {
      const phase = stateRef.current
      if (phase === 'off') return

      // Smoothed so the visualiser doesn't strobe on single 8ms frames.
      micLevelRef.current = micLevelRef.current * 0.7 + rms * 0.3
      wakeRef.current?.push(pcm)
      // Into the ring before the VAD: a barge-in confirmed by this very frame
      // snapshots the ring synchronously and must find the frame in it.
      ringRef.current?.push(pcm)
      vadRef.current?.push(rms, pcm.length)

      // `phase` was read before the VAD ran. If this frame triggered a
      // barge-in, the state is now 'listening' but the frame went out as
      // pre-roll — checking the stale phase is what keeps it from being sent
      // twice.
      const hold = holdRef.current
      if (phase === 'listening' && hold?.stage !== 'deciding') {
        optsRef.current.socket.sendAudio(pcm)
      }
      const vad = vadRef.current
      if (
        hold?.stage === 'listening' &&
        vad?.speaking &&
        vad.stats.speechMs >= HOLD_COMMIT_SPEECH_MS
      ) {
        commitHold()
      }

      // Dev diagnostics for "stuck listening": every 500ms, the loudest frame
      // vs the VAD threshold. Speech that never beats the threshold never
      // starts; a room that never drops below it never ends.
      if (import.meta.env.DEV && (phase === 'listening' || phase === 'speaking') && vadRef.current) {
        const dbg = vadDebugRef.current
        dbg.peak = Math.max(dbg.peak, rms)
        const now = performance.now()
        if (now - dbg.lastLog >= 500) {
          const s = vadRef.current.stats
          console.debug(
            `[vad] ${phase} peak=${dbg.peak.toFixed(4)} threshold=${s.threshold.toFixed(4)} ` +
              `floor=${s.noiseFloor.toFixed(4)} speaking=${s.speaking} ` +
              `speechMs=${Math.round(s.speechMs)} quietMs=${Math.round(s.quietMs)} ` +
              `heard=${speechDetectedRef.current}`,
          )
          dbg.peak = 0
          dbg.lastLog = now
        }
      }
    },
    [commitHold],
  )

  const handleVad = useCallback(
    (e: 'speech-start' | 'speech-end', ms: number) => {
      const phase = stateRef.current
      if (import.meta.env.DEV) console.debug(`[vad] ${e} in ${phase}`)

      if (e === 'speech-start') {
        // Barge-in: talking over the assistant cuts it off immediately.
        if (phase === 'speaking' || phase === 'thinking') {
          startHold(ms)
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
      if (phase === 'listening' && holdRef.current) {
        endHold()
        return
      }
      if (phase === 'listening') {
        const now = wallNow()
        vadEndRef.current = { speechLastActive: now - ms, vadEndpoint: now }
        void finishUtterance()
      }
    },
    [endHold, finishUtterance, startHold],
  )

  const enable = useCallback(async () => {
    if (stateRef.current !== 'off') return
    setError(null)
    try {
      playerRef.current = createPlayer()
      vadRef.current = createVad(CAPTURE_SAMPLE_RATE, handleVad)
      ringRef.current = createPcmRingBuffer(
        Math.round((CAPTURE_SAMPLE_RATE * PREROLL_RING_MS) / 1000),
      )
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
    clearHold()
    epochRef.current += 1
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
    ringRef.current = null
    vadEndRef.current = null
    latencyRef.current = null
    micLevelRef.current = 0
    setPhase('off')
  }, [clearAudioGraceTimer, clearHold, clearTimer, setPhase])

  const pushToTalk = useCallback(() => {
    if (stateRef.current === 'idle') beginUtterance()
  }, [beginUtterance])

  const endTurn = useCallback(() => {
    if (stateRef.current !== 'listening') return
    if (holdRef.current) {
      endHold()
      return
    }
    if (!speechDetectedRef.current) {
      optsRef.current.socket.cancelVoice()
      setPhase('idle')
      return
    }
    void finishUtterance()
  }, [endHold, finishUtterance, setPhase])

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

  const beginUnpromptedSpeech = useCallback(() => {
    const phase = stateRef.current
    if (phase === 'off' || holdRef.current) return
    if (phase === 'listening') {
      if (speechDetectedRef.current || vadRef.current?.speaking) return
      clearTimer()
      optsRef.current.socket.cancelVoice()
    } else if (phase !== 'idle') {
      return // replying already: this audio isn't for now
    }
    // Same bookkeeping as a reply, so playback re-arms listening when done.
    epochRef.current += 1
    audioArrivedRef.current = false
    playbackDoneRef.current = false
    replyDoneRef.current = true
    setPhase('speaking')
  }, [clearTimer, setPhase])

  const handleAudio = useCallback(
    (pcm: Int16Array, sampleRate: number) => {
      // Reply audio only belongs to thinking/speaking (or a nudge while idle).
      // Anything arriving while listening is a superseded reply leaking past
      // the fence; playing it would also feed her voice into the open mic.
      // A held reply keeps arriving into the paused player, ready to resume.
      if (stateRef.current === 'off') return
      if (stateRef.current === 'listening' && !holdRef.current) return
      audioArrivedRef.current = true
      clearAudioGraceTimer()
      // 'thinking' is a reply to something we asked. 'idle' can only be the
      // assistant speaking first — it still has to show as speaking, or the
      // orb sits still while a voice comes out of the machine.
      const phase = stateRef.current
      if (phase === 'thinking' || phase === 'idle') setPhase('speaking')
      const startsInSeconds = playerRef.current?.enqueue(pcm, sampleRate) ?? null
      const trace = latencyRef.current
      if (trace) {
        const now = wallNow()
        trace.marks.browser_first_audio = now
        if (startsInSeconds !== null) {
          trace.marks.browser_playback_started = now + startsInSeconds * 1000
        }
        reportLatency('audio')
      }
    },
    [clearAudioGraceTimer, reportLatency, setPhase],
  )

  const handleAudioEnd = useCallback(() => {
    // The server has finished *sending*; the queue is still draining. Re-arm
    // only once it's actually silent, or the follow-up window would open while
    // the assistant is still talking and immediately trip on its own voice.
    const epoch = epochRef.current
    void playerRef.current?.waitUntilDone().then(() => {
      if (epochRef.current !== epoch) return
      playbackDoneRef.current = true
      const phase = stateRef.current
      if ((phase === 'speaking' || phase === 'thinking') && replyDoneRef.current) {
        beginUtterance()
      }
    })
  }, [beginUtterance])

  useEffect(() => disable, [disable])

  useEffect(() => {
    if (opts.connected) setError((current) => (current === NOT_CONNECTED ? null : current))
  }, [opts.connected])

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
    holding,
    wakeWordAvailable,
    error,
    enable,
    disable,
    pushToTalk,
    endTurn,
    interrupt,
    getLevel,
    beginUnpromptedSpeech,
    handleAudio,
    handleAudioEnd,
    handleBargeResolved: resolveHold,
    handleAudioSegment,
  }
}
