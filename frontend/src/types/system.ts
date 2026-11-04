// Her parts as one system (backend app/api/routes/system.py, owner only).

export type PartId = 'mouth' | 'cognition' | 'mind' | 'hands' | 'memory' | 'observation' | 'learning'

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
  student_two?: string
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

export interface CognitionFeed {
  decisions: {
    id: string
    learner: 'student_1' | 'student_2'
    trigger: string
    status: string
    error: string | null
    model: string | null
    turn_id: string | null
    at: string | null
    seconds: number | null
    decided: string | null
  }[]
  work: { id: string; kind: string; status: string; error: string | null; at: string | null }[]
}

export interface MindFeed {
  mood: { words: Record<string, string> | null; updated_at: number | null } | null
  thoughts: {
    id: number
    at: string | null
    level: string
    kind: string | null
    trigger: string | null
    conclusion: string | null
    status: string
    source: string | null
    text: string | null
    thinking: string | null
    question: string | null
    said: boolean | null
    dropped: string | null
  }[]
}

export interface HandsFeed {
  picks: { id: string; at: string | null; status: string; picked: string | null }[]
  outcomes: { id: number; at: string | null; kind: string; game: string; text: string | null; seconds: number | null }[]
}

export interface MemoryFeed {
  pending_jobs: number
  open: { id: string; kind: string; owner: string; status: string; text: string | null; at: string | null }[]
  events: { id: number; kind: string; epistemic: string; text: string | null; at: string | null }[]
}

export interface ObservationFeed {
  sources: { source: string; instance: string; availability: string; last_received_at: string | null; error: string | null }[]
  reports: { id: number; source: string; kind: string; salience: string; text: string | null; at: string | null }[]
}

export interface PartFeeds {
  mouth: MouthFeed
  cognition: CognitionFeed
  mind: MindFeed
  hands: HandsFeed
  memory: MemoryFeed
  observation: ObservationFeed
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
