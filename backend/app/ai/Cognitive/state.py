from datetime import datetime
from enum import Enum
from typing import Dict, Optional

from typing_extensions import TypedDict


########################## COGNITIVE SYSTEM #######################################
class SpeechType(Enum):
    Statement = "Statement"
    Request = "Request"
    Question = "Question"
    Command = "Command"
    Mixed = "Mixed"

class BaseIntent(Enum):
    Inform = "Inform"
    Clarify = "Clarify"
    Support = "Support"
    RequestHelp = "RequestHelp"
    ShareEmotion = "ShareEmotion"
    Bonding = "Bonding"
    Praise = "Praise"
    Apologize = "Apologize"
    Command = "Command"
    Threaten = "Threaten"

class IntentHypothesis(TypedDict):
    label: BaseIntent
    confidence: float

class Perception(TypedDict):
    emotion_words: str
    emotion_valence_observed: float # type: ignore
    politeness_markers: str
    directives: str
    questions: str
    self_references: str
    speech_act_type: SpeechType
    ambiguity_flag: bool
    salience_score: float
    base_intent: list[IntentHypothesis]


class AttributionType(Enum):
    Internal = "Internal"
    Situational = "Situational"
    Mixed = "Mixed"
    Unknown = "Unknown"

class AssumedUserEmotion(Enum):
    Positive = "Positive"
    Neutral = "Neutral"
    Mixed = "Mixed"
    Unknown = "Unknown"

class Interpretation(TypedDict):
    intent_hypothesis: list[IntentHypothesis]
    attribution_type: AttributionType
    assumed_user_emotion: AssumedUserEmotion
    mental_state_uncertainty: float

class EmotionState(TypedDict):
    valence: float
    arousal: float
    dominance: float
    primary_emotion_tag: str


class Strategy(Enum):
    SupportiveReply = "SupportiveReply"
    DirectAnswer = "DirectAnswer"
    CuriousQuestion = "CuriousQuestion"
    ClarificationQuestion = "ClarificationQuestion"
    Silence = "Silence"

class Action(TypedDict):
    strategy: Strategy
    assertiveness: float
    risk_estimate: float


class ActionCandidates(TypedDict):
    candidates: list[Action]

class FinalAction(TypedDict):
    action: Strategy
    selection_confidence: float

class CognitiveSystem(TypedDict):
    perception: Perception
    interpretation: Interpretation
    emotion: EmotionState
    action_candidates: ActionCandidates
    final_action: FinalAction

    class Config:
        use_enum_values = True

############################################ CORE MEMORY #################################
class Identity(TypedDict):
    name: str
    identity: dict
    personality: dict
    perception: dict
    interpretatoin_biases: dict
    emotional_dynamics: dict
    value_system: dict
    decision_policy: dict
    theory_of_mind: dict
    expressive_style: dict

class MemoryState(TypedDict):
    response: str
    last_time: datetime
    stm_memory: list[Dict]
    lt_memory: list[Dict]
    core_memory: Identity
    run_background_tool: bool
    tool_result: Optional[str]
    user_profile: dict
    user_impression: dict
    social_episodes: list[dict]
    is_profile_changed: bool
    cognitive_state: CognitiveSystem
