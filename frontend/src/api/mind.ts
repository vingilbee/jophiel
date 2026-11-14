// Her mind (GET /api/mind/thoughts, /api/mind/state). New thoughts also arrive live as
// `thought` frames on the chat socket (hooks/useChatSocket.ts).
import type { MindState, Thought } from '../types'
import { api } from './client'

export const mindApi = {
  /** Her thoughts from the last `minutes`, oldest first. */
  listThoughts: (minutes = 120) => api.get<Thought[]>(`/mind/thoughts?minutes=${minutes}`),
  /** What her mind keeps between evaluations: thinking under way, what she'll come back to, what's open. */
  state: () => api.get<MindState>('/mind/state'),
}
