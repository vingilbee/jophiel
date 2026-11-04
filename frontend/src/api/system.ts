import { api } from './client'
import type { PartFeeds, SystemStatus } from '../types/system'

/** Her parts as one system: who is alive, and what each part did lately (owner only). */
export const systemApi = {
  status: () => api.get<SystemStatus>('/system/status'),
  part: <P extends keyof PartFeeds>(part: P) => api.get<PartFeeds[P]>(`/system/parts/${part}`),
}
