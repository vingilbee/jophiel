import { useCallback, useEffect, useRef, useState } from 'react'
import type { AwarenessSnapshot, SenseReport, StoredChatMessage, Thought } from '../types'

export type SocketStatus = 'connecting' | 'open' | 'closed'

// Server → client JSON frames. Binary frames (raw PCM) are handled separately.
type ServerEvent =
  | { type: 'meta'; conversation_id: string; turn_id?: string }
  | { type: 'history'; conversation_id: string | null; messages: StoredChatMessage[] }
  | { type: 'ack'; turn_id: string; user_message_id: string; conversation_id: string }
  | { type: 'token'; turn_id?: string; content: string }
  | { type: 'sticker'; turn_id?: string; message_id?: string; sticker: string }
  | { type: 'subagent_start'; turn_id?: string; agent: string }
  | { type: 'subagent_token'; turn_id?: string; agent: string; content: string }
  | { type: 'subagent_end'; turn_id?: string; agent: string }
  | { type: 'nudge_start'; conversation_id: string; reason: string; message_id?: string }
  | { type: 'reply_interrupted'; reason: string }
  | { type: 'nudge_token'; content: string }
  | { type: 'nudge_sticker'; sticker: string }
  | { type: 'nudge_subagent_start'; agent: string }
  | { type: 'nudge_subagent_token'; agent: string; content: string }
  | { type: 'nudge_subagent_end'; agent: string }
  | { type: 'nudge_end' }
  | { type: 'thought'; thought: Thought }
  | { type: 'state'; awareness: AwarenessSnapshot }
  | { type: 'audio_start'; turn_id?: string; sample_rate: number; unprompted?: boolean }
  | { type: 'audio_end'; turn_id?: string }
  | {
    type: 'audio_segment'
    turn_id?: string
    segment: number
    text_start: number
    text_end: number
  }
  | { type: 'transcript'; turn_id?: string; content: string }
  | { type: 'barge_resolved'; turn_id: string; action: BargeAction }
  | { type: 'error'; turn_id?: string; detail: string }
  | { type: 'stopped' | 'done'; turn_id?: string; status?: ChatTurnStatus }

export type ChatTurnStatus = 'completed' | 'partial' | 'cancelled' | 'failed'

/**
 * The server's decision on a held interruption: carry on with the paused
 * reply, drop it and stay quiet, or drop it and answer the new utterance.
 */
export type BargeAction = 'resume' | 'stop' | 'turn'

/** Browser-side timing stages of a voice turn (see backend voice_latency). */
export type VoiceLatencyStage =
  | 'speech_last_active'
  | 'vad_endpoint'
  | 'voice_end_sent'
  | 'browser_first_audio'
  | 'browser_playback_started'

export interface VoiceLatencyReport {
  outcome: 'audio' | 'no_audio' | 'error' | 'interrupted'
  endpoint: 'vad' | 'manual'
  /** Wall-clock epoch milliseconds per stage. */
  marks: Partial<Record<VoiceLatencyStage, number>>
}

// The full reply plus the (possibly newly created) conversation it belongs to.
export interface ChatTurnResult {
  conversationId: string
  reply: string
  status: ChatTurnStatus
}

/** Callbacks for everything that isn't a reply to a message we sent. */
export interface SocketHandlers {
  onHistory?: (history: { conversationId: string | null; messages: StoredChatMessage[] }) => void
  /** What the server transcribed from the user's speech. */
  onTranscript?: (text: string, turnId?: string) => void
  /** `unprompted`: she's speaking first (a game event), not replying. */
  onAudioStart?: (sampleRate: number, unprompted: boolean) => void
  onAudio?: (pcm: Int16Array, sampleRate: number) => void
  onAudioEnd?: () => void
  /** The audio that follows speaks this span of the reply's text. */
  onAudioSegment?: (segment: number, textStart: number, textEnd: number) => void
  /** A held interruption was decided (see `holdVoice`). */
  onBargeResolved?: (action: BargeAction) => void
  /**
   * Something urgent happened (her game): the server cut off the reply in
   * flight because it's out of date, and an unprompted message follows.
   */
  onReplyInterrupted?: () => void
  /**
   * The agent is speaking *first* — unprompted, because something in its
   * awareness was worth mentioning. Nothing on the client asked for this, so
   * it arrives outside the request/response bookkeeping entirely.
   */
  onNudgeStart?: (info: { conversationId: string; reason: string; messageId?: string }) => boolean | void
  onConversation?: (conversationId: string) => void
  onNudgeToken?: (chunk: string) => void
  onNudgeSticker?: (name: string) => void
  onNudgeSubagentStart?: (agent: string) => void
  onNudgeSubagentToken?: (agent: string, content: string) => void
  onNudgeSubagentEnd?: (agent: string) => void
  onNudgeEnd?: () => void
  /** A thought of her mind: shown in the thoughts panel, never a message. */
  onThought?: (thought: Thought) => void
}

interface TurnCallbacks {
  onToken: (chunk: string) => void
  onSticker?: (name: string) => void
  onSubagentStart?: (agent: string) => void
  onSubagentToken?: (agent: string, content: string) => void
  onSubagentEnd?: (agent: string) => void
}

interface Pending {
  callbacks: TurnCallbacks
  resolve: (result: ChatTurnResult) => void
  reject: (err: Error) => void
  buffer: string
  conversationId: string | null
  turnId?: string
  reconnecting?: boolean
}

/**
 * Maintains a single persistent WebSocket to /api/chat/ws (with auto-reconnect)
 * and exposes a promise-based `sendMessage`. One turn is in flight at a time;
 * the promise resolves with the full reply on `done`.
 *
 * Voice shares this socket rather than opening a second one, so a spoken turn
 * and a typed turn land in the same conversation with one auth handshake and
 * one reconnect story. Audio moves as binary frames; everything else is JSON.
 */
export function useChatSocket(handlers: SocketHandlers = {}) {
  const wsRef = useRef<WebSocket | null>(null)
  // Every connect attempt gets a generation. Events from an older socket may
  // arrive after a refresh/reconnect and must not change the current state.
  const socketGenerationRef = useRef(0)
  const pendingRef = useRef<Pending | null>(null)
  const intentionalCloseRef = useRef(false)
  const reconnectTimerRef = useRef<number | null>(null)
  const audioRateRef = useRef(32000)
  // Handlers change identity every render; keep them in a ref so `connect`
  // doesn't need them as deps and tear the socket down on each render.
  const handlersRef = useRef(handlers)
  handlersRef.current = handlers
  const [status, setStatus] = useState<SocketStatus>('connecting')
  const networkOnlineRef = useRef(
    typeof navigator === 'undefined' || navigator.onLine,
  )
  // Arrives on connect, on every sense update, and on a 30s heartbeat — slow
  // enough that holding it in state costs nothing, unlike the audio level.
  const [awareness, setAwareness] = useState<AwarenessSnapshot | null>(null)
  // The last report we sent, so re-reporting an unchanged sense is a no-op.
  const lastSenseRef = useRef<string>('')
  const voiceTurnIdRef = useRef<string | null>(null)
  // The voice turn whose timing marks haven't been reported yet. Separate from
  // voiceTurnIdRef, which is cleared on `done` and replaced by the next
  // utterance before the previous turn's report may have gone out.
  const latencyTurnIdRef = useRef<string | null>(null)
  // Barge-in fencing. Turns the user talked over, so their late audio (the
  // worker can still be mid-chunk, the relay mid-flight) never plays again,
  // and whether the current audio envelope belongs to one of them. Binary
  // frames carry no turn id, so they inherit it from the last `audio_start`.
  const fencedTurnsRef = useRef<string[]>([])
  const dropAudioRef = useRef(false)
  const ignoredNudgeRef = useRef(false)
  // An open hold: the utterance talking over a paused reply, and the voice
  // turn id to restore if the reply resumes.
  const heldRef = useRef<{ turnId: string; previousVoiceTurnId: string | null } | null>(null)
  // The turn whose audio is playing — not the pending turn: a reply's text
  // usually finishes (and settles) while its audio is still being spoken.
  const audioTurnIdRef = useRef<string | null>(null)
  // fenceInFlightTurn, for the message handler set up before it's defined.
  const fenceInFlightTurnRef = useRef<() => unknown>(() => {})
  // The turn a finished utterance is waiting on, so turning voice off can settle it.
  const voiceReplyTurnIdRef = useRef<string | null>(null)

  const connect = useCallback(() => {
    if (!networkOnlineRef.current) {
      setStatus('closed')
      return
    }
    intentionalCloseRef.current = false
    const generation = ++socketGenerationRef.current
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const resumeTurn = pendingRef.current?.turnId
    const query = resumeTurn ? `?resume_turn_id=${encodeURIComponent(resumeTurn)}` : ''
    const ws = new WebSocket(`${proto}://${window.location.host}/api/chat/ws${query}`)
    ws.binaryType = 'arraybuffer'
    wsRef.current = ws
    setStatus('connecting')

    const isCurrentSocket = () => (
      wsRef.current === ws && socketGenerationRef.current === generation
    )

    ws.onopen = () => {
      if (!isCurrentSocket()) return
      setStatus('open')
      // A reconnect gets a fresh server-side session that knows nothing about
      // this client — replay the last report so awareness resumes rather than
      // silently falling back to UTC and senses-off.
      if (lastSenseRef.current) ws.send(lastSenseRef.current)
    }

    ws.onmessage = (event) => {
      if (!isCurrentSocket()) return
      // Binary frame: streamed TTS audio for the reply being spoken.
      if (event.data instanceof ArrayBuffer) {
        if (dropAudioRef.current) return
        handlersRef.current.onAudio?.(
          new Int16Array(event.data),
          audioRateRef.current,
        )
        return
      }

      let msg: ServerEvent
      try {
        msg = JSON.parse(event.data as string)
      } catch {
        return
      }

      // Awareness is connection-scoped, not turn-scoped: it keeps arriving
      // between turns, which is the whole point of it.
      if (msg.type === 'state') {
        setAwareness(msg.awareness)
        return
      }

      if (msg.type === 'history') {
        const pending = pendingRef.current
        if (pending?.conversationId && msg.conversation_id !== pending.conversationId) return
        if (pending?.reconnecting) {
          pending.buffer = '' // the server replays from the beginning of the turn stream
          pending.reconnecting = false
          const saved = msg.messages.find(m => m.role === 'assistant' && m.turn_id === pending.turnId)
          if (saved) {
            pending.resolve({ conversationId: msg.conversation_id ?? '', reply: saved.content,
              status: saved.status ?? 'completed' })
            pendingRef.current = null
          }
        }
        handlersRef.current.onHistory?.({
          conversationId: msg.conversation_id,
          messages: msg.messages,
        })
        return
      }
      if (msg.type === 'ack') {
        const pending = pendingRef.current
        if (pending && (!pending.turnId || pending.turnId === msg.turn_id)) {
          pending.turnId = msg.turn_id
          pending.conversationId = msg.conversation_id
          handlersRef.current.onConversation?.(msg.conversation_id)
        }
        return
      }

      // Unprompted. Routed well clear of `pendingRef` — a nudge must never
      // resolve, or worse reject, a turn the user actually asked for.
      if (msg.type === 'reply_interrupted') {
        // Stop the stale reply where it is (and report what was heard) before
        // its turn is fenced, so the urgent message that follows plays next.
        handlersRef.current.onReplyInterrupted?.()
        fenceInFlightTurnRef.current()
        return
      }
      if (msg.type === 'nudge_start') {
        ignoredNudgeRef.current = handlersRef.current.onNudgeStart?.({
          conversationId: msg.conversation_id,
          reason: msg.reason,
          messageId: msg.message_id,
        }) === false
        return
      }
      if (msg.type === 'nudge_token') {
        handlersRef.current.onNudgeToken?.(msg.content)
        return
      }
      if (msg.type === 'nudge_sticker') {
        handlersRef.current.onNudgeSticker?.(msg.sticker)
        return
      }
      if (msg.type === 'nudge_end') {
        handlersRef.current.onNudgeEnd?.()
        return
      }
      if (msg.type === 'thought') {
        if (msg.thought) handlersRef.current.onThought?.(msg.thought)
        return
      }

      // Audio frames are not tied to the pending text promise — a voice turn
      // can be speaking while the caller has already resolved.
      if (msg.type === 'audio_start') {
        // Every reply's audio opens with this frame, so it is also where a
        // fence from a previous interruption is lifted for the next reply.
        dropAudioRef.current = (msg.unprompted === true && ignoredNudgeRef.current) ||
          (!!msg.turn_id && fencedTurnsRef.current.includes(msg.turn_id))
        if (dropAudioRef.current) return
        audioTurnIdRef.current = msg.turn_id ?? null
        audioRateRef.current = msg.sample_rate
        handlersRef.current.onAudioStart?.(msg.sample_rate, msg.unprompted === true)
        return
      }
      if (msg.type === 'audio_segment') {
        if (dropAudioRef.current) return
        handlersRef.current.onAudioSegment?.(msg.segment, msg.text_start, msg.text_end)
        return
      }
      if (msg.type === 'audio_end') {
        if (dropAudioRef.current || (msg.turn_id && fencedTurnsRef.current.includes(msg.turn_id))) {
          return
        }
        handlersRef.current.onAudioEnd?.()
        return
      }
      if (msg.type === 'transcript') {
        handlersRef.current.onTranscript?.(msg.content, msg.turn_id)
        return
      }
      if (msg.type === 'barge_resolved') {
        // Only for the hold still open; a stale decision changes nothing.
        if (heldRef.current?.turnId !== msg.turn_id) return
        handlersRef.current.onBargeResolved?.(msg.action)
        return
      }

      const pending = pendingRef.current
      if (!pending) return
      const eventTurnId = 'turn_id' in msg ? msg.turn_id : undefined
      if (eventTurnId && pending.turnId && eventTurnId !== pending.turnId) return
      if (msg.type === 'meta') {
        pending.conversationId = msg.conversation_id
        handlersRef.current.onConversation?.(msg.conversation_id)
      } else if (msg.type === 'token') {
        pending.buffer += msg.content
        pending.callbacks.onToken(msg.content)
      } else if (msg.type === 'sticker') {
        pending.callbacks.onSticker?.(msg.sticker)
      } else if (msg.type === 'subagent_start') {
        pending.callbacks.onSubagentStart?.(msg.agent)
      } else if (msg.type === 'subagent_token') {
        pending.callbacks.onSubagentToken?.(msg.agent, msg.content)
      } else if (msg.type === 'subagent_end') {
        pending.callbacks.onSubagentEnd?.(msg.agent)
      } else if (msg.type === 'done' || msg.type === 'stopped') {
        pending.resolve({
          conversationId: pending.conversationId ?? '',
          reply: pending.buffer,
          status: msg.status ?? (msg.type === 'stopped' ? 'cancelled' : 'completed'),
        })
        if (pending.turnId === voiceTurnIdRef.current) voiceTurnIdRef.current = null
        pendingRef.current = null
      } else if (msg.type === 'error') {
        pending.reject(new Error(msg.detail || 'Assistant error'))
        pendingRef.current = null
      }
    }

    ws.onclose = (event) => {
      if (!isCurrentSocket()) return
      setStatus('closed')
      heldRef.current = null
      if (event.code === 1008) {
        pendingRef.current?.reject(new Error('Session expired. Please sign in again.'))
        pendingRef.current = null
        return
      }
      // Keep the turn identity and callbacks: the replacement socket replays its stream.
      if (pendingRef.current) pendingRef.current.reconnecting = true
      if (!intentionalCloseRef.current && networkOnlineRef.current) {
        reconnectTimerRef.current = window.setTimeout(connect, 1500)
      }
    }
  }, [])

  useEffect(() => {
    const handleOffline = () => {
      networkOnlineRef.current = false
      setStatus('closed')
      const ws = wsRef.current
      wsRef.current = null
      ws?.close()
      if (pendingRef.current) pendingRef.current.reconnecting = true
    }
    const handleOnline = () => {
      networkOnlineRef.current = true
      if (wsRef.current?.readyState !== WebSocket.OPEN) connect()
    }

    window.addEventListener('offline', handleOffline)
    window.addEventListener('online', handleOnline)
    connect()
    return () => {
      intentionalCloseRef.current = true
      if (reconnectTimerRef.current) window.clearTimeout(reconnectTimerRef.current)
      window.removeEventListener('offline', handleOffline)
      window.removeEventListener('online', handleOnline)
      wsRef.current?.close()
      pendingRef.current?.reject(new Error('Chat closed.'))
      pendingRef.current = null
    }
  }, [connect])

  /** Register the pending turn and return the promise the caller awaits. */
  const beginTurn = useCallback(
    (
      conversationId: string | null,
      callbacks: TurnCallbacks,
      send: (ws: WebSocket) => void,
      turnId?: string,
    ) =>
      new Promise<ChatTurnResult>((resolve, reject) => {
        const ws = wsRef.current
        if (!ws || ws.readyState !== WebSocket.OPEN) {
          reject(new Error('Not connected. Reconnecting…'))
          return
        }
        if (pendingRef.current) {
          reject(new Error('A response is already in progress.'))
          return
        }
        pendingRef.current = { callbacks, resolve, reject, buffer: '', conversationId, turnId }
        send(ws)
      }),
    [],
  )

  /**
   * Send one new user message. Only the message text is sent — history lives in
   * the DB. Pass the current conversation id (or null to start a new one); the
   * resolved id comes back in the result so the caller can keep the thread.
   *
   * `imageIds` are photos already uploaded via POST /api/images. Ids rather
   * than pixels: the socket frame stays small, and the agent hands the id
   * straight to the food-vision specialist.
   */
  const sendMessage = useCallback(
    (
      conversationId: string | null,
      content: string,
      callbacks: TurnCallbacks,
      imageIds: string[] = [],
      assetIds: string[] = [],
      turnId?: string,
    ) =>
      beginTurn(conversationId, callbacks, (ws) =>
        ws.send(
          JSON.stringify({
            type: 'run',
            conversation_id: conversationId,
            turn_id: turnId,
            content,
            image_ids: imageIds,
            asset_ids: assetIds,
          }),
        ),
        turnId,
      ),
    [beginTurn],
  )

  /**
   * Report this client's senses (time zone, route, mic state). Cheap and
   * idempotent: an unchanged report is dropped rather than sent, so callers can
   * fire it freely from an effect. Sending while disconnected queues it for the
   * next open instead of failing.
   */
  const sendSense = useCallback((report: SenseReport) => {
    const payload = JSON.stringify({ type: 'sense', ...report })
    if (payload === lastSenseRef.current) return
    lastSenseRef.current = payload
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) ws.send(payload)
  }, [])

  /**
   * Tell the server to stop speaking first. Blunt by design — silence is
   * ambiguous and only counts toward a backoff streak, whereas this mutes
   * unprompted messages outright.
   */
  const dismissNudges = useCallback(() => {
    const ws = wsRef.current
    if (ws?.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'nudge_feedback', action: 'dismiss' }))
  }, [])

  /** Open an utterance. Binary frames sent after this are its audio. */
  const startVoice = useCallback(
    (conversationId: string | null, sampleRate: number) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) return false
      const turnId = crypto.randomUUID()
      voiceTurnIdRef.current = turnId
      ws.send(
        JSON.stringify({
          type: 'voice_start',
          conversation_id: conversationId,
          turn_id: turnId,
          sample_rate: sampleRate,
        }),
      )
      return true
    },
    [],
  )

  const sendAudio = useCallback((pcm: Int16Array) => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    // Send a byte view over the frame rather than slicing the buffer: it's
    // zero-copy, and `TypedArray.buffer` is ArrayBufferLike which `send` won't
    // take directly.
    ws.send(
      new Uint8Array(pcm.buffer as ArrayBuffer, pcm.byteOffset, pcm.byteLength),
    )
  }, [])

  /** Close the utterance and run the turn. Resolves like `sendMessage`. */
  const endVoice = useCallback(
    (conversationId: string | null, callbacks: TurnCallbacks) => {
      const turnId = voiceTurnIdRef.current ?? crypto.randomUUID()
      return beginTurn(conversationId, callbacks, (ws) => {
        latencyTurnIdRef.current = voiceTurnIdRef.current
        voiceReplyTurnIdRef.current = turnId
        ws.send(JSON.stringify({ type: 'voice_end', turn_id: voiceTurnIdRef.current }))
      }, turnId)
    },
    [beginTurn],
  )

  /**
   * Send the browser's timing marks for the last voice turn. At most once per
   * turn; measurement only, so a closed socket just drops it.
   */
  const reportVoiceLatency = useCallback((report: VoiceLatencyReport) => {
    const turnId = latencyTurnIdRef.current
    latencyTurnIdRef.current = null
    const ws = wsRef.current
    if (!turnId || !ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'voice_latency', turn_id: turnId, ...report }))
  }, [])

  /**
   * Discard the open utterance without running a turn. A spoken turn still
   * waiting for its reply is stopped and settled too: otherwise, after a stuck
   * reply and a voice restart, every new utterance was refused as "already in
   * progress".
   */
  const cancelVoice = useCallback(() => {
    const ws = wsRef.current
    const pending = pendingRef.current
    if (pending?.turnId && pending.turnId === voiceReplyTurnIdRef.current) {
      fenceInFlightTurnRef.current()
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'stop', conversation_id: pending.conversationId, turn_id: pending.turnId }))
      }
    }
    voiceReplyTurnIdRef.current = null
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(JSON.stringify({ type: 'voice_cancel' }))
    voiceTurnIdRef.current = null
  }, [])

  /**
   * Interrupt the reply currently streaming. Also the barge-in signal: the
   * server stops generating and stops synthesizing, persists the partial text,
   * and sends `stopped` — which resolves the in-flight promise.
   */
  const stop = useCallback(() => {
    const ws = wsRef.current
    if (!ws || ws.readyState !== WebSocket.OPEN) return
    ws.send(
      JSON.stringify({
        type: 'stop',
        conversation_id: pendingRef.current?.conversationId ?? null,
        turn_id: pendingRef.current?.turnId,
      }),
    )
  }, [])

  /**
   * Fence the in-flight turn and settle it locally as cancelled. Returns what
   * the caller needs to tell the server which turn that was.
   */
  const fenceInFlightTurn = useCallback(() => {
    const pending = pendingRef.current
    const turnId = pending?.turnId ?? null
    // Silence whatever audio envelope is open now; the next reply's
    // `audio_start` lifts this unless it belongs to a fenced turn.
    dropAudioRef.current = true
    if (turnId) {
      fencedTurnsRef.current = [...fencedTurnsRef.current, turnId].slice(-16)
    }
    const conversationId = pending?.conversationId ?? null
    if (pending) {
      pendingRef.current = null
      if (pending.turnId === voiceTurnIdRef.current) voiceTurnIdRef.current = null
      pending.resolve({
        conversationId: conversationId ?? '',
        reply: pending.buffer,
        status: 'cancelled',
      })
    }
    return { turnId, conversationId }
  }, [])
  fenceInFlightTurnRef.current = fenceInFlightTurn

  /**
   * Barge-in: cut off the reply being spoken or prepared so a new utterance
   * can start immediately. Unlike `stop`, the old turn is settled here, as
   * cancelled, instead of waiting for the server's `stopped` (teardown can
   * take up to 2s, and a short "wait, what?" would otherwise be rejected as
   * "already in progress"). Its late audio and events are fenced off.
   */
  const interruptVoice = useCallback(() => {
    const ws = wsRef.current
    const { turnId, conversationId } = fenceInFlightTurn()
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'stop', conversation_id: conversationId, turn_id: turnId }))
    }
  }, [fenceInFlightTurn])

  /**
   * Barge-in handoff in one frame: cut off the reply (as `interruptVoice`
   * does) and open a new utterance. Binary frames sent after this belong to
   * the new utterance — WebSocket keeps order, so pre-roll can follow at once.
   * Returns false, changing nothing, when the socket isn't open.
   */
  const interruptVoiceStart = useCallback(
    (conversationId: string | null, sampleRate: number) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) return false
      const cancelled = fenceInFlightTurn()
      const newTurnId = crypto.randomUUID()
      voiceTurnIdRef.current = newTurnId
      ws.send(JSON.stringify({
        type: 'voice_interrupt',
        cancel_turn_id: cancelled.turnId,
        new_turn_id: newTurnId,
        // The cut-off turn may have just created the thread (its `meta`
        // arrived, but the caller's state hasn't caught up yet). Without
        // this the interruption would land in a brand-new conversation.
        conversation_id: cancelled.conversationId ?? conversationId,
        sample_rate: sampleRate,
      }))
      return true
    },
    [fenceInFlightTurn],
  )

  /**
   * Barge-in as a *hold*: open an utterance over the reply without cancelling
   * it. The reply keeps streaming (the caller has paused playback); the
   * server decides once the utterance ends, via `barge_resolved`. Returns
   * false, changing nothing, when the socket isn't open.
   */
  const holdVoice = useCallback(
    (conversationId: string | null, sampleRate: number) => {
      const ws = wsRef.current
      if (!ws || ws.readyState !== WebSocket.OPEN) return false
      const pending = pendingRef.current
      const turnId = crypto.randomUUID()
      heldRef.current = { turnId, previousVoiceTurnId: voiceTurnIdRef.current }
      voiceTurnIdRef.current = turnId
      ws.send(JSON.stringify({
        type: 'voice_interrupt',
        tentative: true,
        cancel_turn_id: pending?.turnId ?? null,
        new_turn_id: turnId,
        conversation_id: pending?.conversationId ?? conversationId,
        sample_rate: sampleRate,
      }))
      return true
    },
    [],
  )

  /** The hold is clearly a real turn: cut the reply off now, keep listening. */
  const commitHold = useCallback(() => {
    const held = heldRef.current
    if (!held) return
    heldRef.current = null
    fenceInFlightTurn()
    const ws = wsRef.current
    if (ws?.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'voice_interrupt_commit', turn_id: held.turnId }))
    }
  }, [fenceInFlightTurn])

  /** The held utterance is over: send it for a decision. */
  const endHold = useCallback(() => {
    const held = heldRef.current
    const ws = wsRef.current
    if (!held || !ws || ws.readyState !== WebSocket.OPEN) return false
    ws.send(JSON.stringify({ type: 'voice_end', turn_id: held.turnId }))
    return true
  }, [])

  /** Decision `resume`: the reply carries on as the voice turn. */
  const resumeHold = useCallback(() => {
    const held = heldRef.current
    if (!held) return
    heldRef.current = null
    voiceTurnIdRef.current = held.previousVoiceTurnId
  }, [])

  /** Decision `stop`: the server stopped the reply; settle it here too. */
  const stopHold = useCallback(() => {
    if (!heldRef.current) return
    heldRef.current = null
    fenceInFlightTurn()
    voiceTurnIdRef.current = null
  }, [fenceInFlightTurn])

  /**
   * Decision `turn`: fence the held reply and take the held utterance as the
   * pending turn. Its transcript and reply follow `barge_resolved` on the
   * socket, so this must run synchronously from that handler.
   */
  const adoptHold = useCallback(
    (conversationId: string | null, callbacks: TurnCallbacks) => {
      const held = heldRef.current
      if (!held) return Promise.reject(new Error('No held utterance.'))
      heldRef.current = null
      const cancelled = fenceInFlightTurn()
      latencyTurnIdRef.current = held.turnId
      return beginTurn(cancelled.conversationId ?? conversationId, callbacks, () => {}, held.turnId)
    },
    [beginTurn, fenceInFlightTurn],
  )

  /**
   * Tell the server how much of the reply being spoken was heard before the
   * user cut it off. Returns that reply's turn id, or null if there's none.
   */
  const reportHeard = useCallback((heardChars: number) => {
    const turnId = audioTurnIdRef.current
    const ws = wsRef.current
    if (!turnId || !ws || ws.readyState !== WebSocket.OPEN) return null
    ws.send(JSON.stringify({ type: 'voice_heard', turn_id: turnId, heard_chars: heardChars }))
    return turnId
  }, [])

  return {
    status,
    awareness,
    sendMessage,
    sendSense,
    dismissNudges,
    stop,
    interruptVoice,
    interruptVoiceStart,
    holdVoice,
    commitHold,
    endHold,
    resumeHold,
    stopHold,
    adoptHold,
    reportHeard,
    startVoice,
    sendAudio,
    endVoice,
    cancelVoice,
    reportVoiceLatency,
  }
}
