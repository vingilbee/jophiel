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
    id: 'cognition', label: 'Cognition', fluor: 'CFP', color: '#3FD3F0', x: 800, y: 500, r: 64, labelSide: 'below',
    role: 'Reads every message and decides how to meet it: a memory check, Student 1’s reading, Student 2’s decision, and background work.',
    subs: [
      { id: 'memory-check', label: 'Memory check' },
      { id: 'student-1', label: 'Student 1' },
      { id: 'student-2', label: 'Student 2' },
      { id: 'coordinator', label: 'Coordinator' },
    ],
  },
  {
    id: 'mouth', label: 'Mouth', fluor: 'GFP', color: '#4BE37A', x: 1235, y: 455, r: 50, labelSide: 'right',
    role: 'Puts her decision into words, in text and voice, and speaks first when she chooses to.',
    subs: [
      { id: 'text', label: 'Text' },
      { id: 'voice', label: 'Voice' },
      { id: 'speaking-first', label: 'Speaking first' },
    ],
  },
  {
    id: 'mind', label: 'Mind', fluor: 'mCherry', color: '#FF5C8A', x: 440, y: 285, r: 54, labelSide: 'left',
    role: 'Thinks on her own: quick and hard thoughts, her game planner, and her mood.',
    subs: [
      { id: 'quick', label: 'Quick thoughts' },
      { id: 'hard', label: 'Hard thoughts' },
      { id: 'planner', label: 'Planner' },
      { id: 'mood', label: 'Mood' },
    ],
  },
  {
    id: 'observation', label: 'Observation', fluor: 'BFP', color: '#6E8CFF', x: 845, y: 145, r: 40, labelSide: 'right',
    role: 'Takes in what each source reports, once: the conversation, deliveries, the game.',
    subs: [
      { id: 'conversation', label: 'Conversation' },
      { id: 'game', label: 'Game' },
      { id: 'deliveries', label: 'Deliveries' },
    ],
  },
  {
    id: 'memory', label: 'Memory', fluor: 'mOrange', color: '#FF9A3C', x: 770, y: 835, r: 56, labelSide: 'below',
    role: 'Keeps what lasts: facts, episodes, what’s open between you, corrections, and what she was asked to forget.',
    subs: [
      { id: 'facts', label: 'Facts' },
      { id: 'episodes', label: 'Episodes' },
      { id: 'working', label: 'Working items' },
      { id: 'recall', label: 'Recall' },
    ],
  },
  {
    id: 'hands', label: 'Hands', fluor: 'YFP', color: '#F5D23A', x: 1215, y: 805, r: 46, labelSide: 'right',
    role: 'Her body in a game: picks one skill at a time and runs it.',
    subs: [
      { id: 'skills', label: 'Skill driver' },
      { id: 'pack', label: 'Game pack' },
      { id: 'moves', label: 'Learned moves' },
    ],
  },
  {
    id: 'learning', label: 'Learning', fluor: 'Venus-V', color: '#B77DFF', x: 380, y: 745, r: 50, labelSide: 'left',
    role: 'Looks back on what happened and carries lessons forward; runs small experiments in games.',
    subs: [
      { id: 'assessments', label: 'Assessments' },
      { id: 'lessons', label: 'Lessons' },
      { id: 'trials', label: 'Trials' },
      { id: 'game-lessons', label: 'Game lessons' },
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
  cognition: 'below', mouth: 'below', mind: 'above', observation: 'below', memory: 'below', hands: 'below',
  learning: 'above',
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
  { from: 'memory', to: 'cognition' }, // the memory check before every reply
  { from: 'cognition', to: 'mouth' }, // Student 2's decision, worded by the mouth
  { from: 'cognition', to: 'mind' }, // thinking asked for, run by the coordinator
  { from: 'mind', to: 'mouth' }, // something she wants to say on her own
  { from: 'mind', to: 'memory' }, // a concluded thought handed to memory
  { from: 'mind', to: 'hands' }, // tips and plans for her hands
  { from: 'mouth', to: 'memory' }, // a finished exchange, consolidated
  { from: 'mouth', to: 'observation' }, // replies and deliveries reported
  { from: 'hands', to: 'observation' }, // skill outcomes reported
  { from: 'observation', to: 'memory' }, // reports reaching working memory
  { from: 'observation', to: 'learning' }, // corrections and failures that trigger assessment
  { from: 'hands', to: 'learning' }, // game experience reflected on
  { from: 'learning', to: 'cognition' }, // lessons Student 2 weighs
  { from: 'learning', to: 'hands' }, // game lessons and learned moves
]

/** Zoom (as magnification over the fitted view) at which sub-parts begin to resolve. */
export const SUBS_FROM = 1.9
export const SUBS_FULL = 2.7
export const MAX_MAG = 6
