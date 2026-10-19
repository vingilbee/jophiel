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
  relay_address: string
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
  max_duration_seconds: number
  started_at: string
  ended_at: string | null
  /** The command took input from gameplay and the VM hasn't confirmed releasing it. */
  release_pending: boolean
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
