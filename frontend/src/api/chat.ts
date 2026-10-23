// Chat history REST client. The live streaming turn goes over the WebSocket
// (see hooks/useChatSocket.ts); these endpoints load/manage stored history.
import type { Conversation, ConversationDetail } from '../types'
import { api } from './client'

export const chatApi = {
  /** All of the current user's conversations, most recently updated first. */
  listConversations: () => api.get<Conversation[]>('/chat/conversations'),

  /** A single conversation with its full ordered message history. */
  getConversation: (id: string) =>
    api.get<ConversationDetail>(`/chat/conversations/${id}`),

  /** Delete a conversation (and its messages). */
  deleteConversation: (id: string) =>
    api.delete<void>(`/chat/conversations/${id}`),
}
