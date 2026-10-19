"""5. What this turn needs: a few flags, and the moves code allows before anyone scores them."""
from __future__ import annotations

from typing import Any

from .contracts import GROUPS, Note, scale


def flags(notice: dict[str, Any], understand: dict[str, Any], notes: list[Note]) -> dict[str, bool]:
    acts = understand['acts']
    return dict(
        must_respond=bool(acts['asking'] or notice['question']),
        yes_no_needed=bool(acts['requesting'] and understand['request'] != 'no_request'),
        something_went_wrong=bool(acts['correcting_her']
                                  or (acts['complaining'] and scale(understand['about_her']) >= 0.75)),
        her_open_question=any(n.kind == 'her_open_question' for n in notes),
        cant_do=understand['request'] == 'outside_ability',
    )


def allowed_groups(notice: dict[str, Any], understand: dict[str, Any], turn_flags: dict[str, bool]) -> list[str]:
    """Remove impossible moves; the student (and teacher) only choose among the rest."""
    acts = understand['acts']
    blocked = set()
    if not turn_flags['something_went_wrong']:
        blocked.add('apologise_fix')
    if turn_flags['cant_do'] or not turn_flags['yes_no_needed']:
        blocked.add('say_yes_commit')
    if not (notice['greeting'] or notice['first_user_turn']):
        blocked.add('greet')
    if not (notice['farewell'] or acts['ending']):
        blocked.add('say_bye')
    return [g for g in GROUPS if g not in blocked]
