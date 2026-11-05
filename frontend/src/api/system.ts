import { api } from './client'
import type { CaptureChain, PartFeeds, Signal, SystemError, SystemStatus, WatchLog } from '../types/system'

/** Her parts as one system: who is alive, and what each part did lately (owner only). */
export const systemApi = {
  status: () => api.get<SystemStatus>('/system/status'),
  part: <P extends keyof PartFeeds>(part: P) => api.get<PartFeeds[P]>(`/system/parts/${part}`),
  signals: (limit = 100) => api.get<Signal[]>(`/system/signals?limit=${limit}`),
  /** The live signal stream (server-sent events, same-origin so the session cookie rides along). */
  signalStreamUrl: '/api/system/signals/stream',
  /** What she thought, what memory captured, what her hands are after: one log, newest first. */
  watch: () => api.get<WatchLog>('/system/watch'),
  errors: () => api.get<SystemError[]>('/system/errors'),
  /** One conversation turn followed through every part (memory check, Student 1, Student 2, the mouth). */
  traceTurn: (turnId: string) => api.get<CaptureChain>(`/learning/capture/turns/${turnId}?blobs=true`),
  /** One decision (her hands', her planner's, a thought's) with what it was given and what ran. */
  traceAttempt: (attemptId: string) => api.get<CaptureChain>(`/learning/capture/attempts/${attemptId}`),
}
