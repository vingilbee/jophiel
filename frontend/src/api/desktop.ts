// Desktop agent API (backend: app/api/routes/desktop.py). Every route answers
// 404 while the server's DESKTOP_AGENT_ENABLED flag is off.
import { api } from './client'
import type {
  DesktopEnvironment,
  DesktopProfile,
  DesktopSession,
  DesktopSessionCreate,
  DesktopSessionEvent,
} from '../types/desktop'

const s = (id: string) => `/desktop/sessions/${encodeURIComponent(id)}`

export const desktopApi = {
  profiles: () => api.get<DesktopProfile[]>('/desktop/profiles'),

  environments: () => api.get<DesktopEnvironment[]>('/desktop/environments'),
  registerEnvironment: (display_name: string, allowed_profile_ids: string[]) =>
    api.post<DesktopEnvironment>('/desktop/environments', { display_name, allowed_profile_ids }),
  revokeEnvironment: (id: string) =>
    api.post<DesktopEnvironment>(`/desktop/environments/${encodeURIComponent(id)}/revoke`),

  liveSessions: () => api.get<DesktopSession[]>('/desktop/sessions?live_only=true&limit=1'),
  session: (id: string) => api.get<DesktopSession>(s(id)),
  start: (body: DesktopSessionCreate) => api.post<DesktopSession>('/desktop/sessions', body),
  pause: (id: string) => api.post<DesktopSession>(`${s(id)}/pause`),
  resume: (id: string, expected_state_revision: number) =>
    api.post<DesktopSession>(`${s(id)}/resume`, { expected_state_revision }),
  takeover: (id: string) => api.post<DesktopSession>(`${s(id)}/takeover`),
  end: (id: string) => api.post<DesktopSession>(`${s(id)}/end`),
  events: (id: string, afterId = 0) =>
    api.get<DesktopSessionEvent[]>(`${s(id)}/events?after_id=${afterId}&limit=50`),
}
