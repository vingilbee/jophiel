// App-level desktop agent state (plan §11): one provider, so the session
// survives route changes and every panel shows the same thing.
//
// Stage A has no event stream yet, so a live session is polled. The server is
// the only source of truth: every command returns the authoritative snapshot,
// and nothing here infers a state the server didn't report.
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, ApiError } from '../api/client'
import { desktopApi } from '../api/desktop'
import { useAuth } from './useAuth'
import type {
  DesktopEnvironment,
  DesktopPairing,
  DesktopProfile,
  DesktopSession,
  DesktopSessionEvent,
} from '../types/desktop'

const POLL_MS = 3000
// Connection health (the VM coming online or dropping) without a live session.
const HEALTH_POLL_MS = 5000
const MAX_EVENTS = 20
const TERMINAL = new Set(['ended', 'failed'])

interface DesktopSessionContextValue {
  enabled: boolean
  profiles: DesktopProfile[]
  environments: DesktopEnvironment[]
  session: DesktopSession | null
  events: DesktopSessionEvent[]
  /** Input was taken from gameplay and the VM hasn't confirmed releasing it. */
  releasePending: boolean
  /** The code just made for pairing; cleared once the VM pairs. */
  pairing: DesktopPairing | null
  busy: boolean
  error: string | null
  registerEnvironment: (name: string, profileIds: string[]) => Promise<void>
  start: (environmentId: string, profileId: string) => Promise<void>
  pair: (environmentId: string) => Promise<void>
  cancelPairing: () => void
  revokeEnvironment: (environmentId: string) => Promise<void>
  pause: () => Promise<void>
  resume: () => Promise<void>
  takeover: () => Promise<void>
  end: () => Promise<void>
  dismiss: () => void
}

const DesktopSessionContext = createContext<DesktopSessionContextValue | null>(null)

function message(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Something went wrong. Try again.'
}

export function DesktopSessionProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const [enabled, setEnabled] = useState(false)
  const [profiles, setProfiles] = useState<DesktopProfile[]>([])
  const [environments, setEnvironments] = useState<DesktopEnvironment[]>([])
  const [session, setSession] = useState<DesktopSession | null>(null)
  const [events, setEvents] = useState<DesktopSessionEvent[]>([])
  const [releasePending, setReleasePending] = useState(false)
  const [pairing, setPairing] = useState<DesktopPairing | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const lastEventId = useRef(0)
  const pairingFor = useRef<{ environmentId: string; pairedAt: string | null } | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .get<{ desktop_enabled?: boolean }>('/capabilities')
      .then((caps) => !cancelled && setEnabled(Boolean(caps.desktop_enabled)))
      .catch(() => !cancelled && setEnabled(false))
    return () => {
      cancelled = true
    }
  }, [])

  const adopt = useCallback((next: DesktopSession | null) => {
    setSession((prev) => {
      if (next?.id !== prev?.id) {
        lastEventId.current = 0
        setEvents([])
        setReleasePending(false)
      }
      return next
    })
    if (next?.release_pending) setReleasePending(true)
    if (next && next.control_owner === 'gameplay') setReleasePending(false)
  }, [])

  // Initial load, once the user is known and the feature is on.
  useEffect(() => {
    if (!user || !enabled) return
    let cancelled = false
    Promise.all([desktopApi.profiles(), desktopApi.environments(), desktopApi.liveSessions()])
      .then(([p, e, live]) => {
        if (cancelled) return
        setProfiles(p)
        setEnvironments(e)
        adopt(live[0] ?? null)
      })
      .catch((err) => !cancelled && setError(message(err)))
    return () => {
      cancelled = true
    }
  }, [user, enabled, adopt])

  const refreshEvents = useCallback(async (id: string) => {
    const fresh = await desktopApi.events(id, lastEventId.current)
    if (fresh.length === 0) return
    lastEventId.current = fresh[fresh.length - 1].id
    setEvents((prev) => [...prev, ...fresh].slice(-MAX_EVENTS))
  }, [])

  // Poll a live session (no event stream until stage B).
  const liveId = session && !TERMINAL.has(session.state) ? session.id : null
  useEffect(() => {
    if (!liveId) return
    let cancelled = false
    const tick = async () => {
      try {
        const [next, envs] = await Promise.all([desktopApi.session(liveId), desktopApi.environments()])
        if (cancelled) return
        adopt(next)
        setEnvironments(envs)
        await refreshEvents(liveId)
      } catch {
        /* transient; the next tick retries */
      }
    }
    void tick()
    const timer = window.setInterval(tick, POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [liveId, adopt, refreshEvents])

  // Environment health while there's no live session (that poll covers it otherwise).
  useEffect(() => {
    if (!user || !enabled || liveId) return
    const timer = window.setInterval(() => {
      desktopApi
        .environments()
        .then((envs) => {
          setEnvironments(envs)
          // The code has been used once its environment's pairing time changes.
          const target = pairingFor.current
          if (target && envs.some((e) => e.id === target.environmentId && e.paired_at !== target.pairedAt)) {
            pairingFor.current = null
            setPairing(null)
          }
        })
        .catch(() => {})
    }, HEALTH_POLL_MS)
    return () => window.clearInterval(timer)
  }, [user, enabled, liveId])

  const run = useCallback(
    async (fn: () => Promise<DesktopSession | null | void>) => {
      setBusy(true)
      setError(null)
      try {
        const next = await fn()
        if (next) {
          adopt(next)
          await refreshEvents(next.id)
        }
      } catch (err) {
        setError(message(err))
        // A refusal like stale_revision means our snapshot is old: re-sync.
        if (session) desktopApi.session(session.id).then(adopt).catch(() => {})
      } finally {
        setBusy(false)
      }
    },
    [adopt, refreshEvents, session],
  )

  const value: DesktopSessionContextValue = {
    enabled,
    profiles,
    environments,
    session,
    events,
    releasePending,
    pairing,
    busy,
    error,
    registerEnvironment: (name, profileIds) =>
      run(async () => {
        const env = await desktopApi.registerEnvironment(name, profileIds)
        setEnvironments((prev) => [...prev, env])
      }),
    start: (environmentId, profileId) =>
      run(() =>
        desktopApi.start({
          environment_id: environmentId,
          profile_id: profileId,
          idempotency_key: crypto.randomUUID(),
        }),
      ),
    pair: (environmentId) =>
      run(async () => {
        const created = await desktopApi.createPairing(environmentId)
        pairingFor.current = {
          environmentId,
          pairedAt: environments.find((e) => e.id === environmentId)?.paired_at ?? null,
        }
        setPairing(created)
      }),
    cancelPairing: () => {
      pairingFor.current = null
      setPairing(null)
    },
    revokeEnvironment: (environmentId) =>
      run(async () => {
        const revoked = await desktopApi.revokeEnvironment(environmentId)
        setPairing(null)
        setEnvironments((prev) => prev.map((e) => (e.id === revoked.id ? revoked : e)))
        if (session && session.environment_id === environmentId) return desktopApi.session(session.id)
      }),
    pause: () => run(async () => (session ? desktopApi.pause(session.id) : null)),
    resume: () => run(async () => (session ? desktopApi.resume(session.id, session.state_revision) : null)),
    takeover: () => run(async () => (session ? desktopApi.takeover(session.id) : null)),
    end: () => run(async () => (session ? desktopApi.end(session.id) : null)),
    dismiss: () => adopt(null),
  }

  return <DesktopSessionContext.Provider value={value}>{children}</DesktopSessionContext.Provider>
}

export function useDesktopSession(): DesktopSessionContextValue {
  const ctx = useContext(DesktopSessionContext)
  if (!ctx) throw new Error('useDesktopSession must be used inside DesktopSessionProvider')
  return ctx
}
