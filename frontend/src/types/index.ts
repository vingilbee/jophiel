export interface User {
  id: string
  name: string
  email: string
  created_at: string
}

export type ChatRole = 'user' | 'assistant'

export type MessagePart =
  | { kind: 'text'; content: string }
  | { kind: 'subagent'; agent: string; content: string }
  | { kind: 'sticker'; name: string }

export type ChatMessage = {
  /** Ephemeral UI identity used to route concurrent/queued stream updates. */
  clientId?: string
  turnId?: string
  status?: 'completed' | 'partial' | 'cancelled' | 'failed'
  role: 'user' | 'assistant'
  parts: MessagePart[]
  unprompted?: boolean
  imageIds?: string[]
  assetIds?: string[]
}

export interface StoredAsset {
  asset_id: string
  filename: string
  media_type: string
  size_bytes: number
  created_at: string
  kind: string
  derived_from?: string | null
}

// A photo in the per-user image store. `kind` is "upload" for the user's own
// photo, or "depth" for a depth map the food-vision agent generated from one.
export interface StoredImage {
  image_id: string
  media_type: string
  width: number
  height: number
  size_bytes: number
  created_at: string
  kind: string
  filename?: string | null
  derived_from?: string | null
}

// A conversation as returned by the REST endpoints (history is server-side).
export interface Conversation {
  id: string
  title: string | null
  origin_channel: 'web' | 'whatsapp'
  created_at: string
  updated_at: string
}

export interface StoredChatMessage extends ChatMessage {
  id: string
  created_at: string
  content: string
  turn_id?: string | null
  status?: 'completed' | 'partial' | 'cancelled' | 'failed'
  source_channel: 'web' | 'whatsapp' | 'system'
  /** Server-side attachment ids (snake_case, straight off the API). */
  image_ids?: string[] | null
  asset_ids?: string[] | null
  /** Interrupted voice replies: characters of `content` the user heard. */
  heard_chars?: number | null
}

export interface ConversationDetail extends Conversation {
  messages: StoredChatMessage[]
}

export type CognitiveSignalValue = boolean | number | string | null

export interface CognitiveSignal {
  value: CognitiveSignalValue
  confidence: number
  source: string
}

export interface CognitiveInspection {
  perception: {
    schema_version: number
    is_question: boolean
    is_request: boolean
    is_directed_at_agent: boolean
    novelty: number
    ambiguity: number
    signals: Record<string, CognitiveSignal>
  }
  interpretation: {
    schema_version: number
    intent: 'question' | 'request' | 'inform' | 'clarify' | 'feedback' | 'greeting' | 'unknown'
    confidence: number
    alternatives: Array<{ intent: string; confidence: number }>
    needs_clarification: boolean
    source: string
  }
  metrics: {
    processing_ms: number
    character_count: number
    word_count: number
    recent_message_count: number
    online_model_calls: number
  }
}

export interface CognitiveLLMInspection {
  mode: 'llm'
  state: {
    perception: { observations: string[]; references: string[]; explicit_constraints: string[] }
    interpretation: { primary_intent: string; secondary_intents: string[]; resolved_goal: string; missing_information: string[] }
    action: { kind: 'respond' | 'clarify' | 'delegate'; objective: string; capability: string | null; clarification_question: string | null }
  }
  image_count: number
  asset_count: number
  metrics: CognitiveInspection['metrics']
}

// A connectable third-party provider plus this user's connection to it. The
// backend drives this list from its provider registry, so a new integration
// appears in the UI without a frontend change.
export interface Integration {
  id: string
  name: string
  description: string
  category: string
  capabilities: string[]
  /** Server has credentials for this provider. If false, connecting can't work. */
  configured: boolean
  connected: boolean
  /** The connected account, e.g. an email address. */
  account_label: string | null
  connected_at: string | null
  scopes: string[]
}

// --- Awareness -------------------------------------------------------------
// The agent's sense of the current moment. The client reports its own senses
// up (`SenseReport`), the server folds in the clock and the diary and sends the
// assembled view back down (`AwarenessSnapshot`) — so the UI renders exactly
// what the model is told, never a second guess at it.

export type SenseActivity = 'idle' | 'reading' | 'typing' | 'speaking'

export interface SenseReport {
  /** IANA zone. Without it every time-relative thing the agent says is wrong. */
  timezone: string | null
  /** Proprioception: where the user is in the app. Needs no permission. */
  route: string | null
  activity: SenseActivity
  hearing_enabled: boolean
  wake_armed: boolean
  voice_state: 'off' | 'idle' | 'listening' | 'thinking' | 'speaking' | null
  /** Screen sharing is on (the eye toggle). */
  vision_enabled: boolean
  /** Newest screen frame in the image store. An id, never pixels. */
  screen_image_id: string | null
}

export interface AwarenessSnapshot {
  now: string
  zone: string
  part_of_day: string
  session_seconds: number
  since_last_message_seconds: number | null
  calories_today: number
  entries_today: number
  days_since_last_log: number | null
  senses: SenseReport
  /** One-line description written about the current screen frame, if any. */
  screen_caption: string | null
  /** The exact text handed to the model, so the user can see what it perceives. */
  narration: string
}

export type PredictionMode = 'combined' | 'regression'

export interface Prediction {
  calories: number
  fat: number
  carbs: number
  protein: number
  dish?: string | null
  portion_scale?: number | null
  source: string
}

/** What moved her mood (backend services/inner_state_live.py). */
export interface MoodCause {
  at: number
  conversation_id: string
  message: string
  feeling: string
  status: string
  size: string
  incident: string
  quote: string
}

/** Her thought about one of their messages, written after her reply by the inner-state reading. */
export interface ConversationThought {
  at: number
  conversation_id: string
  message: string
  moment: string
  intention: string
}

/** Her inner state for the mind panel: each value in two readings (an unclear size taken as
 * small / as clear); `words` names both when they differ. Nothing reads it into her replies. */
export interface InnerState {
  enabled: boolean
  config: string
  updated_at: number | null
  values: Record<string, { small: number; clear: number }>
  baseline: Record<string, number>
  words: Record<string, string>
  causes: MoodCause[]
  /** Newest first. */
  thoughts: ConversationThought[]
}

/** One thought of her mind while playing (backend services/mind.py). Not a message. */
export interface Thought {
  id: string
  session_id: string | null
  level: 'react' | 'light' | 'quiet' | 'hard'
  text: string
  said: string | null
  status: 'done' | 'stale' | 'unfinished' | 'dropped'
  topic: string | null
  /** The kind of thinking: why, next, you, judgement, feeling, memory (null for hard thoughts). */
  kind?: string | null
  /** Hard thoughts (backend services/mind_hard.py): what started it (failing, surprise, big, handoff, quiet). */
  trigger?: string | null
  /** decision, guess, question, intention or feeling. */
  conclusion_kind?: string | null
  /** Her private reasoning, shown folded under the conclusion. */
  thinking?: string | null
  for_hands?: string | null
  question?: string | null
  dropped_reason?: string | null
  created_at: string
}
