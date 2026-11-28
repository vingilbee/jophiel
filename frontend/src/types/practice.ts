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
  /** Pinned by you: her reviews read it but never rewrite or remove it. */
  pinned: boolean
  created_at: string
  updated_at: string
}

/** One thing she knows of a place that holds next time (backend services/place_memory.py): a control fixed to the
 *  screen, a landmark or area of her own village, farm or world, or a thing she saw there (near a landmark or area). */
export type PlaceEntryKind = 'control' | 'landmark' | 'area' | 'thing'

export interface PlaceEntry {
  id: string
  world: string
  kind: PlaceEntryKind
  text: string
  near_id: string | null
  revision: number
  seen_at: string
}

/** A place she uses, with what she knows of it that holds next time (kept from her screenshots, per world) and her
 *  own lines. This sitting's part lives only for the sitting and isn't listed. */
export interface PlaceLayout {
  id: string
  surface: string
  place: string
  her_layout: string
  revision: number
  created_at: string
  updated_at: string
  entries: PlaceEntry[]
}
