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
import { useVoiceSession, type VoiceSession } from './useVoiceSession'
import { api } from '../api/client'

export type VoiceMessage = {
  role: 'user' | 'assistant'
  content: string
  turnId?: string
  status?: 'completed' | 'partial' | 'cancelled' | 'failed'
  unprompted?: boolean
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

  const socket = useChatSocket({
    onTranscript: (text, turnId) =>
      setMessages((prev) => [
        ...prev,
        { role: 'user', content: text, turnId },
        { role: 'assistant', content: '', turnId },
      ]),
    onAudio: (pcm, sampleRate) => voiceRef.current?.handleAudio(pcm, sampleRate),
    onAudioEnd: () => voiceRef.current?.handleAudioEnd(),
    onNudgeStart: ({ conversationId: id }) => {
      setConversationId(id)
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: '', unprompted: true },
      ])
    },
    onNudgeToken: appendToReply,
  })

  const voice = useVoiceSession({
    conversationId,
    onToken: appendToReply,
    onReplyComplete: (id) => {
      if (id) setConversationId(id)
    },
    socket: {
      startVoice: socket.startVoice,
      sendAudio: socket.sendAudio,
      endVoice: (id, onToken) => socket.endVoice(id, { onToken }),
      cancelVoice: socket.cancelVoice,
      stop: socket.stop,
    },
  })
  voiceRef.current = voice

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
          setMessages(detail.messages.map(({ role, content }) => ({ role, content })))
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
