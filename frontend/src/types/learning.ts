// Game learning contracts — mirror backend/app/schemas/game_learning.py.

export type LessonStatus = 'trial' | 'confirmed' | 'retired'
export type ProposalStatus = 'open' | 'accepted' | 'implemented' | 'rejected'

export interface LessonEvidence {
  session_id?: string
  verdict: string
  experience_ids?: number[]
  at: string
}

export interface Lesson {
  id: string
  game: string
  normalized_key: string
  situation: string
  advice: string
  /** skill: how an action behaves (her hands); plan: what to do and when (Columbina, when she plans). */
  level: 'skill' | 'plan'
  status: LessonStatus
  support_count: number
  contradict_count: number
  evidence: LessonEvidence[]
  created_at: string
  updated_at: string
}

export interface ProposalEvidence {
  session_id: string
  experience_ids: number[]
  examples: string[]
  at: string
  after_implemented?: boolean
}

export interface Proposal {
  id: string
  game: string
  normalized_key: string
  title: string
  area: string
  problem: string
  suggestion: string
  occurrences: number
  evidence: ProposalEvidence[]
  status: ProposalStatus
  user_note: string | null
  created_at: string
  updated_at: string
}

export interface ReflectionResult {
  outcomes?: number
  why?: string
  lessons?: { new: string[]; supported: string[]; contradicted: string[] }
  proposals?: { new: string[]; repeated: string[]; reopened: string[] }
  dropped?: unknown[]
  episode_id?: string | null
}

export interface Reflection {
  id: string
  session_id: string
  game: string
  status: 'pending' | 'processing' | 'done' | 'skipped' | 'failed'
  attempts: number
  last_error: string | null
  result: ReflectionResult | null
  created_at: string
  processed_at: string | null
}

/** A move she taught herself (stage H2): her hands have it as a skill while enabled. */
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
