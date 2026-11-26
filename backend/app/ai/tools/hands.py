"""Her mind's tools for her hands on the user's phone (PLAN_PHONE_HANDS.md): she starts a job, changes its
instructions, pauses, resumes or stops it; the hands (app/ai/hands.py) do the screen work in their own loop and wake
her when there's something to know. The job as it stands is in her moment ("Your hands on their phone").

- **Whose say-so.** ``use_phone`` and ``resume_hands`` only on a turn the user's own words caused (a reply turn);
  never on a wake, never on a turn someone else's words caused (app/ai/chat.py builds none there). On a wake (her
  hands' own, say) she can still tell them more, pause or stop them.
- **Seeing without acting** (``read_phone_screen``, reply turns only): while a job runs, the hands' latest screen
  (text, from this process's short-term memory); otherwise one read of its own that doesn't take a job's place.
- **Recall** (``recent_phone_screens``): screens seen recently, text only, never actionable.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid

from langchain_core.tools import BaseTool, tool

from app.ai.tools.desktop_control.tools import PHONE_ARTIFACT_KEY
from app.services import hands, phone_actions, phone_control

log = logging.getLogger(__name__)

TOOL_NAMES = ("use_phone", "tell_hands", "pause_hands", "resume_hands", "stop_hands", "read_phone_screen",
              "recent_phone_screens")


def _said(job: hands.HandsJob, what: str) -> str:
    return f"{what} (job [{str(job.id)[:8]}], instructions version {job.version})."


def build_hands_tools(user_id: uuid.UUID, conversation_id: uuid.UUID, *, reply: bool,
                      message_id: uuid.UUID | None = None, zone: str | None = None) -> list[BaseTool]:
    """Her mind's hands tools for one evaluation. ``reply``: a turn the user's own words caused."""

    async def call(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs), None
        except hands.HandsError as exc:
            return None, f"Not done: {exc}."

    @tool
    async def use_phone(goal: str, instructions: str = "") -> str:
        """Put your hands to work on their phone's screen for what they asked, when no phone_action does it (the
        third song of an album, a chat with no notification, a setting, a game, reading a long page). Your hands
        work in the background on their real screen, one step at a time, and wake you when they're done, stuck,
        need you, or find something worth telling; you don't wait for them. goal: what's to be achieved, in a
        sentence. instructions: how, in your own words (where, in what order, what to avoid, when to ask you).
        A job already open is ended first. Tell them you're on it; you can talk while your hands work."""
        job, error = await call(hands.start, user_id, conversation_id, goal=goal, instructions=instructions,
                                message_id=message_id, zone=zone)
        return error or _said(job, "Started: your hands are on it and will wake you")

    @tool
    async def tell_hands(instructions: str, replace: bool = False) -> str:
        """Change or add to what your hands on their phone are doing: they read it before their next action.
        instructions: in your own words; added to what you said before, or instead of it with replace=true.
        Answering a question of theirs? Put the answer here, then resume_hands."""
        job, error = await call(hands.tell, user_id, instructions, replace=replace)
        return error or _said(job, "Told them")

    @tool
    async def pause_hands(reason: str = "") -> str:
        """Pause your hands on their phone now (an action already sent finishes; nothing more is sent)."""
        job, error = await call(hands.pause, user_id, reason)
        return error or _said(job, "Paused")

    @tool
    async def stop_hands(reason: str = "") -> str:
        """Stop your hands' job on their phone for good (an action already sent finishes; nothing more is sent)."""
        job, error = await call(hands.stop, user_id, reason)
        return error or _said(job, "Stopped")

    @tool
    async def resume_hands(instructions: str = "") -> str:
        """Let your paused hands carry on (after their Stop on the banner, only because they asked you to).
        instructions: anything to add first."""
        job, error = await call(hands.resume, user_id, instructions or None)
        return error or _said(job, "Resumed: your hands carry on and will wake you")

    @tool(response_format="content_and_artifact")
    async def read_phone_screen(wait_ms: int = 0) -> tuple[str, dict | None]:
        """See what's on their phone screen without acting on it (to answer "what's on my screen?"). While your
        hands are working, this is their latest look. To do anything there, use_phone or tell_hands."""
        if await asyncio.to_thread(_running, user_id):
            return await asyncio.to_thread(_hands_latest, user_id), None
        return await _look(user_id, conversation_id, wait_ms)

    @tool
    async def recent_phone_screens(screen: str | None = None) -> str:
        """Screens seen on their phone recently (the last 10, for 30 minutes, text only: the app and what was on
        it), to answer "what was that song?" later. No screen: the list. screen: one of them in full. Recall only."""
        seen = await asyncio.to_thread(phone_control.recent, user_id)
        if not seen:
            return "No phone screens remembered (none in the last 30 minutes, or Phone control was switched off)."
        if screen:
            for entry in reversed(seen):
                if entry.id == screen.strip():
                    return f"(Remembered, not actionable.)\n{entry.text}"
            return f"There's no remembered screen {screen} (you have: {', '.join(e.id for e in seen)})."
        now = time.time()
        rows = [f"- {e.id}: {e.app}, {max(1, int((now - e.at) // 60))} min ago" for e in reversed(seen)]
        return "Phone screens seen recently (newest first; recall only, not actionable):\n" + "\n".join(rows)

    if reply:
        return [use_phone, tell_hands, pause_hands, resume_hands, stop_hands, read_phone_screen, recent_phone_screens]
    return [tell_hands, pause_hands, stop_hands, recent_phone_screens]


def _running(user_id: uuid.UUID) -> bool:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        job = hands.open_job(db, user_id)
        return job is not None and job.status == "running"


def _hands_latest(user_id: uuid.UUID) -> str:
    seen = phone_control.recent(user_id)
    if not seen:
        return "Your hands are working but haven't seen a screen yet. Look again in a moment."
    latest = seen[-1]
    ago = max(0, int(time.time() - latest.at))
    return f"(Your hands' latest look, {ago} s ago; not actionable by you: tell_hands to act.)\n{latest.text}"


async def _look(user_id: uuid.UUID, conversation_id: uuid.UUID, wait_ms: int) -> tuple[str, dict | None]:
    """One read of the screen with a turn of its own (no job is running: nothing is taken from one)."""
    turn = f"look-{uuid.uuid4()}"
    await asyncio.to_thread(phone_control.begin_turn, user_id, turn)
    outcome = await asyncio.to_thread(phone_actions.act, user_id, conversation_id, "read_screen",
                                      {"wait_ms": max(0, min(int(wait_ms or 0), 5000)), "turn": turn})
    threading.Thread(target=_end_look, args=(user_id, conversation_id, turn), daemon=True).start()
    if outcome.status == "done" and outcome.screen:
        screen = outcome.screen
        image = screen.get("image") or None
        text = phone_control.render(screen, picture=bool(image))
        phone_control.remember(user_id, screen, phone_control.render(screen, picture=False))
        sid = str(screen.get("id"))
        artifact = ({PHONE_ARTIFACT_KEY: {"id": sid, "jpeg_b64": image["jpeg_base64"], "label": f"Phone screen {sid}"}}
                    if image else None)
        return text + "\n(A look only: to act, use_phone.)", artifact
    from app.ai.tools.phone_control import REASONS
    why = REASONS.get(outcome.reason or "", outcome.reason or outcome.text)
    return f"Couldn't see their screen: {why}.", None


def _end_look(user_id: uuid.UUID, conversation_id: uuid.UUID, turn: str) -> None:
    if phone_control.superseded(user_id, turn):
        return
    try:
        phone_actions.act(user_id, conversation_id, "end_control", {"turn": turn})
    except Exception:  # noqa: BLE001 - the phone ends control by itself 20 s after the last action
        log.info("hands: end_control after a look didn't reach the phone")
