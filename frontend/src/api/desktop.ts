// Desktop agent API (backend: app/api/routes/desktop.py). Every route answers
// 404 while the server's DESKTOP_AGENT_ENABLED flag is off.
import { api } from './client'
import type {
  DesktopCaptureGate,
  DesktopCaptureScope,
  DesktopDecisionReport,
  DesktopDriverState,
  DesktopPlanInput,
  DesktopEnvironment,
  DesktopPairing,
  DesktopProfile,
  DesktopSession,
  DesktopSessionCreate,
  DesktopSessionEvent,
  DesktopSessionOptions,
} from '../types/desktop'

const s = (id: string) => `/desktop/sessions/${encodeURIComponent(id)}`

export const desktopApi = {
  profiles: () => api.get<DesktopProfile[]>('/desktop/profiles'),

  environments: () => api.get<DesktopEnvironment[]>('/desktop/environments'),
  registerEnvironment: (display_name: string, allowed_profile_ids: string[]) =>
    api.post<DesktopEnvironment>('/desktop/environments', { display_name, allowed_profile_ids }),
  createPairing: (id: string) =>
    api.post<DesktopPairing>(`/desktop/environments/${encodeURIComponent(id)}/pairings`),
  setCapture: (id: string, change: { gate?: DesktopCaptureGate; scope?: DesktopCaptureScope }) =>
    api.put<DesktopEnvironment>(`/desktop/environments/${encodeURIComponent(id)}/capture`, change),
  /** The latest frame as a Blob, or null when there's no current picture. */
  preview: async (id: string): Promise<{ blob: Blob; ageMs: number } | null> => {
    const res = await api.raw(`/desktop/environments/${encodeURIComponent(id)}/preview`, { cache: 'no-store' })
    if (!res.ok) return null
    return { blob: await res.blob(), ageMs: Number(res.headers.get('X-Frame-Age-Ms') ?? 0) }
  },
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
  sendDecision: (id: string, decision: Record<string, unknown>) =>
    api.post<{ decision_id: string }>(`${s(id)}/decisions`, { decision }),
  setOptions: (id: string, options: DesktopSessionOptions) =>
    api.patch<DesktopSession>(`${s(id)}/options`, options),
  driver: (id: string) => api.get<DesktopDriverState>(`${s(id)}/driver`),
  replacePlan: (id: string, plan: DesktopPlanInput, expected_plan_revision: number) =>
    api.put<DesktopSession>(`${s(id)}/plan`, { ...plan, expected_plan_revision }),
  decisions: (id: string) => api.get<DesktopDecisionReport[]>(`${s(id)}/decisions`),
  events: (id: string, afterId = 0) =>
    api.get<DesktopSessionEvent[]>(`${s(id)}/events?after_id=${afterId}&limit=50`),
}
