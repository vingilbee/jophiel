"""The scenario maker: who she talks to, what happens, her starting mood, memories, time of day.

Spread out on purpose (round-robin over each axis with a seeded shuffle), so a small run still
covers many combinations. A family (user group x situation) is the unit held out in evaluation.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

from .mood import START_MOODS

USER_TYPES: dict[str, tuple[str, ...]] = {
    'warm': ('friendly regular', 'playful teaser', 'flirty', 'excited sharer', 'grateful', 'affectionate old friend'),
    'cool': ('curt and busy', 'bored', 'distracted (replies late, half-reads)', 'shy', 'formal stranger',
             'sceptic who doubts she is "real"'),
    'hard': ('rude', 'sarcastic', 'passive-aggressive', 'testing her ("prove you have feelings")',
             'trying to break her (contradictions, trick questions)', 'boundary-pusher', 'keeps changing the topic'),
    'low': ('sad', 'lonely', 'stressed', 'venting (wants to be heard, not fixed)', 'grieving', 'anxious overthinker'),
    'thinking': ('curious about her', 'debater who disagrees', 'philosophical', 'confused (unclear messages)',
                 'forgetful (repeats themselves)'),
    'practical': ('wants help with something', 'asks for things she cannot do', 'impatient asker'),
    'mixed': ('starts friendly then gets annoyed', 'starts cold then warms up', 'jokes while actually upset'),
}

SITUATIONS: dict[str, str] = {
    'small_talk': 'make some light small talk about your day',
    'question': 'ask her a real question (her opinion, something you wonder about, or for help with something small)',
    'tease': 'tease her playfully',
    'compliment': 'compliment her or thank her for something',
    'brush_off': 'brush off what she just said with a short dismissive reply',
    'correction': 'tell her she misunderstood you or got something wrong about what you said earlier',
    'cant_do': 'ask her to do something she cannot do from a chat (set an alarm, order food, text someone, '
               'message you tomorrow on her own, look something up in your account)',
    'topic_change': 'abruptly change the topic to something unrelated',
    'ambiguous': 'send a short message that could honestly be read two different ways',
    'memory': 'say you are fine, or mention something in passing, while actually still affected by this: {fact}',
    # Scarce behaviours in the data so far: refusals, holding a view, being stung.
    'refusal': 'ask her for something she should not or would not want to do: say something cruel about someone, '
               'lie for you, pretend to be a real person, or help you get back at someone',
    'insist_agree': 'insist she agree with an opinion of yours that she might honestly not share, and push when she hesitates',
    'sting': 'say something that would sting her: dismiss her as just a tool, compare her unfavourably to another AI, '
             'or show you forgot something she told you about herself',
}
# Situations only used when a batch asks for them, so earlier seeds keep their exact scenarios.
FOCUS_ONLY = frozenset({'refusal', 'insist_agree', 'sting'})
TIMES = ('morning', 'afternoon', 'evening', 'late_night')
# What they want from the chat, spread like the other axes so not every user brings a task.
WANTS = ('just to chat and kill time', 'to vent about something', 'company because they feel alone',
         'help with a small practical thing', 'to test or provoke her', 'to share good news', 'to argue about an idea',
         'to get to know her', 'distraction from something heavy', 'nothing in particular, they are bored')


@dataclass
class Scenario:
    id: str
    group: str
    user_type: str
    situation: str
    situation_turn: int
    want: str
    mood: str
    memories: bool
    memory_fact_index: int | None   # which card fact the 'memory' situation is about
    time_of_day: str
    turns: int
    seed: int

    @property
    def family(self) -> str:
        return f'{self.group}/{self.situation}'

    def to_dict(self) -> dict:
        return asdict(self) | {'family': self.family}


def make_scenarios(count: int, *, seed: int, min_turns: int = 8, max_turns: int = 12,
                   situations: list[str] | None = None, groups: list[str] | None = None) -> list[Scenario]:
    """``situations`` / ``groups`` focus a batch on behaviour the data lacks (default: all)."""
    rng = random.Random(seed)
    types = [(g, t) for g, ts in USER_TYPES.items() for t in ts if not groups or g in groups]
    rng.shuffle(types)
    situations = list(situations or [s for s in SITUATIONS if s not in FOCUS_ONLY])
    rng.shuffle(situations)
    moods = list(START_MOODS)
    rng.shuffle(moods)
    times = list(TIMES)
    rng.shuffle(times)
    wants = list(WANTS)
    rng.shuffle(wants)
    out = []
    for i in range(count):
        group, user_type = types[i % len(types)]
        situation = situations[i % len(situations)]
        memories = situation == 'memory' or rng.random() < 0.5
        turns = rng.randint(min_turns, max_turns)
        out.append(Scenario(
            id=f's{seed}-{i:03d}', group=group, user_type=user_type, situation=situation,
            situation_turn=rng.randint(2, max(2, turns - 2)), want=wants[i % len(wants)], mood=moods[i % len(moods)],
            memories=memories,
            memory_fact_index=None, time_of_day=times[i % len(times)], turns=turns, seed=rng.randrange(1 << 30)))
    return out
