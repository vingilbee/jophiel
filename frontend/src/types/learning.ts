// Shared learning and executable game skill contracts.

/** A move she taught herself (stage H2): her hands have it as a skill while enabled. */
/** A challenge she set herself while exploring (stage H3). */
export interface Challenge {
  id: string
  game: string
  challenge: string
  why: string
  aim: 'discover' | 'for_us'
  check: { kind: string; what?: string; count?: number } | null
  status: 'active' | 'done' | 'given_up' | 'replaced' | 'expired'
  result: string | null
  /** moves she learned on the way */
  learned: string[]
  created_at: string
  started_at: string | null
  ended_at: string | null
}

export interface LearnedSkill {
  id: string
  game: string
  /** the skill name her hands pick, learned_<words> */
  name: string
  task: string
  code: string
  enabled: boolean
  uses: number
  successes: number
  misses_in_row: number
  /** why it switched itself off (misses in a row), or null */
  off_reason: string | null
  /** the program as she learned it, while `code` is a tidied copy */
  original_code: string | null
  /** what the tidy changed, or why it was undone */
  tidy_note: string | null
  /** the tidied copy has worked at least once */
  tidy_proven: boolean
  last_used_at: string | null
  created_at: string
  updated_at: string
}

/** Learning's retrospective assessments (docs/learning-system/02-implementation.md). */
export type AssessmentStatus = 'supported' | 'plausible' | 'unresolved' | 'contradicted'
export type AssessmentOutcome = 'concluded' | 'unresolved' | 'no_feedback' | 'left'

export interface AssessmentConclusion {
  status: AssessmentStatus
  cites: string[]
  independent: number
  why?: string
  note?: string
  basis?: string | null
}

export interface AssessmentTarget extends AssessmentConclusion {
  ref: string
  also: string[]
  target_roots: string[]
}

export interface AssessmentCause extends AssessmentConclusion {
  target: string
  component: string
}

export interface AssessmentResult {
  feedback: 'yes' | 'no' | 'unclear'
  targets: AssessmentTarget[]
  causes: AssessmentCause[]
  user_stated: (AssessmentConclusion & { kind: 'intended_meaning' | 'preference'; quote: string })[]
  dimensions: (AssessmentConclusion & { dimension: string; target: string; value: string })[]
  choice_vs_outcome: { target: string; choice: string; outcome: string; note: string; cites: string[] }[]
  missing: { what: string; needed_for: string }[]
  /** Revisions before post-audit 3 only. */
  clarification?: { propose: boolean; question: string }
  /** Post-audit 3 (docs/learning-system/POST-AUDIT-03-implementation.md): absent on older revisions. */
  review?: { decision: 'examine' | 'wait' | 'leave'; why: string }
  findings?: (AssessmentConclusion & {
    statement: string
    form: string
    applies_when: string
    alternatives: string[]
    /** A lesson (id, revision) or, since post-audit 4, a memory (target: "fact:<id>", "item:<id>"). */
    relates_to: { lesson: string; id?: string; revision?: number; target?: string; relation: string }[]
    /** Post-audit 4: untested / needs / would_support / would_challenge, in the reviewer's words. */
    details?: Record<string, string>
  })[]
  follow_up?: { propose: boolean; kind: 'question' | 'observation' | 'practice'; text: string; resolves: string; item?: string | null }
  inquiry?: AssessmentInquiry
  requests?: { source: string; query: string }[]
  explanation: string
  target_labels: Record<string, string>
  trigger: string
}

export interface AssessmentRevision {
  revision: number
  evaluator: string
  reason: string
  outcome: AssessmentOutcome
  target_status: string
  cause_status: string
  stop_reason: 'concluded' | 'missing_evidence' | 'stuck' | 'waiting' | 'left'
  independent: number
  explanation: string
  result: AssessmentResult
  dropped: { what: string; why: string }[]
  rounds: { plan: Record<string, unknown>; chars: number; skipped?: string }[]
  superseded_by: number | null
  created_at: string
}

export interface AssessmentInquiry {
  question?: string
  provisional?: string
  alternatives?: string[]
  unknown?: string
  resume_when?: string
  stop?: string
  revision?: number
}

export interface AssessmentSummary {
  id: string
  trigger_kind: 'correction' | 'complaint' | 'execution' | 'sample' | 'review'
  trigger: { why?: string }
  inquiry?: AssessmentInquiry
  state: 'pending' | 'running' | 'done' | 'failed'
  outcome: AssessmentOutcome | null
  target_status: string | null
  cause_status: string | null
  stop_reason: string | null
  explanation: string | null
  revision: number
  last_error: string | null
  awaiting: string[]
  anchor_at: string
  updated_at: string
}

export interface AssessmentDetail extends AssessmentSummary {
  evidence: { ref: string; reason: string | null; detail: { text?: string }; added_at: string }[]
  revisions: AssessmentRevision[]
  uses: { consumer: string; ref: string; revision: number; state: 'current' | 'reconsider'; reason: string | null }[]
}

export interface AssessmentListing {
  counts: Record<string, number>
  items: AssessmentSummary[]
}

/** Lessons from supported assessments, memory's (docs/learning-system/03-implementation.md). */
export type AdaptationLessonStatus = 'accepted' | 'hypothesis' | 'episode_only' | 'withdrawn'
export type LessonUseCase = 'not_received' | 'no_decision' | 'no_account' | 'applied' | 'unclear' | 'set_aside'

export interface LessonUse {
  lesson_id: string
  revision: number
  consumer: string
  attempt_id: string
  received: boolean
  reason: string | null
  ref: string | null
  rank: number | null
  reported: 'applied' | 'not_relevant' | 'outweighed' | 'conflict' | 'unclear' | null
  reported_why: string | null
  decision_status: string | null
  case: LessonUseCase
  explanation: string
  at: string
}

export interface AdaptationLesson {
  id: string
  kind: 'preference' | 'strategy' | 'understanding'
  origin: 'stated' | 'inferred'
  scope: 'standing' | 'situation' | 'episode'
  status: AdaptationLessonStatus
  statement: string
  applies_when: string
  exceptions: string[]
  form?: string | null
  alternatives?: string[]
  /** Post-audit 4: untested / tried / needs / would_support / would_challenge, in the builder's words. */
  details?: Record<string, string>
  user_words: string[]
  revision: number
  status_reason: string | null
  conflicts_with: string[]
  support: number
  counter: number
  /** Occasions that bore on it without support or counter (post-audit 4). */
  noted?: number
  withdrawn_at: string | null
  created_at: string
  updated_at: string
}

/** How a lesson relates to another memory (post-audit 4); never support. */
export interface LessonLink {
  relation: 'depends_on' | 'builds_on' | 'same_as' | 'distinct' | 'supports' | 'challenges' | 'related'
  target_kind: 'lesson' | 'fact' | 'item'
  target_id: string
  what: string
  then_text: string
  then_status: string | null
  now_status: string | null
  now_text: string | null
  changed: boolean
  note: string | null
}

export interface AdaptationLessonDetail extends AdaptationLesson {
  links?: LessonLink[]
  /** Premises that changed since it was last assessed: it is being reassessed. */
  unsettled?: string[]
  revisions: {
    revision: number
    op: string
    status: AdaptationLessonStatus
    scope: string
    statement: string
    applies_when: string
    exceptions: string[]
    user_words: string[]
    reason: string
    /** A revision made without new evidence: a critique of its reasoning, or a changed premise. */
    prompted_by?: 'critique' | 'premise' | null
    assessment_id: string | null
    assessment_revision: number | null
    at: string
  }[]
  evidence: {
    role: 'support' | 'counter' | 'noted'
    occasion: string
    occasion_at: string
    assessment_id: string | null
    assessment_revision: number | null
    refs: string[]
    quote: string | null
    why: string
    current: boolean
    added_revision: number
  }[]
  uses: LessonUse[]
}

export interface AdaptationListing {
  counts: Partial<Record<AdaptationLessonStatus, number>>
  items: AdaptationLesson[]
  jobs: { id: string; assessment_id: string; state: string; outcome: string | null; last_error: string | null; updated_at: string }[]
}

/** A bounded game trial (docs/learning-system/04-implementation.md): learning's experiment record. */
export type TrialStatus =
  | 'proposed'
  | 'presented'
  | 'deferred'
  | 'rejected'
  | 'expired'
  | 'selected'
  | 'preparing'
  | 'executing'
  | 'paused'
  | 'observing'
  | 'assessing'
  | 'assessed'

export interface TrialSummary {
  id: string
  status: TrialStatus
  scenario: string
  problem: string
  revision: number
  hypothesis: string | null
  selected: string | null
  ending: string | null
  stop_reason: string | null
  costs: Record<string, number | string>
  limits: { attempts?: number; seconds?: number; blocks?: number }
  deferral: { reason: string; since: string; expires_at: string } | null
  linked_trial_id: string | null
  created_at: string | null
  closed_at: string | null
}

export interface TrialPrediction {
  measure: string
  expect: string
  value?: number
  high?: number
}

export interface TrialAlternative {
  key: string
  action: string
  approach: string
  predictions: TrialPrediction[]
  expected: string
  uncertainty: 'low' | 'medium' | 'high'
  seconds: number | null
  blocks: number | null
  prerequisites: { item: string; count: number }[]
}

export interface TrialDetail extends TrialSummary {
  selection: { alternative: string; reason: string; recommended: boolean; at: string } | null
  deferrals: { reason: string; since: string }[]
  revisions: {
    revision: number
    reason: string
    at: string
    proposal: {
      hypothesis: string
      recommendation: string
      recommendation_why: string
      alternatives: TrialAlternative[]
      limits: { attempts: number; seconds: number; blocks: number }
    }
  }[]
  events: { kind: string; at: string; payload: Record<string, unknown> }[]
  assessments: {
    n: number
    current: boolean
    hypothesis: 'supported' | 'contradicted' | 'unresolved'
    test_started: boolean
    missing: string[]
    prediction_checks: { ref: string; prediction: string; status: 'met' | 'not_met' | 'unknown'; why: string }[]
    result: {
      why: string
      failure_of: string
      applicability: string
      exceptions: string[]
      explanations: { text: string; status: string }[]
      lesson: { situation: string; advice: string } | null
    }
    at: string
  }[]
}

export interface TrialListing {
  counts: Partial<Record<TrialStatus, number>>
  trials: TrialSummary[]
  investigations: { id: string; kind: string; state: string; problem: string | null; error: string | null; at: string | null }[]
}
