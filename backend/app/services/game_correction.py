"""She corrects herself when the game outdates what she just said.

A reply is written from the game as it was when the user spoke, and spoken while it is
written. When the game moved on meanwhile (``game_moment``), a small model reads what she
said against what changed and, only if the user would now be misled, writes one short
line correcting it, as a person does ("Oh wait, it just worked: I've got the trick now.").
Seen live (2026-10-17): she said she hadn't learned the jump-and-place move 2 s after
learning it, while her hands were already using it.

The line goes out through ``agent_say`` (kind ``correction``: any commentary mode, no
gap), so the socket says it right after the reply has finished playing.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid

from app.core.config import get_settings
from app.db.models import DesktopSession, User
from app.db.session import SessionLocal
from app.services import agent_say, game_moment

log = logging.getLogger(__name__)

_STICKER = re.compile(r"\[\[sticker:[^\]]*\]\]")

INSTRUCTIONS = """\
You are Columbina, playing Minecraft with {user} as your own player. A moment ago you said \
this to {user}:
"{said}"
You wrote it from your game as it was when they spoke. While you were saying it, your game \
changed (counted by the game itself, so this is true now):
{changed}

Would {user} now be misled by something you said? Only if a change makes something you said \
wrong or out of date (you said you hadn't learned a move and now you have; you said you were \
paused and you're playing; you named a count that's different now; you said you were about to \
do something that's already done or now can't happen), write ONE short line correcting \
yourself, the way someone does when they notice ("Oh wait, it just worked: I've got the trick \
now."). First person, casual, at most 20 words. Say only what's true now; no new promises; \
don't repeat the rest of what you said.
If what you said still holds, or the changes just carry on what you said you were doing (you \
said you're chopping and you got a log), leave "correction" empty. Most of the time it's empty.
{voice}"""

SCHEMA = {
    "type": "object",
    "properties": {"correction": {"type": "string"}},
    "required": ["correction"],
    "additionalProperties": False,
}


def enabled() -> bool:
    settings = get_settings()
    return settings.game_corrections_enabled and bool(settings.openai_api_key)


def write(said: str, changed: list[str], *, user: str, voice: str = "") -> str | None:
    """One correcting line, or None when what she said still holds."""
    from openai import OpenAI

    settings = get_settings()
    model = settings.game_correction_model
    client = OpenAI(api_key=settings.openai_api_key, timeout=10.0, max_retries=0)
    kwargs = {"reasoning_effort": "none"} if model.startswith(("gpt-5", "gpt-6")) else {}
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": INSTRUCTIONS.format(
            user=user, said=said, changed="\n".join(f"- {line}" for line in changed), voice=voice)}],
        response_format={"type": "json_schema", "json_schema": {"name": "correction", "strict": True,
                                                                 "schema": SCHEMA}},
        max_completion_tokens=120,
        store=False,
        **kwargs,
    )
    out = json.loads(response.choices[0].message.content or "{}")
    return " ".join(str(out.get("correction") or "").split()) or None


def check(user_id: uuid.UUID, said: str, moment: game_moment.Moment) -> str | None:
    """Correct ``said`` if the game has since made it wrong. Returns what happened, for the log."""
    from app.services import mind, mind_minecraft

    said = " ".join(_STICKER.sub("", said).split())
    if not said:
        return None
    started = time.time()
    with SessionLocal() as db:
        changed = game_moment.since(db, moment)
        if not changed:
            return None
        session = db.get(DesktopSession, moment.session_id)
        user = db.get(User, user_id)
        name = user.name if user and user.name else "the user"
        mode = session.commentary_mode if session else "occasional"
        db.commit()
    line = write(said, changed, user=name, voice=mind_minecraft.situation().voice)
    outcome = None
    if line:
        with SessionLocal() as db:
            outcome = agent_say.deliver(db, user_id=user_id, text=line,
                                        reasons=[f"correction: {'; '.join(changed)}"[:400]], commentary_mode=mode)
    mind._log({"ts": time.time(), "session": str(moment.session_id), "correction": line, "said": said[:300],
               "changed": changed, "outcome": outcome, "ms": round((time.time() - started) * 1000)})
    return outcome


def start(user_id: uuid.UUID, said: str, moment: game_moment.Moment | None) -> None:
    """``check`` in the background, so the next turn never waits for it."""
    if moment is None or not said or not enabled():
        return

    def run() -> None:
        try:
            check(user_id, said, moment)
        except Exception:  # noqa: BLE001 - a correction that fails is just not said
            log.exception("game correction failed for user %s", user_id)

    threading.Thread(target=run, name="game-correction", daemon=True).start()
