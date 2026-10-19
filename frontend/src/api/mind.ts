// Her mind's thoughts (GET /api/mind/thoughts). New ones also arrive live as
// `thought` frames on the chat socket (hooks/useChatSocket.ts).
import type { Thought } from '../types'
import { api } from './client'

export const mindApi = {
  /** Her thoughts from the last `minutes`, oldest first. */
  listThoughts: (minutes = 120) => api.get<Thought[]>(`/mind/thoughts?minutes=${minutes}`),
}
