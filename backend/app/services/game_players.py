"""Who the user is inside a game: their player name ("tearDrop12" in Minecraft).

Set when the user tells Jophiel (her ``this_is_me`` tool). With it she knows
who "me" is whoever else is online, and her memories of play name the user
instead of a player tag. Without it she still guesses the one other player
online, and says it's a guess.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GamePlayerLink, User

# What a player name can be, per game: Minecraft's is 3-16 letters, digits or _.
_NAMES = {"minecraft": re.compile(r"[A-Za-z0-9_]{3,16}")}


def user_player(db: Session, user_id: uuid.UUID, game: str) -> str | None:
    return db.scalar(select(GamePlayerLink.player_name).where(
        GamePlayerLink.user_id == user_id, GamePlayerLink.game == game))


def link(db: Session, user_id: uuid.UUID, game: str, player: str) -> str:
    """Save (or change) the user's player name. Raises ValueError for a name the
    game can't have. No commit."""
    player = player.strip().lstrip("@")
    rule = _NAMES.get(game)
    if not player or (rule is not None and not rule.fullmatch(player)):
        raise ValueError(f'"{player}" isn\'t a {game} player name')
    row = db.scalar(select(GamePlayerLink).where(GamePlayerLink.user_id == user_id, GamePlayerLink.game == game))
    if row is None:
        db.add(GamePlayerLink(user_id=user_id, game=game, player_name=player))
    else:
        row.player_name = player
    db.flush()
    return player


def user_name(db: Session, user_id: uuid.UUID) -> str | None:
    user = db.get(User, user_id)
    return user.name.strip() if user is not None and user.name and user.name.strip() else None


def who(db: Session, user_id: uuid.UUID, game: str) -> str | None:
    """"Vincent (tearDrop12 in the game)", "Vincent", "tearDrop12", or None."""
    name, player = user_name(db, user_id), user_player(db, user_id, game)
    if name and player and name.lower() != player.lower():
        return f"{name} ({player} in the game)"
    return name or player
