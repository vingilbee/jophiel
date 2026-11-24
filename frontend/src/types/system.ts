// Her parts as one system (backend app/api/routes/system.py, owner only).

export type PartId = 'mouth' | 'mind' | 'hands' | 'memory' | 'observation' | 'learning' | 'phone'

/** busy: a worker is inside a job; partial: some of its workers are down; off: not running in this mode. */
export type PartState = 'alive' | 'busy' | 'partial' | 'down' | 'failing' | 'off'

export interface WorkerStatus {
  name: string
  state: 'alive' | 'busy' | 'down' | 'failing'
  seen_ago: number | null
  busy: boolean
  worked_ago?: number | null
  error: string | null
}

export interface PartStatus {
  id: PartId
  label: string
  state: PartState
  workers: WorkerStatus[]
}

export interface SystemStatus {
  /** false: the heartbeats couldn't be read (no Redis), so every state is a guess. */
  known: boolean
  at: number
  desktop: boolean
  parts: PartStatus[]
}

export interface MouthFeed {
  replies: { id: string; at: string | null; conversation: string | null; channel: string; status: string; text: string | null; interrupted: boolean }[]
}

/** One evaluation of her mind (backend services/mind_turn.py): why she woke, what memory gave her, the tools she
 * used, and what she did about speaking. */
export interface MindEvaluation {
  id: string
  at: string | null
  status: string
  error: string | null
  model: string | null
  turn_id: string | null
  seconds: number | null
  /** user_message / work_result / thought / proposal / conflict / finding / due */
  reason: string
  message: string | null
  wake: string | null
  memory: string | null
  memory_records: number
  lessons: number
  tools: { tool: string | null; result: string | null }[]
  silent: string | null
  said: string | null
  thinking_started: number
  scheduled: number
  proposal: string | null
}

/** Her mind's continuity, read from its owners' tables (GET /api/mind/state, and the mind's feed). */
export interface MindContinuity {
  thinking: { id: string; kind: string; status: string; about: string; conversation_id: string | null; created_at: string | null }[]
  scheduled: { id: string; about: string; due_at: string | null; item: string | null; conversation_id: string | null }[]
  open: { id: string; kind: string; owner: string; text: string; status: string; due_at: string | null }[]
  recent: { at: string | null; reason: string; decision: string; status: string; conversation_id: string | null }[]
}

export interface Thought {
  id: string
  at: string | null
  level: string
  kind: string | null
  trigger: string | null
  conclusion: string | null
  status: string
  source: string | null
  topic: string | null
  text: string | null
  thinking: string | null
  question: string | null
  for_hands: string | null
  follows: string | null
  /** Her own look back on the effort then: "getting anywhere: no; cause: perception". */
  judged: string | null
  said: boolean
  dropped: string | null
}

/** A lesson memory gave a decision, and what the decision said it did with it (its own account). */
export interface LessonUse {
  id: number
  at: string | null
  consumer: string
  lesson: string | null
  lesson_status: string
  revision: number
  received: boolean
  reason: string | null
  reported: string | null
  why: string | null
}

export interface PlannerCall {
  id: string
  at: string | null
  status: string
  error: string | null
  seconds: number | null
  reasons: string[] | null
  decision: string | null
  why: string | null
  progress: string | null
  sees: string | null
  cannot_see: string | null
  goal: string | null
  steps: (string | null)[]
  say: string | null
  ask: string | null
  applied: {
    result: string
    deviation: string | null
    outcome: string | null
    rules: { rule: string | null; from: string[] }[]
  } | null
}

export interface MindFeed {
  evaluations: MindEvaluation[]
  continuity: MindContinuity
  thoughts: Thought[]
  plans: PlannerCall[]
  lessons: LessonUse[]
}

/** Her latest game session: the goal, the plan's steps, and her effort as code counted it. */
export interface GameGoal {
  session_id: string
  state: string
  driver: string | null
  started_at: string | null
  ended_at: string | null
  goal: { text: string | null; status: string; reason: string | null; owner: string } | null
  plan: {
    goal: string | null
    target: string | null
    revision: number
    steps: { id: string | null; do: string | null; done_when: string | null; state: string | null }[]
  } | null
  effort: {
    active_s: number
    paused_s: number
    approach_changes: number
    status_note: string | null
    rules: { ref: string; rule: string | null; words: string | null }[]
    places: { name: string; where: string | null }[]
    counted: string[]
    spots: string | null
  } | null
}

export interface HandsDecision {
  id: string
  at: string | null
  status: string
  error: string | null
  seconds: number | null
  step: string | null
  /** Her hands' own status for the step (a claim), and their note. */
  claim: string | null
  note: string | null
  action: string | null
  weighed: { lesson: string | null; use: string | null; why: string | null }[]
  /** What the game pack reported for the skill, and her state as code read it before and after. */
  run: {
    result: string
    pack_status: string | null
    reason: string | null
    seconds: number | null
    deviation: string | null
    before: string | null
    after: string | null
    inventory: string | null
    observed_note: string | null
  } | null
}

/** One action of her hands on her own desktop (backend services/desktop_control.remember), as code recorded it. */
export interface DesktopAction {
  id: string
  at: string | null
  tool: string | null
  args: string | null
  result: string
  status: string | null
  app: string | null
  window: string | null
  screen: string | null
  deviation: string | null
  turn_id: string | null
  /** A command's (run_command): its exit code (null: it didn't finish) and the end of its output. */
  exit_code?: number | null
  output?: string | null
}

export interface HandsFeed {
  goal: GameGoal | null
  desktop: DesktopAction[]
  decisions: HandsDecision[]
  checks: { id: string; at: string | null; do: string | null; done_when: string | null; counted: string | null }[]
  lessons: LessonUse[]
  outcomes: { id: number; at: string | null; kind: string; game: string; text: string | null; seconds: number | null; failed: boolean }[]
}

export interface MemoryFeed {
  pending_jobs: number
  open: { id: string; kind: string; owner: string; status: string; text: string | null; at: string | null }[]
  events: { id: number; kind: string; epistemic: string; text: string | null; at: string | null }[]
  facts: { id: string; kind: string; status: string; text: string | null; confidence: number; revision: number; at: string | null }[]
  lessons: {
    id: string
    kind: string
    scope: string
    status: string
    origin: string
    statement: string | null
    applies_when: string | null
    reason: string | null
    revision: number
    used: number
    at: string | null
  }[]
}

export interface LearningFeed {
  gaps: { id: number; at: string | null; kind: string; label: string; reason: string; tries: number; detail: string | null }[]
}

export interface ObservationFeed {
  sources: { source: string; instance: string; availability: string; last_received_at: string | null; error: string | null }[]
  reports: { id: number; source: string; kind: string; salience: string; text: string | null; at: string | null }[]
}

/** The user's phone as a part of her (backend services/system_parts._phone): what it reports, her replies to other
 * people there and the check each one passed or was held by, the people she meets there, and pushes. */
export interface PhoneFeed {
  /** What the phone reports about itself, one line each, with how old each reading is. */
  senses: string[]
  /** The notifications still on the phone (null: Redis couldn't be read, so unknown). */
  notifications: { key: string; app: string | null; conversation: string | null; group: boolean; can_reply: boolean; messages: number; at: string | null }[] | null
  replies: { id: string; at: string | null; to: string | null; conversation: string | null; asked_by: string; status: string; echoed: boolean; text: string | null }[]
  /** Outcomes of the phone's checks: reply / draft (a message going out), operation (a phone read or action on
   * the user's turn), grant (a detail the user let her pass on). ok null: no verdict recorded. */
  checks: { at: string | null; check: string | null; kind: string | null; ok: boolean | null; what: string | null; why: string | null; reveals: string[] }[]
  people: { id: string; name: string | null; contact: boolean | null; status: string; at: string | null; familiarity: string | null; warmth: string | null; initiative: string | null; note: string | null }[]
  exchanges: { id: string; at: string | null; person: string | null; conversation: string | null; addressed: string; participated: boolean; observation: string | null; their_words: string | null; lesson: string; lesson_why: string | null }[]
  space: { id: string; at: string | null; person: string | null; conversation: string | null; scope: string; topic: string | null; words: string | null }[]
  actions: { id: string; at: string | null; text: string | null }[]
  pushes: { id: string; at: string | null; kind: string; status: string; attempts: number; error: string | null }[]
}

export interface PartFeeds {
  mouth: MouthFeed
  mind: MindFeed
  hands: HandsFeed
  memory: MemoryFeed
  observation: ObservationFeed
  learning: LearningFeed
  phone: PhoneFeed
}

/** The watch log (GET /api/system/watch): what woke her mind and what she did, what she thought, what memory
 * captured, what her hands are after. */
export interface WatchItem {
  id: string
  part: PartId
  kind: string
  at: string
  title: string
  text: string | null
  detail: string | null
  /** The section on that part's page this item belongs to. */
  anchor: string
  tone: 'muted' | 'error' | null
}

export interface WatchLog {
  goal: GameGoal | null
  items: WatchItem[]
}

/** Something that went wrong in one of her parts (GET /api/system/errors). */
export interface SystemError {
  id: string
  part: PartId
  at: string
  what: string
  text: string | null
  turn_id?: string | null
  attempt_id?: string | null
}

/** One captured decision (learning capture), as the trace shows it. */
export interface CapturedAttempt {
  id: string
  learner: string
  trigger: string
  model: string | null
  status: string
  error: string | null
  input: Record<string, unknown>
  output: Record<string, unknown> | null
  upstream: Record<string, unknown>
  absent: unknown[]
  started_at: string | null
  produced_at: string | null
}

export interface CapturedExecution {
  id: string
  kind: string
  executor: string
  attempt_id: string | null
  intended: Record<string, unknown>
  executed: Record<string, unknown>
  result: string
  deviation: string | null
  started_at: string | null
  ended_at: string | null
  captured_at: string | null
}

/** A turn or a decision followed through every part it touched (GET /api/learning/capture/...). */
export interface CaptureChain {
  complete: boolean
  attempts: CapturedAttempt[]
  executions: CapturedExecution[]
  missing: { what: string; id?: string; note: string }[]
  gaps: { record_kind: string; label: string; reason: string; detail: string | null }[]
  turn_id?: string
}

/** One hand-off from a part to another (GET /api/system/signals, and its live stream). Ids only, no text. */
export interface Signal {
  id: string
  from: PartId
  to: PartId
  kind: string
  ref: string | null
  at: number
}
