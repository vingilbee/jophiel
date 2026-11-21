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
  DesktopCaptureGate,
  DesktopCaptureScope,
  DesktopDecisionReport,
  DesktopDriverState,
  DesktopPlanInput,
  DesktopEnvironment,
  DesktopPairing,
  DesktopPreviewFrame,
  DesktopProfile,
  DesktopSession,
  DesktopSessionEvent,
  DesktopSessionOptions,
} from '../types/desktop'

const POLL_MS = 3000
// Connection health (the VM coming online or dropping) without a live session.
const HEALTH_POLL_MS = 5000
// Faster while a capture change waits for the VM's confirmation.
const CONFIRM_POLL_MS = 1000
const PREVIEW_POLL_MS = 1000
const MAX_EVENTS = 20
const TERMINAL = new Set(['ended', 'failed'])

interface DesktopSessionContextValue {
  enabled: boolean
  /** Whether the server has said if desktop sessions are on: ``enabled`` only means something once 'loaded'. */
  capabilities: 'loading' | 'loaded' | 'failed'
  /** Ask the server again after a failed capabilities check. */
  retryCapabilities: () => void
  profiles: DesktopProfile[]
  environments: DesktopEnvironment[]
  /** The environment the card is about: the live session's, else the machine picked, else the first one. */
  environment: DesktopEnvironment | null
  /** Pick which paired machine the card shows (by its stable id); remembered in this browser. */
  selectEnvironment: (environmentId: string) => void
  /** Latest game picture while capture is allowed; null otherwise. */
  preview: DesktopPreviewFrame | null
  /** Recent test decisions and their outcomes, newest first. */
  decisions: DesktopDecisionReport[]
  /** The driver's notes, facts and timings (untrusted game observation). */
  driver: DesktopDriverState | null
  session: DesktopSession | null
  events: DesktopSessionEvent[]
  /** Input was taken from gameplay and the VM hasn't confirmed releasing it. */
  releasePending: boolean
  /** The code just made for pairing; cleared once the VM pairs. */
  pairing: DesktopPairing | null
  busy: boolean
  error: string | null
  /** Resolves true once the machine is added; false if the server refused (the reason is in `error`). */
  registerEnvironment: (name: string, profileIds: string[]) => Promise<boolean>
  start: (environmentId: string, profileId: string) => Promise<void>
  pair: (environmentId: string) => Promise<void>
  cancelPairing: () => void
  revokeEnvironment: (environmentId: string) => Promise<void>
  /** "Allow Jophiel to start sessions" on one machine. */
  setAutoStart: (environmentId: string, allowed: boolean) => Promise<void>
  setCapture: (gate: DesktopCaptureGate) => Promise<void>
  setCaptureScope: (scope: DesktopCaptureScope) => Promise<void>
  setSecurityMonitoring: (enabled: boolean) => Promise<void>
  sendDecision: (decision: Record<string, unknown>) => Promise<void>
  setPlan: (plan: DesktopPlanInput) => Promise<void>
  setOptions: (options: DesktopSessionOptions) => Promise<void>
  pause: () => Promise<void>
  resume: () => Promise<void>
  takeover: () => Promise<void>
  end: () => Promise<void>
  dismiss: () => void
}

const DesktopSessionContext = createContext<DesktopSessionContextValue | null>(null)

const SELECTED_KEY = 'desktop.selectedEnvironment'

function readSelected(): string | null {
  try {
    return window.localStorage.getItem(SELECTED_KEY)
  } catch {
    return null
  }
}

function writeSelected(id: string): void {
  try {
    window.localStorage.setItem(SELECTED_KEY, id)
  } catch {
    // private window or blocked storage: the choice just isn't remembered
  }
}

function message(err: unknown): string {
  return err instanceof ApiError ? err.message : 'Something went wrong. Try again.'
}

export function DesktopSessionProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const [enabled, setEnabled] = useState(false)
  const [capabilities, setCapabilities] = useState<'loading' | 'loaded' | 'failed'>('loading')
  const [capabilitiesAttempt, setCapabilitiesAttempt] = useState(0)
  const [profiles, setProfiles] = useState<DesktopProfile[]>([])
  const [environments, setEnvironments] = useState<DesktopEnvironment[]>([])
  const [session, setSession] = useState<DesktopSession | null>(null)
  const [events, setEvents] = useState<DesktopSessionEvent[]>([])
  const [releasePending, setReleasePending] = useState(false)
  const [pairing, setPairing] = useState<DesktopPairing | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(() => readSelected())
  const [preview, setPreview] = useState<DesktopPreviewFrame | null>(null)
  const [decisions, setDecisions] = useState<DesktopDecisionReport[]>([])
  const [driver, setDriver] = useState<DesktopDriverState | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const lastEventId = useRef(0)
  const pairingFor = useRef<{ environmentId: string; pairedAt: string | null } | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .get<{ desktop_enabled?: boolean }>('/capabilities')
      .then((caps) => {
        if (cancelled) return
        setEnabled(Boolean(caps.desktop_enabled))
        setCapabilities('loaded')
      })
      .catch(() => {
        if (cancelled) return
        setEnabled(false)
        setCapabilities('failed')
      })
    return () => {
      cancelled = true
    }
  }, [capabilitiesAttempt])

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
  const usable = environments.filter((e) => !e.revoked_at)
  const machines = usable.filter((e) => e.hosting_kind !== 'game_pack')
  const environment =
    usable.find((e) => e.id === session?.environment_id) ??
    machines.find((e) => e.id === selectedId) ??
    machines[0] ??
    usable[0] ??
    null
  const awaitingConfirm = Boolean(environment && environment.status === 'online' && !environment.capture_confirmed)

  useEffect(() => {
    if (!user || !enabled || (liveId && !awaitingConfirm)) return
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
    }, awaitingConfirm ? CONFIRM_POLL_MS : HEALTH_POLL_MS)
    return () => window.clearInterval(timer)
  }, [user, enabled, liveId, awaitingConfirm])

  // Decision outcomes and the driver's state, while a session is live.
  useEffect(() => {
    if (!liveId) {
      setDecisions([])
      setDriver(null)
      return
    }
    let cancelled = false
    const tick = () => {
      desktopApi
        .decisions(liveId)
        .then((list) => !cancelled && setDecisions(list))
        .catch(() => {})
      desktopApi
        .driver(liveId)
        .then((state) => !cancelled && setDriver(Object.keys(state).length ? state : null))
        .catch(() => {})
    }
    void tick()
    const timer = window.setInterval(tick, 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [liveId])

  // The picture: only fetched while capture is allowed and the VM is online,
  // and not while the browser tab is hidden.
  const previewEnvId =
    environment && environment.status === 'online' && environment.capture_gate === 'allowed' ? environment.id : null
  useEffect(() => {
    if (!previewEnvId) {
      setPreview(null)
      return
    }
    let cancelled = false
    let current: string | null = null
    const tick = async () => {
      if (document.hidden) return
      const frame = await desktopApi.preview(previewEnvId).catch(() => null)
      if (cancelled) return
      const url = frame ? URL.createObjectURL(frame.blob) : null
      if (current) URL.revokeObjectURL(current)
      current = url
      setPreview(frame && url ? { url, ageMs: frame.ageMs } : null)
    }
    void tick()
    const timer = window.setInterval(tick, PREVIEW_POLL_MS)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      if (current) URL.revokeObjectURL(current)
      setPreview(null)
    }
  }, [previewEnvId])

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
    capabilities,
    retryCapabilities: () => {
      setCapabilities('loading')
      setCapabilitiesAttempt((n) => n + 1)
    },
    profiles,
    environments,
    environment,
    selectEnvironment: (environmentId) => {
      setSelectedId(environmentId)
      writeSelected(environmentId)
      setPairing(null) // a code shown for one machine must not sit under another's name
      pairingFor.current = null
      setPreview(null)
    },
    preview,
    decisions,
    driver,
    session,
    events,
    releasePending,
    pairing,
    busy,
    error,
    registerEnvironment: async (name, profileIds) => {
      let added = false
      await run(async () => {
        const env = await desktopApi.registerEnvironment(name, profileIds)
        setEnvironments((prev) => [...prev, env])
        setSelectedId(env.id)
        writeSelected(env.id)
        added = true
      })
      return added
    },
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
    setAutoStart: (environmentId, allowed) =>
      run(async () => {
        const updated = await desktopApi.setAutoStart(environmentId, allowed)
        setEnvironments((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
      }),
    setCapture: (gate) =>
      run(async () => {
        if (!environment) return
        if (gate !== 'allowed') setPreview(null) // drop the picture at once, before the server answers
        const updated = await desktopApi.setCapture(environment.id, { gate })
        setEnvironments((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
      }),
    setCaptureScope: (scope) =>
      run(async () => {
        if (!environment) return
        setPreview(null) // the old picture shows the wrong area
        const updated = await desktopApi.setCapture(environment.id, { scope })
        setEnvironments((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
      }),
    setSecurityMonitoring: (enabled) =>
      run(async () => {
        if (!environment) return
        const updated = await desktopApi.setSecurity(environment.id, enabled)
        setEnvironments((prev) => prev.map((e) => (e.id === updated.id ? updated : e)))
      }),
    setOptions: (options) =>
      run(async () => (session ? desktopApi.setOptions(session.id, options) : null)),
    setPlan: (plan) =>
      run(async () => (session ? desktopApi.replacePlan(session.id, plan, session.plan_revision) : null)),
    sendDecision: (decision) =>
      run(async () => {
        if (!session) return
        await desktopApi.sendDecision(session.id, decision)
        setDecisions(await desktopApi.decisions(session.id))
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
