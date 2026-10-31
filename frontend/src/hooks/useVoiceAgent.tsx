import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { chatApi } from '../api/chat'
import { useAuth } from './useAuth'
import { useAwareness } from './useAwareness'
import { useChatSocket } from './useChatSocket'
import { heardCut } from '../audio/heardText'
import { useVoiceSession, type VoiceSession } from './useVoiceSession'
import { api } from '../api/client'

export type VoiceMessage = {
  role: 'user' | 'assistant'
  content: string
  stickers?: string[]
  turnId?: string
  status?: 'completed' | 'partial' | 'cancelled' | 'failed' | 'silent'
  unprompted?: boolean
  /**
   * Set when the user cut this spoken reply off: characters of `content` they
   * heard. The rest was generated but never said aloud.
   */
  heardChars?: number
}

const STICKER_MARKER = /\[\[sticker:([a-z0-9-]+)\]\]/g
const STICKER_NAMES = new Set([
  'confused', 'question', 'love', 'thinking', 'good-job', 'sleepy', 'yum',
  'tea', 'hmm', 'shy', 'sorry', 'idea', 'encouragement', 'silent', 'scribble',
  'grateful', 'surprised', 'peeking', 'embarrassed', 'praying', 'determined',
  'hungry', 'see-you',
])

function cleanStickerMarkers(content: string) {
  const stickers: string[] = []
  const visible = content.replace(STICKER_MARKER, (_marker, name: string) => {
    if (STICKER_NAMES.has(name)) stickers.push(name)
    return ''
  })
  return { visible, stickers }
}

type VoiceAgentContextValue = {
  voice: VoiceSession
  status: ReturnType<typeof useChatSocket>['status']
  awareness: ReturnType<typeof useChatSocket>['awareness']
  sendSense: ReturnType<typeof useChatSocket>['sendSense']
  dismissNudges: ReturnType<typeof useChatSocket>['dismissNudges']
  messages: VoiceMessage[]
  conversationId: string | null
  clearConversation: () => void
  voiceEnabled: boolean
}

const VoiceAgentContext = createContext<VoiceAgentContextValue | null>(null)

function VoiceAgentRuntime({ children }: { children: ReactNode }) {
  const [messages, setMessages] = useState<VoiceMessage[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [voiceEnabled, setVoiceEnabled] = useState(false)
  const voiceRef = useRef<VoiceSession | null>(null)

  function appendToReply(chunk: string) {
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (!last || last.role !== 'assistant') return prev
      next[next.length - 1] = { ...last, content: last.content + chunk }
      return next
    })
  }

  function appendSticker(name: string) {
    setMessages((prev) => {
      const next = [...prev]
      const last = next[next.length - 1]
      if (!last || last.role !== 'assistant') return prev
      next[next.length - 1] = { ...last, stickers: [...(last.stickers ?? []), name] }
      return next
    })
  }

  const socket = useChatSocket({
    onTranscript: (text, turnId) =>
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: text, turnId },
        // A transcript can land after the user already talked over that turn
        // (they are listening again); its reply will never come, so no
        // typing placeholder for it.
        ...(voiceRef.current?.state === 'thinking'
          ? [{ role: 'assistant' as const, content: '', turnId }]
          : []),
      ]),
    onAudioStart: (_rate, unprompted) => {
      if (unprompted) voiceRef.current?.beginUnpromptedSpeech()
    },
    onAudio: (pcm, sampleRate) => voiceRef.current?.handleAudio(pcm, sampleRate),
    onAudioEnd: () => voiceRef.current?.handleAudioEnd(),
    onAudioSegment: (segment, textStart, textEnd) =>
      voiceRef.current?.handleAudioSegment(segment, textStart, textEnd),
    onBargeResolved: (action) => voiceRef.current?.handleBargeResolved(action),
    onNudgeStart: ({ conversationId: id }) => {
      setConversationId(id)
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: '', unprompted: true },
      ])
    },
    onNudgeToken: appendToReply,
    onNudgeSticker: appendSticker,
  })

  const voice = useVoiceSession({
    conversationId,
    connected: socket.status === 'open',
    onToken: appendToReply,
    onReplyComplete: (id) => {
      if (id) setConversationId(id)
    },
    onHeard: (turnId, heardChars) =>
      setMessages((prev) => {
        let index = -1
        for (let i = prev.length - 1; i >= 0; i--) {
          if (prev[i].role === 'assistant' && prev[i].turnId === turnId) {
            index = i
            break
          }
        }
        if (index < 0) return prev
        const next = [...prev]
        next[index] = { ...next[index], heardChars }
        return next
      }),
    socket: {
      startVoice: socket.startVoice,
      sendAudio: socket.sendAudio,
      endVoice: (id, onToken, onSticker) => socket.endVoice(id, { onToken, onSticker }),
      cancelVoice: socket.cancelVoice,
      stop: socket.stop,
      interruptVoiceStart: socket.interruptVoiceStart,
      holdVoice: socket.holdVoice,
      commitHold: socket.commitHold,
      endHold: socket.endHold,
      resumeHold: socket.resumeHold,
      stopHold: socket.stopHold,
      adoptHold: (id, onToken, onSticker) => socket.adoptHold(id, { onToken, onSticker }),
      reportHeard: socket.reportHeard,
      reportVoiceLatency: socket.reportVoiceLatency,
    },
  })
  voiceRef.current = voice

  // A reply placeholder is added the moment the transcript lands. If the turn
  // is interrupted before its first token (the user paused, then kept
  // talking), nothing ever fills it and its typing dots would pulse forever.
  // Once we're listening again (or voice is off) no reply is pending, so any
  // still-empty placeholder belongs to a turn that is over. Not on 'idle':
  // a nudge opens its placeholder while idle.
  useEffect(() => {
    if (voice.state !== 'listening' && voice.state !== 'off') return
    // A held reply is paused, not over: its bubble will fill when it resumes.
    if (voice.holding) return
    setMessages((prev) => {
      const next = prev.filter(
        (m) => m.role !== 'assistant' || m.content !== '' || (m.stickers?.length ?? 0) > 0,
      )
      return next.length === prev.length ? prev : next
    })
  }, [voice.state, voice.holding])

  useAwareness({
    sendSense: socket.sendSense,
    activity: voice.state === 'listening' ? 'speaking' : 'idle',
    hearingEnabled: voice.state !== 'off',
    // Push-to-talk is intentional until a real wake-word model exists.
    wakeArmed: false,
    voiceState: voice.state,
  })

  useEffect(() => {
    let cancelled = false
    chatApi
      .listConversations()
      .then((conversations) => {
        if (cancelled || conversations.length === 0) return
        return chatApi.getConversation(conversations[0].id).then((detail) => {
          if (cancelled) return
          setConversationId(detail.id)
          setMessages(detail.messages.map(({ role, content, heard_chars }) => {
            // Cut the raw text first, then strip sticker markers from each
            // side: heard_chars counts characters of the raw stored reply.
            const cut = heardCut(content, heard_chars)
            const heard = cleanStickerMarkers(content.slice(0, cut))
            const rest = cleanStickerMarkers(content.slice(cut))
            return {
              role,
              content: heard.visible + rest.visible,
              stickers: [...heard.stickers, ...rest.stickers],
              ...(cut < content.length ? { heardChars: heard.visible.length } : {}),
            }
          }))
        })
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    api.get<{ voice_enabled: boolean }>('/capabilities')
      .then(({ voice_enabled }) => {
        if (!cancelled) setVoiceEnabled(voice_enabled)
      })
      .catch(() => {
        if (!cancelled) setVoiceEnabled(false)
      })
    return () => { cancelled = true }
  }, [])

  return (
    <VoiceAgentContext.Provider
      value={{
        voice,
        status: socket.status,
        awareness: socket.awareness,
        sendSense: socket.sendSense,
        dismissNudges: socket.dismissNudges,
        messages,
        conversationId,
        clearConversation: () => {
          setConversationId(null)
          setMessages([])
        },
        voiceEnabled,
      }}
    >
      {children}
    </VoiceAgentContext.Provider>
  )
}

/** Keep microphone/audio ownership above the router while a user is signed in. */
export function VoiceAgentProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  return user ? <VoiceAgentRuntime>{children}</VoiceAgentRuntime> : children
}

export function useVoiceAgent(): VoiceAgentContextValue {
  const context = useContext(VoiceAgentContext)
  if (!context) throw new Error('useVoiceAgent must be used while signed in')
  return context
}
