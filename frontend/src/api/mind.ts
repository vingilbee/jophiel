// Her mind's thoughts (GET /api/mind/thoughts). New ones also arrive live as
// `thought` frames on the chat socket (hooks/useChatSocket.ts).
import type { InnerState, Thought } from '../types'
import { api } from './client'

export const mindApi = {
  /** Her thoughts from the last `minutes`, oldest first. */
  listThoughts: (minutes = 120) => api.get<Thought[]>(`/mind/thoughts?minutes=${minutes}`),
  /** Her inner state and its recent causes (debug only; off unless MIND_INNER_STATE_ENABLED). */
  innerState: () => api.get<InnerState>('/mind/state'),
}
