/** Her practice notes: what she learned using her tools, per app (backend services/practice.py). */
export type PracticeNoteStatus = 'known' | 'to_try'

export interface PracticeNote {
  id: string
  surface: string
  place: string
  tool: string | null
  status: PracticeNoteStatus
  source: 'practice' | 'user'
  text: string
  evidence: string | null
  revision: number
  created_at: string
  updated_at: string
}
