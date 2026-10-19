"""The simulated user: a written card, then one message per turn with a hidden reaction score."""
from __future__ import annotations

from typing import Any

from .contracts import SIGNED_WORDS, Message
from .llm import LLM
from .scenario import SITUATIONS, Scenario
from .teacher import _enum, _obj, prompt

CARD_SCHEMA = _obj({
    'name': {'type': 'string'}, 'age_range': {'type': 'string'}, 'where_they_are_in_life': {'type': 'string'},
    'writing_style': {'type': 'string'}, 'wants': {'type': 'string'}, 'mood_today': {'type': 'string'},
    'how_they_act': {'type': 'string'}, 'facts': {'type': 'array', 'items': {'type': 'string'}},
    'emotional_fact': {'type': 'integer'},
})
TURN_SCHEMA = _obj({'reaction': _enum(SIGNED_WORDS), 'reaction_note': {'type': 'string'},
                    'delay': _enum(['instant', 'minutes', 'hours']), 'message': {'type': 'string'},
                    'ending': {'type': 'boolean'}})
DELAY_SECONDS = {'instant': 15.0, 'minutes': 240.0, 'hours': 7200.0}


async def make_card(llm: LLM, scenario: Scenario) -> dict[str, Any]:
    return await llm.json('user_card', prompt('user_card'),
                          f'TYPE: {scenario.user_type} (group: {scenario.group})\nWANT: {scenario.want}\nSEED: {scenario.seed}',
                          CARD_SCHEMA, temperature=1.0)


def _card_text(card: dict[str, Any]) -> str:
    facts = '\n'.join(f'- {f}' for f in card['facts'])
    return (f'Name: {card["name"]} ({card["age_range"]}); {card["where_they_are_in_life"]}\n'
            f'Writing style: {card["writing_style"]}\nWants today: {card["wants"]}\nMood today: {card["mood_today"]}\n'
            f'How they act: {card["how_they_act"]}\nAbout their life:\n{facts}')


async def next_turn(llm: LLM, card: dict[str, Any], scenario: Scenario, history: list[Message], *,
                    turn_index: int, last_turn: bool) -> dict[str, Any]:
    situation = ''
    if turn_index == scenario.situation_turn:
        text = SITUATIONS[scenario.situation]
        if scenario.situation == 'memory' and scenario.memory_fact_index is not None:
            text = text.format(fact=card['facts'][scenario.memory_fact_index])
        situation = f'\nSITUATION FOR THIS TURN: {text}'
    elif turn_index == 0:
        situation = '\nSITUATION FOR THIS TURN: start the chat the way this person would'
    if last_turn:
        situation += '\nThis is your last message in this chat: wrap up or just stop, in your own way.'
    convo = '\n'.join(f'{"You" if m.speaker == "user" else "Columbina"}: {m.text}' for m in history) or '(nothing yet)'
    # The time of day is part of the scenario: both sides must live in the same hour.
    return await llm.json('user_turn', prompt('user_turn'),
                          f'USER CARD\n{_card_text(card)}\nTIME OF DAY for you: {scenario.time_of_day.replace("_", " ")}\n\n'
                          f'CONVERSATION SO FAR\n{convo}{situation}',
                          TURN_SCHEMA, temperature=1.0)
