// The field's anatomy: where each part sits, its fluorophore, what's inside it, and which parts hand work to
// which. Positions are in field units (FIELD_W × FIELD_H); they never move, the cells only drift a little.
import type { PartId } from '../../types/system'

export const FIELD_W = 1600
export const FIELD_H = 1000

export interface SubPart {
  id: string
  label: string
}

export interface PartAnatomy {
  id: PartId
  label: string
  /** One line on what this part is, for the panel. */
  role: string
  x: number
  y: number
  r: number
  /** Its fluorophore, used everywhere this part appears. */
  color: string
  fluor: string
  /** Where its label sits relative to the cell. */
  labelSide: 'left' | 'right' | 'above' | 'below'
  /** For a label above or below: line it up with the cell's edge instead of its centre (near a screen edge). */
  labelAlign?: 'start' | 'end'
  subs: SubPart[]
}

export const PARTS: PartAnatomy[] = [
  {
    id: 'mind', label: 'Mind', fluor: 'mCherry', color: '#FF5C8A', x: 800, y: 500, r: 64, labelSide: 'below',
    role: 'Decides everything she does: every message, finished thought or proposal wakes it, and in one evaluation she answers, stays silent, recalls, starts thinking, notes what stays open and chooses when to come back.',
    subs: [
      { id: 'evaluations', label: 'Evaluations' },
      { id: 'continuity', label: 'What she keeps' },
      { id: 'hard', label: 'Hard thoughts' },
      { id: 'quick', label: 'Quick thoughts' },
      { id: 'planner', label: 'Planner' },
      { id: 'look-backs', label: 'Look backs' },
    ],
  },
  {
    id: 'mouth', label: 'Mouth', fluor: 'GFP', color: '#4BE37A', x: 1235, y: 455, r: 50, labelSide: 'right',
    role: 'Delivers what her mind chose to say, in text and voice, and what she says on her own.',
    subs: [
      { id: 'text', label: 'Text' },
      { id: 'voice', label: 'Voice' },
      { id: 'speaking-first', label: 'Speaking first' },
    ],
  },
  {
    id: 'observation', label: 'Observation', fluor: 'BFP', color: '#6E8CFF', x: 845, y: 145, r: 40, labelSide: 'right',
    role: 'Takes in what each source reports, once: the conversation, deliveries, the game, her desktop and its security logs.',
    subs: [
      { id: 'conversation', label: 'Conversation' },
      { id: 'game', label: 'Game' },
      { id: 'desktop', label: 'Desktop' },
      { id: 'security', label: 'Security logs' },
      { id: 'deliveries', label: 'Deliveries' },
    ],
  },
  {
    id: 'memory', label: 'Memory', fluor: 'mOrange', color: '#FF9A3C', x: 770, y: 835, r: 56, labelSide: 'below',
    role: 'Keeps what lasts: facts, episodes, what’s open between you, the lessons she learned, corrections, and what she was asked to forget.',
    subs: [
      { id: 'facts', label: 'Facts' },
      { id: 'episodes', label: 'Episodes' },
      { id: 'working', label: 'Working items' },
      { id: 'lessons', label: 'Lessons' },
      { id: 'recall', label: 'Recall' },
    ],
  },
  {
    id: 'hands', label: 'Hands', fluor: 'YFP', color: '#F5D23A', x: 1215, y: 805, r: 46, labelSide: 'right',
    role: 'Her body: on her own desktop, one action at a time (look, click, type); in a game, works toward the goal one plan step at a time, picking one skill and running it.',
    subs: [
      { id: 'desktop', label: 'Desktop' },
      { id: 'goal', label: 'Goal and plan' },
      { id: 'skills', label: 'Skill driver' },
      { id: 'pack', label: 'Game pack' },
      { id: 'moves', label: 'Learned moves' },
    ],
  },
  {
    id: 'learning', label: 'Learning', fluor: 'Venus-V', color: '#B77DFF', x: 380, y: 745, r: 50, labelSide: 'left',
    role: 'Captures what happened, looks back on it and writes lessons into memory; runs small experiments in games.',
    subs: [
      { id: 'assessments', label: 'Assessments' },
      { id: 'lessons', label: 'Lessons' },
      { id: 'trials', label: 'Trials' },
      { id: 'capture', label: 'Capture' },
    ],
  },
  {
    id: 'phone', label: 'Phone', fluor: 'mNeonGreen', color: '#BFF04F', x: 390, y: 235, r: 44, labelSide: 'left',
    role: 'Your phone as part of her: what it reports wakes her mind; her replies to people there go out only past a check that reads that one conversation; her messages reach you there as pushes.',
    subs: [
      { id: 'senses', label: 'What it reports' },
      { id: 'notifications', label: 'Notifications' },
      { id: 'replies', label: 'Her replies' },
      { id: 'checks', label: 'Checks' },
      { id: 'people', label: 'People' },
      { id: 'space', label: 'Requests for space' },
      { id: 'push', label: 'Push' },
    ],
  },
]

export const PART_BY_ID = Object.fromEntries(PARTS.map((p) => [p.id, p])) as Record<PartId, PartAnatomy>

export interface Layout {
  w: number
  h: number
  parts: PartAnatomy[]
  byId: Record<PartId, PartAnatomy>
}

/** Where each label sits when the field is turned for a tall screen (chosen so no two labels meet). */
const PORTRAIT_SIDE: Record<PartId, PartAnatomy['labelSide']> = {
  mind: 'below', mouth: 'below', observation: 'below', memory: 'below', hands: 'below', learning: 'above', phone: 'above',
}
const PORTRAIT_ALIGN: Partial<Record<PartId, 'start' | 'end'>> = { observation: 'start' }

/** The field for a viewport: landscape as drawn, or the same field turned on its side for a tall screen,
 * so every cell keeps its neighbours and the field fills a phone. */
export function layoutFor(portrait: boolean): Layout {
  const parts = portrait
    ? PARTS.map((p) => ({ ...p, x: p.y, y: p.x, labelSide: PORTRAIT_SIDE[p.id], labelAlign: PORTRAIT_ALIGN[p.id] }))
    : PARTS
  return {
    w: portrait ? FIELD_H : FIELD_W,
    h: portrait ? FIELD_W : FIELD_H,
    parts,
    byId: Object.fromEntries(parts.map((p) => [p.id, p])) as Record<PartId, PartAnatomy>,
  }
}

/** Who hands work to whom (the direction a signal travels). Every wire is a real hand-off in the backend. */
export const WIRES: { from: PartId; to: PartId }[] = [
  { from: 'memory', to: 'mind' }, // the memory check every evaluation starts from, what she recalls, the lessons it weighs
  { from: 'mind', to: 'mouth' }, // what she chose to say (or that she chose silence)
  { from: 'mind', to: 'memory' }, // what she notes and closes (her working-memory tools), and a concluded thought
  { from: 'mind', to: 'hands' }, // tips and plans for her hands
  { from: 'learning', to: 'mind' }, // a proposal for her to accept, decline or put off
  { from: 'mouth', to: 'memory' }, // a finished exchange, consolidated
  { from: 'mouth', to: 'observation' }, // replies and deliveries reported
  { from: 'hands', to: 'observation' }, // skill outcomes reported
  { from: 'observation', to: 'memory' }, // reports reaching working memory
  { from: 'observation', to: 'learning' }, // corrections and failures that trigger assessment
  { from: 'hands', to: 'learning' }, // a stretch of play to assess, and her trajectory captured
  { from: 'learning', to: 'memory' }, // a lesson written or revised (memory owns lessons)
  { from: 'memory', to: 'hands' }, // lessons supplied to her hands' decisions
  { from: 'phone', to: 'mind' }, // a phone tick with news: a notification, a state change, an action's late answer
  { from: 'mind', to: 'phone' }, // an action on the phone, a reply she sent there, or one the check held
  { from: 'mouth', to: 'phone' }, // her message pushed to the phone when the user wasn't at any app
  { from: 'phone', to: 'learning' }, // a phone exchange she took part in, for assessment
  { from: 'hands', to: 'mind' }, // her hands' actions on her desktop, watched by her background mind
]

/** Zoom (as magnification over the fitted view) at which sub-parts begin to resolve. */
export const SUBS_FROM = 1.9
export const SUBS_FULL = 2.7
export const MAX_MAG = 6
