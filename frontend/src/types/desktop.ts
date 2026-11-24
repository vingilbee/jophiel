// Desktop agent contracts — mirror backend/app/schemas/desktop.py.

export type DesktopSessionState =
  | 'starting'
  | 'running'
  | 'paused'
  | 'manual'
  | 'disconnected'
  | 'ended'
  | 'failed'

export type DesktopControlOwner = 'none' | 'gameplay' | 'manual'
export type DesktopCommentaryMode = 'quiet' | 'occasional' | 'commentary'
export type DesktopStepState = 'pending' | 'active' | 'done' | 'skipped' | 'abandoned'

export interface DesktopProfile {
  id: string
  revision: number
  display_name: string
  /** Decision points are in this frame's pixels, e.g. [640, 360]. */
  observation_size: [number, number]
}

export interface DesktopEnvironment {
  id: string
  display_name: string
  /** game_pack: her own player in a game (stage G), not a VM: no preview, no keys. */
  hosting_kind: 'local_vm' | 'cloud_vm' | 'game_pack'
  guest_os: string | null
  allowed_profile_ids: string[]
  policy_revision: number
  status: 'offline' | 'online'
  last_heartbeat_at: string | null
  revoked_at: string | null
  created_at: string
  paired_at: string | null
  controller_version: string | null
  capture_gate: DesktopCaptureGate
  capture_scope: DesktopCaptureScope
  /** The VM has acknowledged the current capture setting (not just been asked). */
  capture_confirmed: boolean
  /** Why there is or isn't a picture, as last reported by the VM. */
  target_state: DesktopTargetState | null
  /** The game's input mode during a session: "playing", "menu", "unknown". */
  input_context: string | null
  /** Security monitoring of the VM's event logs: the owner's own switch, apart from capture and input. */
  security_monitoring: boolean
  security_changed_at: string | null
  /** The owner's "Allow Jophiel to start sessions" switch for this machine (off until turned on). */
  auto_start_allowed: boolean
  auto_start_changed_at: string | null
  /** What the controller last reported about its machine (controller 0.12+); null until it does. */
  controller_status: DesktopMachineStatus | null
  controller_status_at: string | null
}

export interface DesktopCapability {
  available: boolean
  reason: string | null
}

/** A controller's report about its machine (desktop_controller.link.ControllerStatus). Display only. */
export interface DesktopMachineStatus {
  machine: string
  platform: 'windows' | 'macos' | 'linux' | 'other'
  os: string
  /** Linux only: "x11" or "wayland". */
  display: string | null
  capture: DesktopCapability
  input: DesktopCapability
  /** Whether it can run commands (controller 0.13.0+; absent from older ones). */
  commands?: DesktopCapability | null
  startup: 'registered' | 'not_registered' | 'unknown'
  startup_method: string | null
}

/** A network the relay could listen on (GET /desktop/relay). */
export interface DesktopRelayNetwork {
  interface: string
  address: string
  network: string
  kind: 'vm_only' | 'lan'
  recommended: boolean
  approvable: boolean
  note: string
  current: boolean
}

/** The relay on this computer: where it listens, the approved network, the firewall (owner only). */
export interface DesktopRelayStatus {
  state: 'open' | 'closed' | 'error' | 'stopped'
  reason: string
  detail: string
  /** approved: follows the network approved here; explicit: a fixed address in backend/.env. */
  mode: 'approved' | 'explicit'
  address: string | null
  port: number
  interface: string | null
  kind: 'vm_only' | 'lan' | null
  advertising: boolean
  fingerprint: string
  updated_at: string | null
  approval: { interface: string; network: string; kind: 'vm_only' | 'lan'; approved_at: string } | null
  networks: DesktopRelayNetwork[]
  firewall: { state: 'not_needed' | 'present' | 'missing' | 'unverified'; checked_at?: string | null; how?: string | null }
  cannot_verify: string[]
}

/** How security monitoring is doing (GET /desktop/environments/{id}/security). */
export interface DesktopSecurityStatus {
  monitoring: boolean
  changed_at: string | null
  /** The VM as a source of records: online / offline / unknown. */
  source: string
  last_report_at: string | null
  skew_seconds: number | null
  channels: {
    channel: string
    /** readable | unavailable | waiting (no report about it yet) */
    status: string
    reason: string | null
    through: string | null
  }[]
}

export type DesktopCaptureGate = 'allowed' | 'blocked' | 'manual_auth'
export type DesktopCaptureScope = 'desktop' | 'game_window'
export type DesktopTargetState =
  | 'ok'
  | 'not_found'
  | 'not_foreground'
  | 'minimized'
  | 'sensitive'
  | 'capture_off'
  | 'error'

export interface DesktopPreviewFrame {
  url: string
  ageMs: number
}

/** Shown once: the code can't be fetched again. */
export interface DesktopPairing {
  code: string
  expires_at: string
  /** The controller's arguments: `pair CODE --fingerprint HEX` (no address: it finds the relay itself). */
  pair_args: string
  /** Where the relay listens now, for `--relay` if discovery can't work; null while it's closed. */
  relay_address: string | null
  relay_fingerprint: string
}

export interface DesktopPlanStepInput {
  do: string
  done_when: string
}

export interface DesktopPlanInput {
  goal: string
  steps: DesktopPlanStepInput[]
  if_stuck?: string | null
  constraints?: string[]
}

export interface DesktopPlanStep extends DesktopPlanStepInput {
  id: string
  state: DesktopStepState
}

export interface DesktopPlan {
  plan_revision: number
  goal: string
  steps: DesktopPlanStep[]
  if_stuck: string | null
  constraints: string[]
  source: 'columbina_planning_turn' | 'columbina_user_turn' | 'columbina_mind' | 'user_api'
}

export interface DesktopSession {
  id: string
  environment_id: string
  conversation_id: string | null
  profile_id: string
  profile_revision: number
  state: DesktopSessionState
  state_revision: number
  reason_code: string | null
  control_owner: DesktopControlOwner
  execution_generation: number
  plan_revision: number
  plan: DesktopPlan | null
  current_step_id: string | null
  driver_status: string | null
  commentary_mode: DesktopCommentaryMode
  /** After a goal: "continue" = she picks the next one herself; "stop" = she pauses and tells you. */
  autonomy: 'continue' | 'stop'
  /** Her own Minecraft player: after a goal she picks her own next challenge (stage H3). */
  explore: boolean
  max_duration_seconds: number
  started_at: string
  ended_at: string | null
  /** The command took input from gameplay and the VM hasn't confirmed releasing it. */
  release_pending: boolean
  /** Who started it; null for sessions from before this was recorded. */
  started_by: 'user' | 'jophiel' | null
}

export interface DesktopSessionEvent {
  id: number
  type: string
  state_revision: number
  payload: Record<string, unknown>
  occurred_at: string
}

export interface DesktopSessionCreate {
  environment_id: string
  profile_id: string
  conversation_id?: string | null
  commentary_mode?: DesktopCommentaryMode
  max_duration_seconds?: number
  plan?: DesktopPlanInput
  idempotency_key?: string
}

/** What happened to a test decision (newest first from the server). */
export interface DesktopDecisionReport {
  decision_id?: string
  state?: string
  intent?: string
  ended_by?: string | null
  ran_ms?: number | null
  rejection_code?: string | null
  rejection_reason?: string | null
  /** Set instead of a decision when the VM couldn't get ready (e.g. target_missing). */
  problem?: string
  at: number
}

/** What the driver last said and measured. Everything but timings is untrusted game observation. */
export interface DesktopDriverState {
  steps?: number
  notes?: string
  facts?: Record<string, { value: string; at: number }>
  reports?: { kind: string; text: string; at: number }[]
  last?: { intent: string; decision_id: string } | null
  model_ms?: number
  frame_age_ms?: number
  waiting?: string
  updated_at?: number
  /** Game pack sessions (kind "skills"): what her hands are doing, and how it went. */
  kind?: 'skills'
  running?: { skill: string; params: Record<string, unknown> } | null
  outcomes?: { skill: string; params: Record<string, unknown>; status: string; reason: string | null; at: number }[]
  notices?: { kind: string; urgency: string; text: string }[]
  decisions?: number
}

export interface DesktopSessionOptions {
  autonomy?: 'continue' | 'stop'
  commentary_mode?: DesktopCommentaryMode
  explore?: boolean
}
