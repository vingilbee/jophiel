import {
  memo,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { Link } from 'react-router-dom'
import { chatApi } from '../api/chat'
import { imageUrl, uploadImage } from '../api/images'
import { uploadAsset } from '../api/assets'
import profileIcon from '../assets/profile_icon.jpg'
import chatWallpaper from '../assets/chat-wallpaper.jpg'
import { ChatContent, DocumentAttachment } from '../components/DocumentAttachment'
import { CognitiveInspector } from '../components/CognitiveInspector'
import { useAwareness } from '../hooks/useAwareness'
import { useChatSocket } from '../hooks/useChatSocket'
import { useVoiceSession, type VoiceSession } from '../hooks/useVoiceSession'
import { useScreenSense } from '../hooks/useScreenSense'
import { api } from '../api/client'
import { ChatSessionTools } from '../components/ChatSessionTools'
import { ThoughtStream } from '../components/ThoughtStream'
import { MoodStrip } from '../components/MoodStrip'
import { mindApi } from '../api/mind'
import { VoiceOrb } from '../components/VoiceOrb'
import type { ChatMessage, MessagePart, Thought } from '../types'

const SUGGESTIONS = [
  'How much protein is in two eggs?',
  'What are good high-protein snacks?',
  'Explain macros like I am new to this.',
]

// Matches the backend's per-turn cap (schemas/chat.py).
const MAX_ATTACHMENTS = 8

const STICKER_MARKER = /\[\[sticker:([a-z0-9-]+)\]\]/g
const STICKER_NAMES = new Set([
  'confused', 'question', 'love', 'thinking', 'good-job', 'sleepy', 'yum',
  'tea', 'hmm', 'shy', 'sorry', 'idea', 'encouragement', 'silent', 'scribble',
  'grateful', 'surprised', 'peeking', 'embarrassed', 'praying', 'determined',
  'hungry', 'see-you',
])
const stickerUrl = (name: string) => `/api/chat/stickers/${encodeURIComponent(name)}.png`

function partsFromContent(content: string): MessagePart[] {
  const parts: MessagePart[] = []
  let cursor = 0
  for (const match of content.matchAll(STICKER_MARKER)) {
    if (match.index === undefined) continue
    if (match.index > cursor) parts.push({ kind: 'text', content: content.slice(cursor, match.index) })
    const name = match[1]
    if (STICKER_NAMES.has(name)) parts.push({ kind: 'sticker', name })
    cursor = match.index + match[0].length
  }
  if (cursor < content.length) parts.push({ kind: 'text', content: content.slice(cursor) })
  return parts
}

const CHAT_NAV = [
  { to: '/chat', label: 'Chat' },
  { to: '/predict', label: 'Scan Meal' },
  { to: '/integrations', label: 'Integrations' },
]

/** A photo attached to the message being composed, before it's sent. */
interface Attachment {
  id: string
  kind: 'image' | 'asset'
  filename: string
  /** Object URL for the local preview, revoked once the turn is sent. */
  previewUrl: string
}

export function ChatPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [cognitiveInspectorOpen, setCognitiveInspectorOpen] = useState(false)
  const [voiceEnabled, setVoiceEnabled] = useState(false)
  const [voiceStarting, setVoiceStarting] = useState(false)
  const [sessionPanelOpen, setSessionPanelOpen] = useState(false)
  const voiceRef = useRef<VoiceSession | null>(null)
  const voiceMessageRef = useRef<string | undefined>(undefined)
  const eye = useScreenSense()
  const conversationIdRef = useRef<string | null>(null)
  conversationIdRef.current = conversationId

  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const streamingRef = useRef(false)
  const wsHistoryReceivedRef = useRef(false)
  // Follow-ups are serialized so an interruption can persist its partial
  // assistant reply before the injected message starts against that history.
  const turnQueueRef = useRef<Promise<void>>(Promise.resolve())
  const nudgeMessageRef = useRef<string | null>(null)
  const seenNudgesRef = useRef(new Set<string>())
  // Her mind's thoughts while playing: their own panel, never messages.
  const [thoughts, setThoughts] = useState<Thought[]>([])
  useEffect(() => {
    let cancelled = false
    mindApi.listThoughts().then((loaded) => { if (!cancelled) setThoughts(loaded) }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  /** Append orchestrator text to the last text part of the assistant bubble
   * being written, starting a new text part if the last part was a subagent
   * segment (or there isn't one yet). */
  function appendToken(chunk: string, targetId?: string) {
    setMessages((prev) => {
      const next = [...prev]
      const index = targetId
        ? next.findIndex((message) => message.clientId === targetId)
        : next.length - 1
      const target = next[index]
      if (!target || target.role !== 'assistant') return prev
      const parts = [...target.parts]
      const lastPart = parts[parts.length - 1]
      if (lastPart?.kind === 'text') {
        parts[parts.length - 1] = { ...lastPart, content: lastPart.content + chunk }
      } else {
        parts.push({ kind: 'text', content: chunk })
      }
      next[index] = { ...target, parts }
      return next
    })
  }

  /** Open a new collapsible segment for a specialist's reasoning. */
  function startSubagent(agent: string, targetId?: string) {
    setMessages((prev) => {
      const next = [...prev]
      const index = targetId
        ? next.findIndex((message) => message.clientId === targetId)
        : next.length - 1
      const target = next[index]
      if (!target || target.role !== 'assistant') return prev
      next[index] = {
        ...target,
        parts: [...target.parts, { kind: 'subagent', agent, content: '' }],
      }
      return next
    })
  }

  /** Append to the most recent open segment for `agent`. */
  function appendSubagentToken(agent: string, chunk: string, targetId?: string) {
    setMessages((prev) => {
      const next = [...prev]
      const index = targetId
        ? next.findIndex((message) => message.clientId === targetId)
        : next.length - 1
      const target = next[index]
      if (!target || target.role !== 'assistant') return prev
      const parts = [...target.parts]
      for (let i = parts.length - 1; i >= 0; i--) {
        const part = parts[i]
        if (part.kind === 'subagent' && part.agent === agent) {
          parts[i] = { ...part, content: part.content + chunk }
          break
        }
      }
      next[index] = { ...target, parts }
      return next
    })
  }

  function appendSticker(name: string, targetId?: string) {
    setMessages((prev) => {
      const next = [...prev]
      const index = targetId
        ? next.findIndex((message) => message.clientId === targetId)
        : next.length - 1
      const target = next[index]
      if (!target || target.role !== 'assistant') return prev
      next[index] = { ...target, parts: [...target.parts, { kind: 'sticker', name }] }
      return next
    })
  }

  // One socket owns typed turns, spoken turns, audio, and awareness.
  const socket = useChatSocket({
    onTranscript: (text, turnId) => {
      const clientId = crypto.randomUUID()
      voiceMessageRef.current = clientId
      setMessages((prev) => [
        ...prev,
        { role: 'user', parts: [{ kind: 'text', content: text }], turnId },
        ...(voiceRef.current?.state === 'thinking'
          ? [{ role: 'assistant' as const, parts: [], turnId, clientId }] : []),
      ])
    },
    onAudioStart: (_rate, unprompted) => {
      if (unprompted) voiceRef.current?.beginUnpromptedSpeech()
    },
    onAudio: (pcm, rate) => voiceRef.current?.handleAudio(pcm, rate),
    onAudioEnd: () => voiceRef.current?.handleAudioEnd(),
    onAudioSegment: (segment, start, end) => voiceRef.current?.handleAudioSegment(segment, start, end),
    onBargeResolved: (action) => voiceRef.current?.handleBargeResolved(action),
    onReplyInterrupted: () => voiceRef.current?.yieldToEvent(),
    onConversation: (id) => {
      conversationIdRef.current = id
      setConversationId(id)
    },
    onHistory: ({ conversationId: historyConversationId, messages: historyMessages }) => {
      if (wsHistoryReceivedRef.current && historyConversationId !== conversationIdRef.current) return
      wsHistoryReceivedRef.current = true
      for (const message of historyMessages) {
        if (message.role === 'assistant') seenNudgesRef.current.add(message.id)
      }
      while (seenNudgesRef.current.size > 200) seenNudgesRef.current.delete(seenNudgesRef.current.values().next().value!)
      setConversationId(historyConversationId)
      conversationIdRef.current = historyConversationId
      setMessages(prev => {
        const restored = historyMessages.map(({ role, content, image_ids, asset_ids, turn_id, status }) => ({
          role, parts: content ? partsFromContent(content) : [], turnId: turn_id ?? undefined, status,
          imageIds: image_ids ?? undefined, assetIds: asset_ids ?? undefined,
          clientId: prev.find(m => m.role === role && m.turnId === turn_id)?.clientId,
        }))
        // Keep the pending callback's target bubble, clearing partial text before full replay.
        const pending = prev.filter(m => m.role === 'assistant' && m.clientId && m.turnId &&
          historyMessages.some(h => h.role === 'user' && h.turn_id === m.turnId) &&
          !restored.some(h => h.role === 'assistant' && h.turnId === m.turnId))
        return [...restored, ...pending.map(m => ({ ...m, parts: [] }))]
      })
    },
    // The assistant speaking first. It opens its own bubble — there is no user
    // message before it, which is exactly what makes it worth marking.
    onNudgeStart: ({ conversationId: target, messageId }) => {
      nudgeMessageRef.current = null
      // Background output never changes the selected conversation or its composer.
      if (target !== conversationIdRef.current) return false
      if (messageId && seenNudgesRef.current.has(messageId)) return false
      if (messageId) {
        seenNudgesRef.current.add(messageId)
        if (seenNudgesRef.current.size > 200) seenNudgesRef.current.delete(seenNudgesRef.current.values().next().value!)
      }
      const clientId = crypto.randomUUID()
      nudgeMessageRef.current = clientId
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', parts: [], unprompted: true, clientId },
      ])
    },
    onNudgeEnd: () => { nudgeMessageRef.current = null },
    onThought: (thought) => setThoughts((prev) =>
      prev.some((t) => t.id === thought.id) ? prev : [...prev, thought].slice(-200)),
    onNudgeToken: (chunk) => { if (nudgeMessageRef.current) appendToken(chunk, nudgeMessageRef.current) },
    onNudgeSticker: (name) => { if (nudgeMessageRef.current) appendSticker(name, nudgeMessageRef.current) },
    onNudgeSubagentStart: (agent) => { if (nudgeMessageRef.current) startSubagent(agent, nudgeMessageRef.current) },
    onNudgeSubagentToken: (agent, chunk) => { if (nudgeMessageRef.current) appendSubagentToken(agent, chunk, nudgeMessageRef.current) },
  })

  const { status, sendMessage, sendSense, dismissNudges, stop, awareness } = socket
  const voice = useVoiceSession({
    conversationId,
    connected: status === 'open',
    onToken: (chunk) => appendToken(chunk, voiceMessageRef.current),
    onSticker: (name) => appendSticker(name, voiceMessageRef.current),
    onReplyComplete: (id) => {
      if (id && voiceRef.current?.state !== 'off') {
        conversationIdRef.current = id
        setConversationId(id)
      }
    },
    socket: {
      ...socket,
      endVoice: (id, onToken, onSticker) => socket.endVoice(id, { onToken, onSticker }),
      adoptHold: (id, onToken, onSticker) => socket.adoptHold(id, { onToken, onSticker }),
    },
  })
  voiceRef.current = voice
  const voiceLive = voice.state !== 'off'
  const voiceBusy = voice.state === 'thinking' || voice.state === 'speaking' || voice.holding
  const voiceLabel = voice.muted ? 'Mic muted' : {
    off: 'Ready', idle: 'Ready to listen', listening: 'Listening', thinking: 'Thinking', speaking: 'Speaking',
  }[voice.state]

  useEffect(() => {
    let cancelled = false
    api.get<{ voice_enabled: boolean }>('/capabilities')
      .then((caps) => { if (!cancelled) setVoiceEnabled(caps.voice_enabled) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!['listening', 'off', 'idle'].includes(voice.state) || voice.holding) return
    setMessages((prev) => prev.filter((message) =>
      message.clientId !== voiceMessageRef.current || message.parts.length > 0,
    ))
  }, [voice.state, voice.holding])

  async function startVoice() {
    if (streaming || voiceStarting || status !== 'open') return
    setVoiceStarting(true)
    try { await voice.enable() } finally { setVoiceStarting(false) }
  }

  function requestStop() {
    if (voiceLive && voiceBusy) { voice.interrupt(); return }
    if (!streamingRef.current || stopping) return
    setStopping(true)
    stop()
  }

  useAwareness({
    sendSense,
    activity: voice.state === 'listening' && !voice.muted ? 'speaking' : input.length > 0 ? 'typing' : 'reading',
    hearingEnabled: voiceLive && !voice.muted,
    wakeArmed: voiceLive && !voice.muted && voice.wakeWordAvailable && voice.state === 'idle',
    voiceState: voice.state,
    visionEnabled: eye.status === 'open',
    screenImageId: eye.imageId,
  })

  // Load the most recent conversation's history on mount (history is server-side).
  useEffect(() => {
    let cancelled = false
    // WebSocket history is authoritative. HTTP is only a recovery path for a
    // socket that cannot deliver its bootstrap frame, and must never overwrite
    // a snapshot already received from the socket.
    const fallbackTimer = window.setTimeout(() => {
      if (wsHistoryReceivedRef.current) return
      chatApi
        .listConversations()
        .then((conversations) => {
          if (cancelled || wsHistoryReceivedRef.current || conversations.length === 0) return
          return chatApi.getConversation(conversations[0].id).then((detail) => {
            if (cancelled || wsHistoryReceivedRef.current) return
            setConversationId(detail.id)
            setMessages(
              detail.messages.map(({ role, content, image_ids, asset_ids, turn_id, status }) => ({
                role,
                parts: content ? partsFromContent(content) : [],
                turnId: turn_id ?? undefined,
                status,
                imageIds: image_ids ?? undefined,
                assetIds: asset_ids ?? undefined,
              })),
            )
          })
        })
        .catch(() => {
          /* No history yet, or it failed to load — start with an empty chat. */
        })
    }, 2000)
    return () => {
      cancelled = true
      window.clearTimeout(fallbackTimer)
    }
  }, [])

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [messages, streaming])

  function startNewChat() {
    if (streaming || voiceBusy || voiceStarting) return
    voice.disable()
    setConversationId(null)
    setMessages([])
    setError(null)
    clearAttachments()
  }

  function clearAttachments() {
    setAttachments((prev) => {
      prev.filter((a) => a.kind === 'image').forEach((a) => URL.revokeObjectURL(a.previewUrl))
      return []
    })
  }

  /**
   * Upload picked files straight away rather than at send time, so the id
   * exists before the turn starts and a slow upload can't stall the message.
   */
  async function attachFiles(files: FileList | null) {
    if (!files?.length || streaming) return
    const room = MAX_ATTACHMENTS - attachments.length
    if (room <= 0) {
      setError(`You can attach at most ${MAX_ATTACHMENTS} files to one message.`)
      return
    }

    setError(null)
    setUploading(true)
    try {
      for (const file of Array.from(files).slice(0, room)) {
        const isImage = file.type.startsWith('image/')
        if (isImage) {
          const stored = await uploadImage(file)
          setAttachments((prev) => [...prev, {
            id: stored.image_id, kind: 'image', filename: file.name,
            previewUrl: URL.createObjectURL(file),
          }])
        } else {
          const stored = await uploadAsset(file)
          setAttachments((prev) => [...prev, {
            id: stored.asset_id, kind: 'asset', filename: stored.filename,
            previewUrl: '',
          }])
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not upload that file.')
    } finally {
      setUploading(false)
    }
  }

  function removeAttachment(id: string) {
    setAttachments((prev) => {
      prev.filter((a) => a.id === id && a.kind === 'image').forEach((a) => URL.revokeObjectURL(a.previewUrl))
      return prev.filter((a) => a.id !== id)
    })
  }

  async function send(text: string) {
    const trimmed = text.trim()
    // A photo on its own is a valid turn — the agent is told to analyze it.
    if ((!trimmed && attachments.length === 0) || status !== 'open' || voiceBusy || voiceStarting) return

    const imageIds = attachments.filter((a) => a.kind === 'image').map((a) => a.id)
    const assetIds = attachments.filter((a) => a.kind === 'asset').map((a) => a.id)
    // Attachments on their own are valid turns, but the backend schema still
    // requires an instruction for the orchestrator.
    const content = trimmed || (
      assetIds.length > 0
        ? 'Help me with the attached file.'
        : 'What is in this photo, and what are its macros?'
    )

    // Capture and clear the composer immediately. When a turn is active this
    // is a barge-in: stop it now, then enqueue this message after `stopped`.
    if (voiceLive) voice.pauseForText()
    setInput('')
    clearAttachments()
    if (streamingRef.current) requestStop()

    const assistantId = crypto.randomUUID()
    const turnId = crypto.randomUUID()
    setMessages((current) => [
      ...current,
      { role: 'user', parts: [{ kind: 'text', content }], imageIds, assetIds, turnId },
      { role: 'assistant', parts: [], clientId: assistantId, turnId, status: 'completed' },
    ])

    const run = async () => {
      setError(null)
      streamingRef.current = true
      setStopping(false)
      setStreaming(true)

      try {
        const result = await sendMessage(
          conversationIdRef.current,
          content,
          {
            onToken: (chunk) => appendToken(chunk, assistantId),
            onSticker: (name) => appendSticker(name, assistantId),
            onSubagentStart: (agent) => startSubagent(agent, assistantId),
            onSubagentToken: (agent, chunk) => appendSubagentToken(agent, chunk, assistantId),
          },
          imageIds,
          assetIds,
          turnId,
        )
        if (result.conversationId) {
          conversationIdRef.current = result.conversationId
          setConversationId(result.conversationId)
        }
        setMessages((prev) => prev.map((message) => (
          message.clientId === assistantId
            ? { ...message, status: result.status }
            : message
        )))
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to reach the assistant.')
        setMessages((prev) => prev.filter((message) => message.clientId !== assistantId))
      } finally {
        streamingRef.current = false
        setStopping(false)
        setStreaming(false)
      }
    }

    const queued = turnQueueRef.current.then(run, run)
    turnQueueRef.current = queued
    await queued
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void send(input)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send(input)
    }
  }

  const empty = messages.length === 0

  return (
    <div className="flex h-dvh overflow-hidden bg-[#F8FAFF] text-[#23314F]">
      <aside className="hidden w-56 shrink-0 flex-col border-r border-[#EAF0FA] bg-white px-4 py-7 md:flex">
        <Link to="/dashboard" className="flex items-center gap-2 px-2 text-[17px] font-semibold tracking-[-0.02em] text-[#23314F]">
          <span className="flex h-6 w-6 items-center justify-center text-[18px] text-[#23314F]">✦</span>
          Columbina
        </Link>
        <p className="mt-1 px-2 text-[10px] text-[#7183A3]">Your nutrition companion</p>

        <nav className="mt-12 flex flex-col gap-2" aria-label="Main navigation">
          {CHAT_NAV.map((item) => {
            const active = item.label === 'Chat'
            return (
              <Link
                key={item.label}
                to={item.to}
                aria-current={active ? 'page' : undefined}
                className={`rounded-full px-3.5 py-2.5 text-[13px] transition-colors ${
                  active
                    ? 'bg-[#EAF6FF] font-medium text-[#8B3158]'
                    : 'text-[#617698] hover:bg-[#F4F8FD] hover:text-[#23314F]'
                }`}
              >
                {item.label}
              </Link>
            )
          })}
        </nav>

        <div className="mt-10 border-t border-[#EAF0FA] pt-7">
          <p className="px-2 text-[9px] font-semibold tracking-[0.08em] text-[#7183A3] uppercase">Profile</p>
          <div className="mt-4 flex items-center gap-2 px-2">
            <span className="h-8 w-8 overflow-hidden rounded-full border border-[#DCCFF2] bg-[#F2EFFB]">
              <img src={profileIcon} alt="Vincent profile" className="h-full w-full object-cover" />
            </span>
            <p className="text-[13px] font-medium text-[#23314F]">Vincent</p>
          </div>
        </div>

        <div className="mt-auto flex flex-col gap-2 px-2 text-[11px] text-[#7183A3]">
          <button
            type="button"
            onClick={() => setCognitiveInspectorOpen((open) => !open)}
            className="text-left transition-colors hover:text-[#8B3158]"
          >
            {cognitiveInspectorOpen ? 'Close cognitive test' : 'Cognitive test'}
          </button>
          <button type="button" onClick={startNewChat} disabled={streaming || voiceBusy || voiceStarting} className="text-left transition-colors hover:text-[#8B3158] disabled:opacity-40">
            New chat
          </button>
        </div>
      </aside>

      <main className={`relative flex min-w-0 flex-1 flex-col ${sessionPanelOpen ? 'lg:mr-[360px]' : ''}`}>
        <header className="flex items-center justify-between px-5 py-5 sm:px-8 lg:px-12">
          <div>
            <Link to="/dashboard" className="text-[17px] font-semibold tracking-[-0.02em] text-[#23314F] md:hidden">✦ Columbina</Link>
            <h1 className="hidden text-[25px] font-semibold tracking-[-0.03em] text-[#23314F] md:block">Columbina</h1>
            <p className="text-[12px] text-[#7183A3]" aria-live="polite">{voiceLive ? voiceLabel : 'Your quiet companion · chat, talk, or play'}</p>
          </div>
          <span className="flex items-center gap-2 rounded-full bg-[#EAF6FF] px-3 py-2 text-[11px] font-medium text-[#8B3158]">
            <span className={`h-2 w-2 rounded-full ${status === 'open' ? 'bg-[#8B3158]' : status === 'connecting' ? 'animate-pulse-dot bg-[#8FC7E8]' : 'bg-[#A94B60]'}`} />
            {status === 'open' ? (voiceLive ? voiceLabel : 'here with you') : status === 'connecting' ? 'connecting' : 'offline'}
          </span>
        </header>

      {/* messages */}
      <div className="relative flex-1 overflow-hidden">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-cover bg-center opacity-[0.13]"
          style={{ backgroundImage: `url(${chatWallpaper})` }}
        />
        <div ref={scrollRef} className="relative h-full overflow-y-auto px-5 sm:px-8 lg:px-12">
          <div className="mx-auto flex h-full w-full max-w-4xl flex-col py-4 sm:py-8">
          {empty ? (
            <div className="flex flex-1 flex-col items-center justify-center text-center">
              <span className="relative flex h-20 w-20 items-center justify-center overflow-hidden rounded-full border border-[#B9DCF4] bg-white shadow-[0_0_0_8px_rgba(234,246,255,0.55)]">
                <img src={profileIcon} alt="Columbina" className="h-full w-full object-cover" />
              </span>
              <h2 className="mt-8 text-[23px] font-semibold tracking-[-0.03em] text-[#23314F] sm:text-[26px]">A little company, whenever you need it.</h2>
              <p className="mt-2 max-w-md text-[13px] text-[#7183A3]">
                Type a message, start voice, or open a game session.
              </p>
              <div className="mt-8 grid w-full max-w-xl gap-2 sm:grid-cols-3">
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => void send(s)}
                    className="rounded-2xl border border-[#D9E8F6] bg-white px-4 py-3 text-left text-[12px] leading-relaxed text-[#526A8F] shadow-[0_5px_18px_rgba(35,49,79,0.03)] transition-colors hover:border-[#B9DCF4] hover:bg-[#F4F9FE] hover:text-[#23314F]"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((m, i) => (
                <MessageBubble
                  key={i}
                  message={m}
                  loading={
                    (streaming || voiceBusy) &&
                    i === messages.length - 1 &&
                    m.role === 'assistant' &&
                    m.parts.length === 0
                  }
                  messageIsStreaming={
                    (streaming || voiceBusy) && i === messages.length - 1 && m.role === 'assistant'
                  }
                  onDismissNudge={dismissNudges}
                />
              ))}
            </div>
          )}

          {error && (
            <p className="mt-4 rounded-xl border border-[#E7B5C5] bg-[#FBEFF3] px-3 py-2 text-xs text-[#8B3158]">
              {error}
            </p>
          )}
          </div>
        </div>
      </div>

      <div className="fixed inset-y-0 right-0 z-50 pt-14">
        <CognitiveInspector
          open={cognitiveInspectorOpen}
          draft={input}
          recentMessages={messages
            .map((message) => `${message.role}: ${message.parts
              .filter((part): part is Extract<MessagePart, { kind: 'text' }> => part.kind === 'text')
              .map((part) => part.content).join('\n')}`)}
          imageCount={attachments.filter((attachment) => attachment.kind === 'image').length}
          assetCount={attachments.filter((attachment) => attachment.kind === 'asset').length}
          onClose={() => setCognitiveInspectorOpen(false)}
        />
      </div>

      {/* composer */}
      <div className="bg-[#F8FAFF] px-5 pb-5 pt-3 sm:px-8 sm:pb-7 lg:px-12">
        <div className="mx-auto max-w-4xl">
          <MoodStrip />
          <ThoughtStream thoughts={thoughts} />
          <ChatSessionTools eye={eye} voice={voice} awareness={awareness} onPanelChange={setSessionPanelOpen} inputBlocked={streaming} />
        <form onSubmit={handleSubmit}>
          {voiceEnabled && (
            <div className="mb-3 flex flex-col items-center gap-1">
              <button
                type="button"
                onClick={() => {
                  if (!voiceLive) void startVoice()
                  else if (voice.muted) voice.setMuted(false)
                  else if (voice.state === 'listening') voice.endTurn()
                  else if (voiceBusy) voice.interrupt()
                  else voice.pushToTalk()
                }}
                disabled={voiceStarting || streaming || status !== 'open'}
                aria-label={!voiceLive ? 'Start voice using orb' : voice.muted ? 'Unmute microphone using orb' : voice.state === 'listening' ? 'Finish speaking' : voiceBusy ? 'Interrupt voice reply' : 'Talk now'}
                className="h-16 w-16 shrink-0 cursor-pointer rounded-full bg-white/80 p-1 shadow-[0_4px_20px_rgba(110,174,211,0.12)] transition-transform hover:scale-105 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#8FC7E8] active:scale-95 disabled:cursor-not-allowed disabled:opacity-50 sm:h-20 sm:w-20"
              >
                <VoiceOrb state={voice.muted && !voiceBusy ? 'off' : voice.state} getLevel={voice.getLevel} />
              </button>
              {!voiceLive && <span className="text-[10px] text-[#7183A3]">{voiceStarting ? 'Starting voice…' : 'Tap to talk'}</span>}
            </div>
          )}
          {voiceLive && (
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-[#B9DCF4] bg-[#EAF6FF] px-4 py-2.5">
              <span className="text-xs font-medium text-[#527998]" role="status">{voiceLabel}{voice.muted && voice.state === 'speaking' ? ' · Columbina is speaking' : ''}</span>
              <div className="flex items-center gap-2">
                {voice.state === 'idle' && !voice.muted && <button type="button" onClick={voice.pushToTalk} disabled={streaming} className="rounded-full bg-white px-3 py-2 text-xs">Talk now</button>}
                {voice.state === 'listening' && <button type="button" onClick={voice.endTurn} className="rounded-full bg-white px-3 py-2 text-xs">Done talking</button>}
                <button type="button" onClick={() => voice.setMuted(!voice.muted)} disabled={streaming} aria-pressed={voice.muted} aria-label={voice.muted ? 'Unmute microphone' : 'Mute microphone'} className={`rounded-full px-3 py-2 text-xs font-medium ${voice.muted ? 'bg-[#8B3158] text-white' : 'bg-white text-[#527998]'}`}>
                  {voice.muted ? 'Unmute mic' : 'Mute mic'}
                </button>
                <button type="button" onClick={voice.disable} className="rounded-full bg-white px-3 py-2 text-xs text-[#8B3158]">End voice</button>
              </div>
            </div>
          )}
          {voice.error && <p role="alert" className="mb-2 text-xs text-[#8B3158]">{voice.error}</p>}

          {/* pending attachments */}
          {attachments.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-2">
              {attachments.map((a) => (
                <div key={a.id} className="group relative">
                  {a.kind === 'image' ? <img
                    src={a.previewUrl} alt="Attached image"
                    className="h-16 w-16 rounded-xl border border-[#D9E8F6] object-cover"
                  /> : <div className="flex h-16 max-w-40 items-center rounded-xl border border-[#D9E8F6] bg-white px-3 text-[10px] text-[#7183A3]">
                    {a.filename}
                  </div>}
                  <button
                    type="button"
                    onClick={() => removeAttachment(a.id)}
                    className="absolute -top-1.5 -right-1.5 flex h-5 w-5 items-center justify-center rounded-full border border-[#D9E8F6] bg-white text-[#7183A3] transition-colors hover:text-[#8B3158]"
                    aria-label="Remove attachment"
                  >
                    <svg width="10" height="10" viewBox="0 0 24 24" fill="none">
                      <path
                        d="M6 6l12 12M18 6L6 18"
                        stroke="currentColor"
                        strokeWidth="3"
                        strokeLinecap="round"
                      />
                    </svg>
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex items-end gap-2 rounded-[22px] border border-[#D9E8F6] bg-white p-2.5 shadow-[0_7px_24px_rgba(35,49,79,0.04)] transition-colors focus-within:border-[#B9DCF4]">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*,.pdf,.docx,.pptx,.xlsx,.txt,.csv,.blend,.fbx,.obj,.glb,.gltf"
              multiple
              className="hidden"
              onChange={(e) => {
                void attachFiles(e.target.files)
                // Reset so picking the same file twice still fires onChange.
                e.target.value = ''
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={streaming || uploading || attachments.length >= MAX_ATTACHMENTS}
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-[#8FC7E8] transition-colors hover:bg-[#EAF6FF] hover:text-[#6FAED3] disabled:opacity-40"
              aria-label="Attach an image or file"
              title="Attach an image or file"
            >
              {uploading ? (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line-bright border-t-accent" />
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                  <path
                    d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
            </button>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              rows={1}
              placeholder="Message Columbina…"
              aria-label="Message Columbina"
              className="min-w-0 max-h-40 flex-1 resize-none bg-transparent px-2.5 py-2 text-[13px] text-[#23314F] placeholder:text-[#7183A3] outline-none"
            />
            <button type="button" onClick={() => voiceLive ? voice.setMuted(!voice.muted) : void startVoice()}
              disabled={!voiceEnabled || voiceStarting || streaming || status !== 'open'}
              aria-label={voiceLive ? (voice.muted ? 'Unmute microphone' : 'Mute microphone') : 'Start voice'}
              title={!voiceEnabled ? 'Voice is unavailable on this server' : voiceLive ? 'Toggle microphone' : 'Start voice conversation'}
              className={`flex h-10 shrink-0 items-center justify-center rounded-full px-3 text-[11px] font-medium transition-colors disabled:opacity-40 ${voiceLive ? 'bg-[#EAF6FF] text-[#527998]' : 'bg-[#F2EFFB] text-[#765E94]'}`}>
              {voiceStarting ? 'Starting…' : voiceLive ? (voice.muted ? 'Unmute' : 'Mic on') : 'Voice'}
            </button>
            {(streaming || voiceBusy) && (
              <button
                type="button"
                onClick={requestStop}
                disabled={stopping}
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#8B3158] text-white transition-all hover:bg-[#762847] disabled:opacity-60"
                aria-label={stopping ? 'Stopping' : 'Stop'}
              >
                {stopping ? (
                  <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink/30 border-t-ink" />
                ) : (
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" />
                  </svg>
                )}
              </button>
            )}
            {(!voiceLive && !streaming) || input.trim() || attachments.length > 0 ? (
              <button
                type="submit"
                disabled={
                  (!input.trim() && attachments.length === 0) ||
                  status !== 'open' ||
                  stopping || voiceBusy || voiceStarting
                }
                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[#8B3158] text-white transition-all hover:bg-[#762847] disabled:opacity-40"
                aria-label="Send"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path
                    d="M5 12h14M13 6l6 6-6 6"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            ) : null}
          </div>
          <p className="mt-2 text-center text-[10px] text-[#9AAAC1]">
            Enter to send · Shift+Enter for newline · Attach images, documents, or 3D assets
          </p>
        </form>
        </div>
      </div>
      </main>
    </div>
  )
}

// Memoized: a streaming token changes only the last message, so the rest of
// the history (markdown included) must not re-render with it. During voice the
// same main thread schedules reply audio, and a slow render there is a cut.
const MessageBubble = memo(function MessageBubble({
  message,
  loading,
  messageIsStreaming,
  onDismissNudge,
}: {
  message: ChatMessage
  loading: boolean
  messageIsStreaming: boolean
  onDismissNudge?: () => void
}) {
  const isUser = message.role === 'user'
  const images = message.imageIds ?? []
  const assets = message.assetIds ?? []
  // Normalize persisted replies and older/live websocket replies as well.
  // This keeps a raw [[sticker:name]] marker from leaking into the bubble if
  // the backend was not restarted after the sticker protocol was added.
  const renderableParts = message.parts.flatMap((part) => (
    part.kind === 'text' ? partsFromContent(part.content) : [part]
  ))
  const stickerParts = renderableParts.filter((part): part is Extract<MessagePart, { kind: 'sticker' }> => part.kind === 'sticker')
  const hasContent = renderableParts.some((part) => part.kind !== 'sticker')
  const outcomeLabel = message.status === 'cancelled'
    ? 'Response stopped.'
    : message.status === 'failed'
      ? 'Response failed.'
      : null

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div className={`flex max-w-[85%] gap-3 ${isUser ? 'flex-row-reverse' : ''}`}>
        {!isUser && (
          <span className="mt-0.5 h-9 w-9 shrink-0 overflow-hidden rounded-full border border-[#DCCFF2] bg-[#F2EFFB]">
            <img src={profileIcon} alt="Columbina" className="h-full w-full object-cover" />
          </span>
        )}
        <div className={`flex flex-col gap-2 ${isUser ? 'items-end' : 'items-start'}`}>
          {/* Mark it. An unprompted message the user can't tell apart from a
              reply reads as the app talking to itself. */}
          {message.unprompted && (
            <span className="flex items-center gap-2">
              <span className="text-[10px] font-semibold tracking-[0.08em] text-[#8B3158] uppercase">
                Unprompted
              </span>
              {onDismissNudge && (
                <button
                  onClick={onDismissNudge}
                  className="text-[10px] tracking-[0.08em] text-[#7183A3] uppercase transition-colors hover:text-[#23314F]"
                  title="Stop the assistant from speaking first this session"
                >
                  Not now
                </button>
              )}
            </span>
          )}
          {images.length > 0 && (
            <div className={`flex flex-wrap gap-2 ${isUser ? 'justify-end' : ''}`}>
              {images.map((id) => (
                <a key={id} href={imageUrl(id)} target="_blank" rel="noreferrer">
                  <img
                    src={imageUrl(id)}
                    alt="Food photo"
                    className="max-h-48 rounded-2xl border border-[#D9E8F6] object-cover transition-opacity hover:opacity-90"
                  />
                </a>
              ))}
            </div>
          )}
          {assets.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {assets.map((id) => (
                <DocumentAttachment key={id} assetId={id} />
              ))}
            </div>
          )}
          {(hasContent || loading || outcomeLabel) && (
            <div
              className={`flex w-full flex-col gap-2 rounded-[20px] px-5 py-4 text-[13px] leading-relaxed shadow-[0_5px_18px_rgba(35,49,79,0.03)] ${
                isUser
                  ? 'bg-[#EAF6FF] text-[#23314F]'
                  : 'border border-[#D9E8F6] bg-white text-[#23314F]'
              }`}
            >
              {!isUser && <span className="text-[10px] font-semibold text-[#8B3158]">Columbina</span>}
              {loading ? (
                <span className="inline-flex gap-1">
                  <span className="h-1.5 w-1.5 rounded-full bg-[#8B3158] animate-pulse-dot" />
                  <span
                    className="h-1.5 w-1.5 rounded-full bg-[#8B3158] animate-pulse-dot"
                    style={{ animationDelay: '0.2s' }}
                  />
                  <span
                    className="h-1.5 w-1.5 rounded-full bg-[#8B3158] animate-pulse-dot"
                    style={{ animationDelay: '0.4s' }}
                  />
                </span>
              ) : outcomeLabel ? (
                <span className="text-muted italic">{outcomeLabel}</span>
              ) : (
                renderableParts.map((part, i) =>
                  part.kind === 'text' ? (
                    <ChatContent key={i} content={part.content} />
                  ) : part.kind === 'subagent' ? (
                    <SubagentSegment
                      key={i}
                      part={part}
                      isActive={
                        messageIsStreaming && i === message.parts.length - 1
                      }
                    />
                  ) : null,
                )
              )}
            </div>
          )}
          {stickerParts.length > 0 && (
            <div className={`flex flex-col gap-1 ${isUser ? 'items-end' : 'items-start'}`}>
              {stickerParts.map((part, index) => (
                <img
                  key={`${part.name}-${index}`}
                  src={stickerUrl(part.name)}
                  alt={`Columbina sticker: ${part.name}`}
                  className="h-36 w-36 object-contain"
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
})

/** A specialist's reasoning, collapsed to its agent name by default once it
 * finishes — expanded automatically while it's the segment actively
 * streaming, so you can watch it work without having to click first. */
function SubagentSegment({
  part,
  isActive,
}: {
  part: Extract<MessagePart, { kind: 'subagent' }>
  isActive: boolean
}) {
  const [collapsed, setCollapsed] = useState(!isActive)
  const userToggledRef = useRef(false)
  const wasActiveRef = useRef(isActive)

  useEffect(() => {
    // Auto-collapse the moment this segment stops being the active one
    // (the orchestrator resumed, or another specialist started) — unless
    // the user already reached in and set it themselves.
    if (wasActiveRef.current && !isActive && !userToggledRef.current) {
      setCollapsed(true)
    }
    wasActiveRef.current = isActive
  }, [isActive])

  return (
    <div className="w-full overflow-hidden rounded-lg border border-line bg-surface-2/60">
      <button
        type="button"
        onClick={() => {
          userToggledRef.current = true
          setCollapsed((c) => !c)
        }}
        className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left transition-colors hover:bg-line/30"
      >
        <span className="flex items-center gap-1.5 font-mono text-[10px] tracking-wider text-muted uppercase">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isActive ? 'bg-accent animate-pulse-dot' : 'bg-line-bright'
            }`}
          />
          {part.agent}
        </span>
        <svg
          width="10"
          height="10"
          viewBox="0 0 24 24"
          fill="none"
          className={`shrink-0 text-muted transition-transform ${
            collapsed ? '' : 'rotate-180'
          }`}
        >
          <path
            d="M6 9l6 6 6-6"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </button>
      {!collapsed && (
        <div className="border-t border-line px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap text-muted">
          {part.content ? <ChatContent content={part.content} /> : <span className="text-muted/60 italic">Thinking…</span>}
        </div>
      )}
    </div>
  )
}
