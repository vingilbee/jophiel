"""Shared vocabulary and records for the simple cognition chain.

Spec: ``docs/COLUMBINA_SIMPLE_COGNITION_AND_TEACHER_PLAN.md``. Every label the teacher writes is a
word from a fixed list; numbers come only from the fixed maps below, so a person can read any
example and the student's targets stay exactly reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 'simple-cognition-v1'

# --- 2. Understand ----------------------------------------------------------------------------
ACTS = ('asking', 'sharing', 'requesting', 'teasing', 'complaining', 'correcting_her', 'small_talk', 'ending')
TONES = ('friendly', 'neutral', 'playful', 'cold', 'upset')
# Whether a request can be met by talking. Not one of the plan's original cues: the `cant_do`
# flag needs it, and code can't tell from words alone what a request needs.
REQUEST_KINDS = ('no_request', 'talk_only', 'outside_ability')
NOTE_UPDATES = ('none', 'answers', 'accepts', 'completes', 'drops')

# --- 3. Feel ----------------------------------------------------------------------------------
APPRAISALS_SIGNED = ('good_for_her', 'warm_toward_her')     # -1..1
APPRAISALS = ('surprising', 'important')                     # 0..1
FEELINGS = ('happy', 'amused', 'curious', 'touched', 'unsure', 'hurt', 'annoyed', 'tired')

# --- 6. Decide --------------------------------------------------------------------------------
GROUPS = (
    'greet', 'say_bye', 'react_briefly', 'answer_explain', 'ask_about_it', 'check_meaning',
    'show_understanding', 'comfort', 'celebrate_thank', 'play', 'share_view', 'agree', 'push_back',
    'apologise_fix', 'say_yes_commit', 'say_no_limit', 'suggest_offer', 'change_topic',
    'share_about_herself', 'say_how_she_feels',
)
GROUP_LABELS = {
    'greet': 'greet', 'say_bye': 'say bye', 'react_briefly': 'react briefly',
    'answer_explain': 'answer / explain', 'ask_about_it': 'ask about it',
    'check_meaning': 'check what they meant', 'show_understanding': 'show she understands',
    'comfort': 'comfort / reassure', 'celebrate_thank': 'celebrate / thank', 'play': 'play (joke, tease, banter)',
    'share_view': 'share her view', 'agree': 'agree', 'push_back': 'push back',
    'apologise_fix': 'apologise / fix it', 'say_yes_commit': 'say yes / commit',
    'say_no_limit': 'say no / set a limit', 'suggest_offer': 'suggest / offer', 'change_topic': 'change topic',
    'share_about_herself': 'share something about herself', 'say_how_she_feels': 'say how she feels',
}
DIALS = ('length', 'warmth', 'playfulness', 'directness')
NO_SECOND = 'none'

# --- Scales: words the teacher writes -> numbers the student learns ---------------------------
SCALE = {'none': 0.0, 'a_little': 0.25, 'somewhat': 0.5, 'strong': 0.75, 'very': 1.0}
SIGNED = {'very_bad': -1.0, 'bad': -0.5, 'neutral': 0.0, 'good': 0.5, 'very_good': 1.0}
SCALE_WORDS = tuple(SCALE)
SIGNED_WORDS = tuple(SIGNED)

# --- 4. Mood ----------------------------------------------------------------------------------
MOOD_KEYS = ('mood', 'energy', 'interest', 'tension', 'closeness')
SIGNED_MOOD = frozenset({'mood', 'closeness'})

# --- 5. Flags and notes -----------------------------------------------------------------------
FLAGS = ('must_respond', 'yes_no_needed', 'something_went_wrong', 'her_open_question', 'cant_do')
NOTE_KINDS = ('her_commitment', 'correction', 'their_open_question', 'her_open_question', 'her_offer',
              'shared_matters')  # in keep-priority order, highest first
NOTE_LABELS = {
    'her_commitment': 'she agreed to', 'correction': 'they corrected her',
    'their_open_question': 'they asked and she has not answered', 'her_open_question': 'she asked and they have not answered',
    'her_offer': 'she offered', 'shared_matters': 'they shared something that matters',
}
# Memory ownership: what a piece of information establishes, kept explicit in the data.
MEMORY_KINDS = ('about_them', 'shared', 'her_own', 'her_preference')
MEMORY_LABELS = {
    'about_them': 'What she knows about them (things they told her; their experiences, not hers)',
    'shared': 'What the two of them have done together',
    'her_own': 'Her own experiences (things she herself did or thought)',
    'her_preference': 'What she likes (her own preferences and interests)',
}
MAX_NOTES = 6
MAX_MEMORIES = 5
WINDOW = 6


@dataclass
class Note:
    id: str
    kind: str
    text: str            # the original message text it came from
    opened_turn: int
    target: str | None = None   # what a correction corrects / what a commitment answers

    def to_dict(self) -> dict[str, Any]:
        return dict(id=self.id, kind=self.kind, text=self.text, opened_turn=self.opened_turn, target=self.target)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Note':
        return cls(**data)


@dataclass
class Message:
    speaker: str   # 'user' or 'columbina'
    text: str
    turn: int

    def to_dict(self) -> dict[str, Any]:
        return dict(speaker=self.speaker, text=self.text, turn=self.turn)


@dataclass
class Bundle:
    """Everything the student gets for one turn; the teacher sees a rendering of exactly this,
    plus her page and (outside the bundle) the full conversation."""
    turn: int
    message: str
    window: list[Message]                 # the last WINDOW messages before the current one
    notes: list[Note]
    memories: list[str]                   # at most MAX_MEMORIES, retrieval order
    notice: dict[str, Any]
    mood: dict[str, float]
    affinity: float                        # reserved, always 0.0 = "you don't know them well yet"
    time_of_day: str
    previous: dict[str, Any] | None        # previous turn's continued labels (Understand/Feel/Decide; + intention)
    world: list[str] = field(default_factory=list)   # her world: what she actually has to talk about
    rhythm: dict[str, Any] = field(default_factory=dict)   # her own recent replies, counted (rhythm.py)
    # Typed memories ({'kind': MEMORY_KINDS, 'text'}); when present they replace the untyped
    # memories/world blocks in the teacher's view. Older records have none and render as before.
    typed_memories: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(turn=self.turn, message=self.message, window=[m.to_dict() for m in self.window],
                    notes=[n.to_dict() for n in self.notes], memories=list(self.memories), notice=dict(self.notice),
                    mood=dict(self.mood), affinity=self.affinity, time_of_day=self.time_of_day,
                    previous=self.previous, world=list(self.world), rhythm=dict(self.rhythm),
                    typed_memories=[dict(m) for m in self.typed_memories])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'Bundle':
        return cls(turn=data['turn'], message=data['message'], window=[Message(**m) for m in data['window']],
                   notes=[Note.from_dict(n) for n in data['notes']], memories=list(data['memories']),
                   notice=dict(data['notice']), mood=dict(data['mood']), affinity=data['affinity'],
                   time_of_day=data['time_of_day'], previous=data['previous'], world=list(data.get('world', [])),
                   rhythm=dict(data.get('rhythm', {})),
                   typed_memories=[dict(m) for m in data.get('typed_memories', [])])


@dataclass
class Sample:
    """One complete teacher chain for a turn: thought -> Understand/Feel -> code -> Decide -> verdict."""
    index: int
    attempt: int
    thought: str
    understand: dict[str, Any]
    mood_after: dict[str, float]
    flags: dict[str, bool]
    allowed: list[str]
    decide: dict[str, Any]
    critic: dict[str, Any]
    accepted: bool
    view: str = 'full'           # 'full' or 'bounded' (the gap measurement)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(index=self.index, attempt=self.attempt, thought=self.thought, understand=self.understand,
                    mood_after=self.mood_after, flags=self.flags, allowed=self.allowed, decide=self.decide,
                    critic=self.critic, accepted=self.accepted, view=self.view, extra=self.extra)


def scale(word: str) -> float:
    return SCALE[word]


def signed(word: str) -> float:
    return SIGNED[word]


def understand_targets(understand: dict[str, Any]) -> dict[str, Any]:
    """Teacher words -> Student 1 numbers (no averaging; one sample is one example)."""
    return dict(
        acts=[1.0 if understand['acts'][a] else 0.0 for a in ACTS],
        tone=TONES.index(understand['tone']),
        request=REQUEST_KINDS.index(understand['request']),
        about_her=scale(understand['about_her']),
        sure=scale(understand['sure']),
        appraisal=[signed(understand['appraisal'][k]) for k in APPRAISALS_SIGNED]
        + [scale(understand['appraisal'][k]) for k in APPRAISALS],
        feelings=[scale(understand['feelings'][f]) for f in FEELINGS],
    )


def decide_targets(decide: dict[str, Any]) -> dict[str, Any]:
    second = decide['second']
    return dict(main=GROUPS.index(decide['main']),
                second=len(GROUPS) if second == NO_SECOND else GROUPS.index(second),
                dials=[scale(decide['dials'][d]) for d in DIALS])
