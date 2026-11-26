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
