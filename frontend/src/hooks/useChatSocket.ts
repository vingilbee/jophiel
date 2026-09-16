import { useCallback, useEffect, useRef, useState } from 'react'
import type { AwarenessSnapshot, SenseReport, StoredChatMessage } from '../types'

export type SocketStatus = 'connecting' | 'open' | 'closed'

// Server → client JSON frames. Binary frames (raw PCM) are handled separately.
type ServerEvent =
  | { type: 'meta'; conversation_id: string }
  | { type: 'history'; conversation_id: string | null; messages: StoredChatMessage[] }
  | { type: 'ack'; turn_id: string; user_message_id: string; conversation_id: string }
  | { type: 'token'; turn_id?: string; content: string }
  | { type: 'sticker'; turn_id?: string; message_id?: string; sticker: string }
  | { type: 'subagent_start'; turn_id?: string; agent: string }
  | { type: 'subagent_token'; turn_id?: string; agent: string; content: string }
  | { type: 'subagent_end'; turn_id?: string; agent: string }
  | { type: 'nudge_start'; conversation_id: string; reason: string }
  | { type: 'nudge_token'; content: string }
  | { type: 'nudge_sticker'; sticker: string }
  | { type: 'nudge_subagent_start'; agent: string }
  | { type: 'nudge_subagent_token'; agent: string; content: string }
  | { type: 'nudge_subagent_end'; agent: string }
  | { type: 'nudge_end' }
  | { type: 'state'; awareness: AwarenessSnapshot }
  | { type: 'audio_start'; sample_rate: number }
  | { type: 'audio_end' }
  | { type: 'transcript'; turn_id?: string; content: string }
  | { type: 'error'; turn_id?: string; detail: string }
  | { type: 'stopped' | 'done'; turn_id?: string; status?: ChatTurnStatus }

export type ChatTurnStatus = 'completed' | 'partial' | 'cancelled' | 'failed'

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
  onAudioStart?: (sampleRate: number) => void
  onAudio?: (pcm: Int16Array, sampleRate: number) => void
  onAudioEnd?: () => void
  /**
   * The agent is speaking *first* — unprompted, because something in its
   * awareness was worth mentioning. Nothing on the client asked for this, so
   * it arrives outside the request/response bookkeeping entirely.
   */
  onNudgeStart?: (info: { conversationId: string; reason: string }) => void
  onNudgeToken?: (chunk: string) => void
  onNudgeSticker?: (name: string) => void
  onNudgeSubagentStart?: (agent: string) => void
  onNudgeSubagentToken?: (agent: string, content: string) => void
  onNudgeSubagentEnd?: (agent: string) => void
  onNudgeEnd?: () => void
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

  const connect = useCallback(() => {
    if (!networkOnlineRef.current) {
      setStatus('closed')
      return
    }
    intentionalCloseRef.current = false
    const generation = ++socketGenerationRef.current
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${window.location.host}/api/chat/ws`)
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
        handlersRef.current.onHistory?.({
          conversationId: msg.conversation_id,
          messages: msg.messages,
        })
        return
      }
      if (msg.type === 'ack') return

      // Unprompted. Routed well clear of `pendingRef` — a nudge must never
      // resolve, or worse reject, a turn the user actually asked for.
      if (msg.type === 'nudge_start') {
        handlersRef.current.onNudgeStart?.({
          conversationId: msg.conversation_id,
          reason: msg.reason,
        })
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

      // Audio frames are not tied to the pending text promise — a voice turn
      // can be speaking while the caller has already resolved.
      if (msg.type === 'audio_start') {
        audioRateRef.current = msg.sample_rate
        handlersRef.current.onAudioStart?.(msg.sample_rate)
        return
      }
      if (msg.type === 'audio_end') {
        handlersRef.current.onAudioEnd?.()
        return
      }
      if (msg.type === 'transcript') {
        handlersRef.current.onTranscript?.(msg.content, msg.turn_id)
        return
      }

      const pending = pendingRef.current
      if (!pending) return
      const eventTurnId = 'turn_id' in msg ? msg.turn_id : undefined
      if (eventTurnId && pending.turnId && eventTurnId !== pending.turnId) return
      if (msg.type === 'meta') {
        pending.conversationId = msg.conversation_id
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

    ws.onclose = () => {
      if (!isCurrentSocket()) return
      setStatus('closed')
      // Fail any in-flight turn so the UI doesn't hang.
      if (pendingRef.current) {
        pendingRef.current.reject(new Error('Connection closed.'))
        pendingRef.current = null
      }
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
      if (pendingRef.current) {
        pendingRef.current.reject(new Error('Connection is offline.'))
        pendingRef.current = null
      }
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
    (conversationId: string | null, callbacks: TurnCallbacks) =>
      beginTurn(conversationId, callbacks, (ws) =>
        ws.send(JSON.stringify({ type: 'voice_end', turn_id: voiceTurnIdRef.current })),
        voiceTurnIdRef.current ?? crypto.randomUUID(),
      ),
    [beginTurn],
  )

  /** Discard the open utterance without running a turn. */
  const cancelVoice = useCallback(() => {
    const ws = wsRef.current
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

  return {
    status,
    awareness,
    sendMessage,
    sendSense,
    dismissNudges,
    stop,
    startVoice,
    sendAudio,
    endVoice,
    cancelVoice,
  }
}
