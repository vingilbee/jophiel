"""Game learning: what she plays becomes what she knows.

Three stages, each with its own rule about trust:

1. **Experience** (``record_experience``): her hands' skill outcomes and what
   the pack noticed, written by code as they happen. Facts, never a model's
   words about them. Append-only (``game_experiences``).
2. **Reflection** (``process_job``): when a session leaves play (offline,
   paused after a long enough stretch, ended), one model call reads the
   timeline of that stretch (outcomes, notices, plan events, and what the user
   said) plus what she already knows, and returns an episode, lessons and
   proposals. Sessions are rarely ended outright, so it goes by stretch.
   Each lesson and proposal must cite timeline refs; code maps them back to
   experience (or message) ids and drops anything without evidence.
3. **Use**, by level:
   - *lessons* (level 1) are applied automatically: ``skill`` lessons (how an
     action behaves) in her hands' prompt (``lessons_for_run``, which shows
     plan lessons too, as background), ``plan`` lessons (what to do, in what
     order, when) in Jophiel's game summary (``plan_lessons``), where she
     sets goals and steps and can hand a rule down as a plan constraint. A lesson starts ``trial``; later sessions that
     support or contradict it move it to ``confirmed`` or ``retired``
     (``lesson_status``). Only reflections on *different* sessions count.
   - *proposals* (level 3) are changes to the game code. The app never applies
     them: the Learning page lists them for the user to hand to a coding agent.
   - the *episode* becomes an ``EpisodicMemory`` so Jophiel can recall it in
     conversation.

Learned skills (level 2, stage H) will build on the same experience log.
Lessons are per user and game, like every other memory. Measuring learning:
``game_learning_apply_lessons`` is the A/B switch, and every run records which
lessons it was given (``lessons_applied``), see scripts/game_learning_report.py.
"""
from __future__ import annotations

import logging
import re
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    ChatMessage, Conversation, DesktopSession, DesktopSessionEvent, EpisodicMemory, GameExperience, GameLesson,
    GameProposal, GameReflectionJob,
)
from app.schemas.game_learning import Reflection
from app.services import game_players

log = logging.getLogger(__name__)

GAME_BY_PROFILE = {"minecraft_pack": "minecraft"}
GAME_NAMES = {"minecraft": "Minecraft"}
LEASE = timedelta(minutes=10)
MAX_ATTEMPTS = 3
# Timeline entries shown to the reflection model; longer sessions keep the start and the end.
MAX_TIMELINE = 400
MAX_EXAMPLES = 5  # outcome lines kept per proposal evidence entry
_KEY_RE = re.compile(r"[^a-z0-9_:\-]+")
_REF_RE = re.compile(r"^e(\d+)(?:-e?(\d+))?$")
_SAID_RE = re.compile(r"^u(\d+)$")
MAX_SAID = 60  # user lines shown per stretch
SAID_CHARS = 160
# A pause this long in the timeline is marked: a stretch can span an evening offline.
GAP_MARK = timedelta(minutes=30)
# A stretch starting this soon after her last recap of the same session is the same evening:
# she rewrites that recap to cover both, so one evening is one memory, not one per pause.
SAME_EVENING = timedelta(hours=2)
# Session states that start a reflection on the stretch just played, and the
# outcomes needed for one: offline or over takes the minimum; a pause (often
# brief) waits for a longer stretch, so a quick pause doesn't split it up.
ENDING_STATES = frozenset({"ended", "failed", "disconnected"})
PAUSE_STATES = frozenset({"paused"})


def game_for_profile(profile_id: str | None) -> str | None:
    return GAME_BY_PROFILE.get(profile_id or "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# 1. Experience
# --------------------------------------------------------------------------- #


def record_experience(db: Session, session_id: uuid.UUID, kind: str, payload: dict) -> int | None:
    """Append one experience for a game session (no-op for games without learning). Commits; returns its id."""
    session = db.get(DesktopSession, session_id)
    game = game_for_profile(session.profile_id) if session is not None else None
    if game is None:
        return None

    def experience() -> GameExperience:
        row = GameExperience(user_id=session.owner_user_id, session_id=session_id, game=game,
                             kind=kind, payload=payload)
        db.add(row)
        return row

    from app.services import observation_adapters

    row = experience()
    # Observed in the same transaction (docs/COLUMBINA_MEMORY_SESSION2_SPEC.md §6); if that fails, the
    # experience is still kept and its source state says where to recover from.
    try:
        db.flush()
        observation_adapters.experience_recorded(db, session, row)
        db.commit()
    except Exception:  # noqa: BLE001 - observing never loses the experience itself
        log.exception("game learning: observing an experience failed; it will be recovered")
        db.rollback()
        session = db.get(DesktopSession, session_id)
        row = experience()
        db.flush()
        try:
            with db.begin_nested():
                observation_adapters.mark_missed(db, session, row.id)
        except Exception:  # noqa: BLE001
            log.exception("game learning: could not mark experience %s for recovery", row.id)
        db.commit()
    return row.id


# --------------------------------------------------------------------------- #
# 3a. Lessons in her hands' prompt
# --------------------------------------------------------------------------- #


def active_lessons(db: Session, user_id: uuid.UUID, game: str, limit: int | None = None) -> list[GameLesson]:
    """Confirmed lessons first (best supported first), then trial ones (newest first)."""
    query = (
        select(GameLesson)
        .where(GameLesson.user_id == user_id, GameLesson.game == game, GameLesson.status != "retired")
        .order_by(case((GameLesson.status == "confirmed", 0), else_=1),
                  (GameLesson.support_count - GameLesson.contradict_count).desc(),
                  GameLesson.updated_at.desc())
    )
    if limit is not None:
        query = query.limit(limit)
    return list(db.scalars(query))


def lessons_for_run(db: Session, session_id: uuid.UUID) -> list[dict]:
    """The lessons for a new driver run of this session, recorded as a
    ``lessons_applied`` experience either way (so runs with and without
    lessons can be compared). Empty when learning is off for the session."""
    settings = get_settings()
    session = db.get(DesktopSession, session_id)
    game = game_for_profile(session.profile_id) if session is not None else None
    if game is None:
        return []
    applied = settings.game_learning_apply_lessons
    lessons = active_lessons(db, session.owner_user_id, game, settings.game_learning_prompt_lessons) if applied else []
    db.add(GameExperience(
        user_id=session.owner_user_id, session_id=session_id, game=game, kind="lessons_applied",
        payload={"enabled": applied, "lesson_ids": [str(lesson.id) for lesson in lessons],
                 "keys": [lesson.normalized_key for lesson in lessons]},
    ))
    db.commit()
    # id and revision: which lesson versions her hands saw (the learning capture records them).
    return [{"situation": lesson.situation, "advice": lesson.advice, "status": lesson.status,
             "level": lesson.level, "id": str(lesson.id), "revision": lesson.revision} for lesson in lessons]


def plan_lessons(db: Session, user_id: uuid.UUID, game: str) -> list[GameLesson]:
    """Plan lessons for Jophiel's game summary (none when the A/B switch is off)."""
    settings = get_settings()
    if not settings.game_learning_apply_lessons:
        return []
    return [lesson for lesson in active_lessons(db, user_id, game)
            if lesson.level == "plan"][:settings.game_learning_prompt_lessons]


def skill_lessons(db: Session, user_id: uuid.UUID, game: str) -> list[GameLesson]:
    """Skill lessons (how her moves behave), for Jophiel too: she plans with them."""
    settings = get_settings()
    if not settings.game_learning_apply_lessons:
        return []
    return [lesson for lesson in active_lessons(db, user_id, game)
            if lesson.level == "skill"][:settings.game_learning_prompt_lessons]


KNOWN_GAPS_SHOWN = 6


def known_gaps(db: Session, user_id: uuid.UUID, game: str) -> list[GameProposal]:
    """Changes she proposed that aren't built yet (open or accepted): what she knows she can't
    do, so she can say so (and ask for help) instead of trying."""
    return list(db.scalars(
        select(GameProposal)
        .where(GameProposal.user_id == user_id, GameProposal.game == game,
               GameProposal.status.in_(("open", "accepted")))
        .order_by(GameProposal.occurrences.desc(), GameProposal.updated_at.desc())
        .limit(KNOWN_GAPS_SHOWN)))


def latest_episode(db: Session, user_id: uuid.UUID, game: str, *, within: timedelta = timedelta(days=7)
                   ) -> EpisodicMemory | None:
    """Her recap of the last session of this game she reflected on, if recent."""
    return db.scalar(
        select(EpisodicMemory)
        .where(EpisodicMemory.user_id == user_id, EpisodicMemory.status == "active",
               EpisodicMemory.context["source"].astext == "game", EpisodicMemory.context["game"].astext == game,
               EpisodicMemory.event_at >= _now() - within)
        .order_by(EpisodicMemory.event_at.desc()).limit(1))


# --------------------------------------------------------------------------- #
# 2. Reflection: scheduling
# --------------------------------------------------------------------------- #


def schedule_reflection(db: Session, session: DesktopSession, to_state: str) -> None:
    """Queue a reflection on the stretch played since the last one, when the
    session leaves play with enough new outcomes (in the caller's transaction;
    the worker polls). Runs in a savepoint and never fails the transition."""
    game = game_for_profile(session.profile_id)
    if game is None or to_state not in ENDING_STATES | PAUSE_STATES:
        return
    settings = get_settings()
    needed = (settings.game_learning_min_outcomes if to_state in ENDING_STATES
              else settings.game_learning_segment_outcomes)
    try:
        with db.begin_nested():
            after = db.scalar(select(func.max(GameReflectionJob.up_to_experience_id))
                              .where(GameReflectionJob.session_id == session.id)) or 0
            up_to = db.scalar(select(func.max(GameExperience.id)).where(
                GameExperience.session_id == session.id, GameExperience.id > after))
            if up_to is None:
                return
            outcomes = db.scalar(select(func.count()).select_from(GameExperience).where(
                GameExperience.session_id == session.id, GameExperience.kind == "skill_outcome",
                GameExperience.id > after, GameExperience.id <= up_to)) or 0
            if outcomes < needed:
                return
            db.add(GameReflectionJob(user_id=session.owner_user_id, session_id=session.id, game=game,
                                     from_experience_id=after, up_to_experience_id=up_to))
            db.flush()
    except IntegrityError:
        pass  # the same stretch was queued concurrently
    except Exception:  # learning never blocks a session change
        log.exception("could not queue a game reflection for session %s", session.id)


def next_pending_job_id(db: Session) -> uuid.UUID | None:
    now = _now()
    return db.scalar(
        select(GameReflectionJob.id)
        .where(or_(
            GameReflectionJob.status == "pending",
            (GameReflectionJob.status == "processing") & (GameReflectionJob.lease_expires_at <= now),
        ))
        .order_by(GameReflectionJob.created_at)
        .limit(1)
    )


# --------------------------------------------------------------------------- #
# 2. Reflection: the timeline the model reads
# --------------------------------------------------------------------------- #


@dataclass
class Timeline:
    text: str
    refs: dict[int, int]  # timeline ref number -> experience id
    lines: dict[int, str]  # ref number -> its outcome line (proposal examples)
    outcomes: int
    said: dict[int, str] = field(default_factory=dict)  # user line ref (u1, u2, ...) -> chat message id


def _clock(at: datetime, start: datetime) -> str:
    seconds = max(0, int((at - start).total_seconds()))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _outcome_line(payload: dict) -> str:
    params = ", ".join(f"{k}={v}" for k, v in (payload.get("params") or {}).items())
    reason = f": {payload['reason']}" if payload.get("reason") else ""
    seconds = f" ({payload['seconds']:.0f} s)" if isinstance(payload.get("seconds"), (int, float)) else ""
    data = payload.get("data") or {}
    shown = " " + ", ".join(f"{k}={v}" for k, v in list(data.items())[:4]) if data else ""
    return f"{payload.get('skill')}({params}) -> {payload.get('status')}{reason}{seconds}{shown}"


def _event_line(event: DesktopSessionEvent) -> str | None:
    payload = event.payload or {}
    if event.type == "desktop.plan_set":
        goal = payload.get("goal")
        return f'new goal{": " + repr(goal) if goal else ""} (set by {payload.get("source", "?")})'
    if event.type == "desktop.step_done":
        return f"plan step {payload.get('step_id')} done"
    if event.type in ("desktop.stuck", "desktop.needs_human"):
        what = "stuck" if event.type == "desktop.stuck" else "needed the user"
        return f'{what} on step {payload.get("step_id")}: "{payload.get("note", "")}"'
    if event.type == "desktop.session_state":
        return f"session {payload.get('state')} ({payload.get('reason')})"
    return None


def build_timeline(session: DesktopSession, experiences: list[GameExperience],
                   events: list[DesktopSessionEvent], said: list[tuple[datetime, str, str]] = (),
                   *, speaker: str = "the user") -> Timeline:
    """Merge experiences, session events and what the user said (``said``:
    time, message id, text; ``speaker``: what to call them) into numbered lines,
    oldest first. The same outcome repeated back to back is one line with a ref
    range; a long break is marked."""
    start = session.started_at
    items: list[tuple[datetime, int, object]] = [(e.occurred_at, 0, e) for e in experiences]
    items += [(e.occurred_at, 1, e) for e in events]
    items += [(at, 2, (message_id, text)) for at, message_id, text in list(said)[-MAX_SAID:]]
    items.sort(key=lambda item: (item[0], item[1]))
    refs: dict[int, int] = {}
    said_refs: dict[int, str] = {}
    ref_lines: dict[int, str] = {}
    rows: list[list] = []  # [clock, first_ref, last_ref, text, repeat]
    outcomes = 0
    previous: datetime | None = None
    for at, _, item in items:
        if previous is not None and at - previous >= GAP_MARK:
            hours = (at - previous).total_seconds() / 3600
            rows.append([None, None, None, f"--- {hours:.0f} h later ---" if hours >= 1
                         else f"--- {hours * 60:.0f} min later ---", 1])
        previous = at
        if isinstance(item, tuple):
            ref = len(said_refs) + 1
            said_refs[ref] = item[0]
            words = " ".join(item[1].split())[:SAID_CHARS]
            rows.append([_clock(at, start), None, None, f'u{ref} {speaker} said: "{words}"', 1])
            continue
        if isinstance(item, DesktopSessionEvent):
            line = _event_line(item)
            if line:
                rows.append([_clock(at, start), None, None, line, 1])
            continue
        if item.kind == "lessons_applied":
            continue
        ref = len(refs) + 1
        refs[ref] = item.id
        if item.kind == "skill_outcome":
            outcomes += 1
            text = _outcome_line(item.payload)
        else:
            text = f"noticed [{item.payload.get('urgency', '?')}] {item.payload.get('text', '')}"
        ref_lines[ref] = text
        last = rows[-1] if rows else None
        if last is not None and last[3] == text and last[1] is not None:
            last[2], last[4] = ref, last[4] + 1
            continue
        rows.append([_clock(at, start), ref, ref, text, 1])

    def render(row: list) -> str:
        clock, first, last, text, repeat = row
        if clock is None:
            return text
        if first is None:
            return f"[{clock}] {text}"
        label = f"e{first}" if first == last else f"e{first}-e{last}"
        return f"[{clock}] {label} {text}" + (f" (x{repeat})" if repeat > 1 else "")

    lines = [render(row) for row in rows]
    if len(lines) > MAX_TIMELINE:
        head = MAX_TIMELINE // 8
        lines = [*lines[:head], f"... {len(lines) - MAX_TIMELINE} entries left out ...",
                 *lines[-(MAX_TIMELINE - head):]]
    return Timeline("\n".join(lines), refs, ref_lines, outcomes, said_refs)


def resolve_refs(refs: list[str], timeline: Timeline) -> list[int]:
    """Timeline refs ("e4", "e7-e9") -> experience ids; unknown refs are dropped."""
    ids: list[int] = []
    for raw in refs:
        match = _REF_RE.match(raw.strip().lower())
        if match is None:
            continue
        first = int(match.group(1))
        last = int(match.group(2) or first)
        for ref in range(first, min(last, first + 50) + 1):
            if ref in timeline.refs and timeline.refs[ref] not in ids:
                ids.append(timeline.refs[ref])
    return ids


def resolve_said(refs: list[str], timeline: Timeline) -> list[str]:
    """User line refs ("u3") -> chat message ids; unknown refs are dropped."""
    ids: list[str] = []
    for raw in refs:
        match = _SAID_RE.match(raw.strip().lower())
        if match and int(match.group(1)) in timeline.said and timeline.said[int(match.group(1))] not in ids:
            ids.append(timeline.said[int(match.group(1))])
    return ids


def _example_lines(refs: list[str], timeline: Timeline) -> list[str]:
    by_id = {exp_id: ref for ref, exp_id in timeline.refs.items()}
    return [timeline.lines[by_id[i]] for i in resolve_refs(refs, timeline)][:MAX_EXAMPLES]


def _clause(text: str) -> str:
    """'Crafting tools' -> 'crafting tools', to read after 'When'; a leading
    'when'/'if' the model wrote itself is dropped; acronyms stay."""
    text = re.sub(r"^(when|if|whenever)\s+", "", text.strip().rstrip("."), flags=re.IGNORECASE)
    return text[:1].lower() + text[1:] if text[1:2].islower() else text


def normalize_key(raw: str) -> str:
    return _KEY_RE.sub("_", raw.strip().lower()).strip("_")[:96]


# --------------------------------------------------------------------------- #
# 2. Reflection: the model call
# --------------------------------------------------------------------------- #


def reflection_system_prompt(game: str, skills: list[str], who: str | None = None) -> str:
    name = GAME_NAMES.get(game, game)
    skill_list = ", ".join(skills) if skills else "(see the timeline)"
    who = who or "the user"
    return f"""You are Jophiel, looking back on a stretch of {name} you just played with your own player, \
together with {who}. \
Your hands choose one skill at a time ({skill_list}); each ends with an outcome (done, failed and why, lost, \
blocked, hurt, timeout, stopped, refused). The timeline is recorded by code and is accurate. Entries with an \
id (e4, or e7-e9 for the same thing repeated) are your outcomes and what you noticed; u1, u2, ... are what the \
user said to you (spoken words are transcribed and can be misheard). "--- 5 h later ---" marks a long break.

Write three things.

1. episode: your memory of this time with {who}, the way you'd bring it up with them later: 2-4 short sentences (under 90 words), \
first person, past tense, "we" for what you did together, and their name (never just their player name). \
What you two set out to do and how it went; what you found, made, lost; what each of you did for the other \
(a gift, help, a rescue); what they asked of you or cared about; anything left unfinished (things dropped \
when you died, a promise, a place to go back to). Only what the timeline shows: no feelings they didn't \
show, nothing that didn't happen. A memory, not a log: no skill names, retries, coordinates or session \
states. Refer to them by name, not "he" or "she" (you can't know which they'd want). Don't open with a \
break; across one only say when it was ("the next evening"). If an earlier recap of the same evening is \
given, write one memory covering all of it (it replaces the earlier one). \
episode_importance: 0 for an uneventful few minutes, up to 1 for something you'd bring up again. Empty \
episode with importance 0 if nothing really happened.

2. lessons: what will make you play better next time, as situation -> advice, each with a level. \
"skill": how an action behaves, for your hands, who pick one skill at a time ("craft fails without a crafting \
table in reach: place one first"). "plan": what to do, in what order, or when, for you when you set goals and \
steps ("before going underground, bring food and torches"; "get shelter before night when you have no \
bed"). Each must be shown by the timeline and cite its refs in evidence. Good lessons are concrete and \
general: how the game's rules work or how your skills behave. Look at every failure: when its reason says \
what was missing ("needs a crafting table nearby", "missing ingredients", "no tool that can harvest stone"), \
that is a skill lesson (what to do first next time). A standing instruction from the user about how you play \
("you can break blocks", "always stay close to me") is a lesson too, citing the user's line (u-ref). Not \
lessons: vague advice ("be careful"), this world's layout, facts about the user's life, or a skill doing \
what its outcome says (a skill cut short by danger is working as designed).
For a lesson you already have (listed below): verdict "supports" if this session shows it again (you may \
sharpen the wording), "contradicts" if this session shows it's wrong (keep its wording). Otherwise verdict \
"new" with a new stable key "area:short_snake_case" (e.g. "crafting:place_table_first"). Never re-propose a \
retired lesson. At most 6; none is fine.

3. proposals: problems in the game code itself, which no choice of yours could fix: a skill that fails or \
misbehaves in a way it should handle itself (e.g. go_to ending "lost: no path" again and again over ordinary \
terrain like gravel or a one-block step; giving up too early; a wrong reason; a loop), or a skill you clearly \
needed and don't have. The same failure repeated with the same reason is a strong sign. A skill failing at \
its own job is a proposal, never a lesson for you (follow_player losing a player who is just walking: the \
skill should keep up). So is a "refused" decision whose request looked sensible (the pack rejected it: its \
limits or checks may be wrong or unclear). Mistakes in your own choices are lessons, not proposals. Each: \
title, area (the skill's name, "new_skill", "runner", "noticing" or "other"), problem (what you wanted vs \
what happened, citing outcomes), suggestion (what a programmer should change), evidence refs. Same problem \
as a listed proposal: reuse its key. At most 3; none is fine.

Most sessions teach little. Don't invent lessons or proposals to fill the lists."""


def reflection_input(game: str, session: DesktopSession, timeline: Timeline, lessons: list[GameLesson],
                     proposals: list[GameProposal], *, window: tuple[datetime, datetime] | None = None,
                     earlier: EpisodicMemory | None = None) -> str:
    start, end = window or (session.started_at, session.ended_at or _now())
    minutes = max(1, int((end - start).total_seconds() // 60))
    lines = [f"A stretch of {GAME_NAMES.get(game, game)} you played: {minutes} min, ending {end:%A %H:%M} UTC.", ""]
    if earlier is not None:
        lines += [f"Your recap of earlier this evening: {earlier.summary}",
                  "Your episode replaces it: keep what matters from it, then add this stretch.", ""]
    lines += ["Timeline (oldest first):", timeline.text or "(nothing recorded)", ""]
    current = [lesson for lesson in lessons if lesson.status != "retired"]
    retired = [lesson for lesson in lessons if lesson.status == "retired"]
    lines.append("Lessons you already have:")
    lines += [f"- {lesson.normalized_key} [{lesson.status}, +{lesson.support_count}/-{lesson.contradict_count}]: "
              f"({lesson.level}) when {lesson.situation}: {lesson.advice}" for lesson in current] or ["(none)"]
    if retired:
        lines.append("Retired lessons (never propose again): " + ", ".join(lesson.normalized_key for lesson in retired))
    lines.append("")
    lines.append("Proposals already made:")
    lines += [f"- {p.normalized_key} [{p.status}, seen {p.occurrences}x]: {p.title}" for p in proposals] or ["(none)"]
    return "\n".join(lines)


class OpenAIReflector:
    """One structured call per reflection; no retries beyond the job's own."""

    def __init__(self) -> None:
        from openai import OpenAI

        from app.ai.agent import supports_reasoning_controls

        settings = get_settings()
        self.model = settings.game_learning_model
        effort = settings.game_learning_reasoning_effort
        self.reasoning = {"effort": effort} if effort and supports_reasoning_controls(self.model) else None
        self._client = OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else OpenAI()

    def __call__(self, system: str, text: str) -> Reflection:
        extra = {"reasoning": self.reasoning} if self.reasoning else {}
        response = self._client.responses.parse(
            model=self.model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": text}],
            text_format=Reflection,
            **extra,
        )
        if response.output_parsed is None:
            raise ValueError("reflection returned no parsed output")
        return response.output_parsed


Reflector = Callable[[str, str], Reflection]


# --------------------------------------------------------------------------- #
# 2. Reflection: applying it
# --------------------------------------------------------------------------- #


def lesson_status(support: int, contradict: int) -> str:
    """Trust from evidence counts. Support and contradiction each come from
    distinct sessions, so one bad session can't confirm or retire a lesson."""
    if contradict >= 2 and contradict >= support:
        return "retired"
    if support >= 2 and support > 2 * contradict:
        return "confirmed"
    return "trial"


def _seen_in(evidence: list, session_id: uuid.UUID) -> bool:
    return any(entry.get("session_id") == str(session_id) for entry in evidence)


def note_trial_lesson(db: Session, *, user_id: uuid.UUID, game: str, session_id: uuid.UUID, key: str,
                      situation: str, advice: str, level: str, verdict: str, experience_ids: list[int],
                      trial_id: uuid.UUID) -> str:
    """A bounded trial's lesson, under the same rules as a reflection's (no commit): new lessons start as
    ``trial``; one session counts once, so a second trial in the same session never confirms it; trust comes
    from distinct sessions (``lesson_status``); a retired lesson stays retired. ``verdict``: supports /
    contradicts. Returns what happened, in a word or two."""
    key = normalize_key("trial:" + key)
    if not key or not advice.strip() or not experience_ids:
        return "dropped: no key, advice or experience"
    entry = {"session_id": str(session_id), "verdict": "new" if verdict == "supports" else verdict,
             "experience_ids": experience_ids, "at": _now().isoformat(), "trial_id": str(trial_id)}
    lesson = db.scalar(select(GameLesson).where(GameLesson.user_id == user_id, GameLesson.game == game,
                                                GameLesson.normalized_key == key).with_for_update())
    if lesson is None:
        if verdict != "supports":
            return "dropped: nothing to contradict"
        active = db.scalar(select(func.count()).select_from(GameLesson).where(
            GameLesson.user_id == user_id, GameLesson.game == game, GameLesson.status != "retired")) or 0
        if active >= get_settings().game_learning_max_active_lessons:
            return "dropped: full"
        db.add(GameLesson(user_id=user_id, game=game, normalized_key=key, situation=_clause(situation),
                          advice=advice.strip(), level=level, support_count=1, contradict_count=0, status="trial",
                          evidence=[entry]))
        db.flush()
        return "new (tentative)"
    if lesson.status == "retired":
        return "dropped: retired"
    if _seen_in(lesson.evidence, session_id):
        return "dropped: same session"
    if verdict == "contradicts":
        lesson.contradict_count += 1
    else:
        lesson.support_count += 1
    lesson.evidence = [*lesson.evidence, entry]
    lesson.status = lesson_status(lesson.support_count, lesson.contradict_count)
    lesson.revision += 1
    return f"{'contradicted' if verdict == 'contradicts' else 'supported'} ({lesson.status})"


def apply_reflection(db: Session, *, user_id: uuid.UUID, game: str, session: DesktopSession,
                     reflection: Reflection, timeline: Timeline, ended_at: datetime | None = None,
                     earlier: EpisodicMemory | None = None) -> dict:
    """Write the reflection's lessons, proposals and episode (no commit).
    Returns what changed, for the job's ``result``."""
    settings = get_settings()
    at = _now().isoformat()
    sid = str(session.id)
    result: dict = {"outcomes": timeline.outcomes, "lessons": {"new": [], "supported": [], "contradicted": []},
                    "proposals": {"new": [], "repeated": [], "reopened": []}, "dropped": [], "episode_id": None}
    lessons = {lesson.normalized_key: lesson for lesson in db.scalars(
        select(GameLesson).where(GameLesson.user_id == user_id, GameLesson.game == game))}
    active = sum(1 for lesson in lessons.values() if lesson.status != "retired")

    for item in reflection.lessons:
        key = normalize_key(item.key)
        ids = resolve_refs(item.evidence, timeline)
        said = resolve_said(item.evidence, timeline)
        if not key or not (ids or said) or not item.advice.strip():
            result["dropped"].append({"lesson": key, "why": "no key, advice or valid evidence"})
            continue
        entry = {"session_id": sid, "verdict": item.verdict, "experience_ids": ids, "at": at}
        if said:
            entry["message_ids"] = said
        lesson = lessons.get(key)
        if lesson is None:
            if item.verdict != "new" or active >= settings.game_learning_max_active_lessons:
                result["dropped"].append({"lesson": key, "why": "unknown key" if item.verdict != "new" else "full"})
                continue
            lesson = GameLesson(user_id=user_id, game=game, normalized_key=key, situation=_clause(item.situation),
                                advice=item.advice.strip(), level=item.level, support_count=1, contradict_count=0,
                                status="trial", evidence=[entry])
            db.add(lesson)
            lessons[key] = lesson
            active += 1
            result["lessons"]["new"].append(key)
            continue
        if lesson.status == "retired" or _seen_in(lesson.evidence, session.id):
            why = "retired" if lesson.status == "retired" else "same session"
            result["dropped"].append({"lesson": key, "why": why})
            continue
        if item.verdict == "contradicts":
            lesson.contradict_count += 1
            result["lessons"]["contradicted"].append(key)
        else:  # "new" for a known key is the same lesson seen again
            lesson.support_count += 1
            lesson.situation, lesson.advice = _clause(item.situation), item.advice.strip()
            lesson.level = item.level
            result["lessons"]["supported"].append(key)
        lesson.evidence = [*lesson.evidence, entry]
        lesson.status = lesson_status(lesson.support_count, lesson.contradict_count)
        lesson.revision += 1

    proposals = {p.normalized_key: p for p in db.scalars(
        select(GameProposal).where(GameProposal.user_id == user_id, GameProposal.game == game))}
    for item in reflection.proposals:
        key = normalize_key(item.key)
        ids = resolve_refs(item.evidence, timeline)
        if not key or not ids:
            result["dropped"].append({"proposal": key, "why": "no key or valid evidence"})
            continue
        entry = {"session_id": sid, "experience_ids": ids, "examples": _example_lines(item.evidence, timeline),
                 "at": at}
        proposal = proposals.get(key)
        if proposal is None:
            proposal = GameProposal(user_id=user_id, game=game, normalized_key=key, title=item.title.strip()[:200],
                                    area=item.area.strip()[:64] or "other", problem=item.problem.strip(),
                                    suggestion=item.suggestion.strip(), evidence=[entry])
            db.add(proposals.setdefault(key, proposal))
            result["proposals"]["new"].append(key)
            continue
        if _seen_in(proposal.evidence, session.id):
            continue
        if proposal.status == "implemented":
            # The fix didn't hold: back on the list, with the new evidence.
            entry["after_implemented"] = True
            proposal.status = "open"
            result["proposals"]["reopened"].append(key)
        else:
            result["proposals"]["repeated"].append(key)
        proposal.occurrences += 1
        proposal.evidence = [*proposal.evidence, entry]

    episode = reflection.episode.strip()
    if episode:
        from app.services.long_term_memory import _embedding

        importance = min(1.0, max(0.0, reflection.episode_importance))
        # Dated when the play happened (the stretch's end), not when it was written up.
        memory = EpisodicMemory(
            user_id=user_id, conversation_id=session.conversation_id,
            event_at=ended_at or session.ended_at or _now(),
            summary=episode, embedding=_embedding(episode),
            importance=max(importance, earlier.importance) if earlier is not None else importance, confidence=1.0,
            context={"source": "game", "game": game, "session_id": sid},
        )
        db.add(memory)
        db.flush()
        result["episode_id"] = str(memory.id)
        if earlier is not None:  # the evening's recap now covers it
            earlier.status = "superseded"
            earlier.context = {**(earlier.context or {}), "superseded_by": str(memory.id)}
            result["superseded_episode_id"] = str(earlier.id)
    return result


def process_job(db: Session, job_id: uuid.UUID, reflector: Reflector | None = None) -> bool:
    """Claim one reflection job, reflect, and apply. True if it finished."""
    now = _now()
    claim = secrets.token_hex(8)
    job = db.scalar(
        select(GameReflectionJob).where(
            GameReflectionJob.id == job_id,
            or_(GameReflectionJob.status == "pending",
                (GameReflectionJob.status == "processing") & (GameReflectionJob.lease_expires_at <= now)),
        ).with_for_update(skip_locked=True)
    )
    if job is None:
        db.commit()
        return False
    job.status, job.attempts, job.lease_expires_at = "processing", job.attempts + 1, now + LEASE
    job.claim_token = claim
    db.commit()
    try:
        session = db.get(DesktopSession, job.session_id)
        query = select(GameExperience).where(GameExperience.session_id == job.session_id,
                                             GameExperience.id > job.from_experience_id)
        if job.up_to_experience_id:
            query = query.where(GameExperience.id <= job.up_to_experience_id)
        experiences = list(db.scalars(query.order_by(GameExperience.id)))
        window = _window(db, session, job, experiences)
        events = _stretch_events(db, session.id, window)
        who = game_players.who(db, job.user_id, job.game)
        speaker = game_players.user_name(db, job.user_id) or "the user"
        timeline = build_timeline(session, experiences, events, _user_lines(db, job.user_id, window), speaker=speaker)
        if timeline.outcomes < get_settings().game_learning_min_outcomes:
            return _finish(db, job_id, claim, "skipped", {"outcomes": timeline.outcomes, "why": "too short"})
        lessons = list(db.scalars(select(GameLesson).where(
            GameLesson.user_id == job.user_id, GameLesson.game == job.game)
            .order_by(GameLesson.status, GameLesson.normalized_key)))
        proposals = list(db.scalars(select(GameProposal).where(
            GameProposal.user_id == job.user_id, GameProposal.game == job.game,
            GameProposal.status != "rejected").order_by(GameProposal.normalized_key)))
        skills = sorted({e.payload.get("skill") for e in experiences
                         if e.kind == "skill_outcome" and e.payload.get("skill")})
        reflect = reflector or OpenAIReflector()
        earlier = _same_evening_episode(db, job.user_id, session.id, experiences[0].occurred_at)
        reflection = reflect(reflection_system_prompt(job.game, skills, who),
                             reflection_input(job.game, session, timeline, lessons, proposals, window=window,
                                              earlier=earlier))
        result = apply_reflection(db, user_id=job.user_id, game=job.game, session=session,
                                  reflection=reflection, timeline=timeline,
                                  ended_at=experiences[-1].occurred_at if experiences else None, earlier=earlier)
        return _finish(db, job_id, claim, "done", result)
    except Exception as exc:
        db.rollback()
        log.exception("game reflection %s failed", job_id)
        failed = db.scalar(select(GameReflectionJob).where(
            GameReflectionJob.id == job_id, GameReflectionJob.claim_token == claim))
        if failed is not None:
            failed.status = "pending" if failed.attempts < MAX_ATTEMPTS else "failed"
            failed.claim_token = failed.lease_expires_at = None
            failed.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            db.commit()
        return False


def _same_evening_episode(db: Session, user_id: uuid.UUID, session_id: uuid.UUID,
                          stretch_start: datetime) -> EpisodicMemory | None:
    """Her recap of this session's previous stretch, if this one picks up soon after it."""
    return db.scalar(
        select(EpisodicMemory)
        .where(EpisodicMemory.user_id == user_id, EpisodicMemory.status == "active",
               EpisodicMemory.context["source"].astext == "game",
               EpisodicMemory.context["session_id"].astext == str(session_id),
               EpisodicMemory.event_at >= stretch_start - SAME_EVENING)
        .order_by(EpisodicMemory.event_at.desc()).limit(1))


def _window(db: Session, session: DesktopSession, job: GameReflectionJob,
            experiences: list[GameExperience]) -> tuple[datetime, datetime]:
    """When the stretch began (the end of the previous one, or the session's
    start) and ended (its last experience)."""
    start = session.started_at
    if job.from_experience_id:
        previous = db.get(GameExperience, job.from_experience_id)
        start = previous.occurred_at if previous is not None else start
    end = experiences[-1].occurred_at if experiences else (session.ended_at or _now())
    return start, end + timedelta(seconds=5)


def _stretch_events(db: Session, session_id: uuid.UUID, window: tuple[datetime, datetime]) -> list[DesktopSessionEvent]:
    """Session events in the stretch, led by the goal that was set when it began."""
    start, end = window
    events = list(db.scalars(
        select(DesktopSessionEvent).where(DesktopSessionEvent.session_id == session_id,
                                          DesktopSessionEvent.occurred_at > start,
                                          DesktopSessionEvent.occurred_at <= end)
        .order_by(DesktopSessionEvent.id)))
    goal = db.scalar(
        select(DesktopSessionEvent).where(DesktopSessionEvent.session_id == session_id,
                                          DesktopSessionEvent.type == "desktop.plan_set",
                                          DesktopSessionEvent.occurred_at <= start)
        .order_by(DesktopSessionEvent.id.desc()).limit(1))
    return [goal, *events] if goal is not None else events


def _user_lines(db: Session, user_id: uuid.UUID, window: tuple[datetime, datetime]) -> list[tuple[datetime, str, str]]:
    """What the user said (typed or spoken) during the stretch. Message times
    are naive UTC in the database."""
    start, end = (moment.astimezone(timezone.utc).replace(tzinfo=None) for moment in window)
    rows = db.execute(
        select(ChatMessage.created_at, ChatMessage.id, ChatMessage.content)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .where(Conversation.user_id == user_id, ChatMessage.role == "user",
               ChatMessage.created_at > start, ChatMessage.created_at <= end)
        .order_by(ChatMessage.created_at)).all()
    return [(at.replace(tzinfo=timezone.utc), str(message_id), text) for at, message_id, text in rows if text.strip()]


def _finish(db: Session, job_id: uuid.UUID, claim: str, status: str, result: dict) -> bool:
    """Commit the work only if this worker still holds the lease."""
    done = db.execute(
        update(GameReflectionJob)
        .where(GameReflectionJob.id == job_id, GameReflectionJob.claim_token == claim)
        .values(status=status, result=result, claim_token=None, last_error=None, lease_expires_at=None,
                processed_at=_now())
    )
    if done.rowcount != 1:
        db.rollback()
        return False
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# The Learning page
# --------------------------------------------------------------------------- #


class LearningError(Exception):
    def __init__(self, code: str, status_code: int, message: str):
        super().__init__(message)
        self.code, self.status_code, self.message = code, status_code, message


def list_lessons(db: Session, user_id: uuid.UUID, game: str | None = None) -> list[GameLesson]:
    query = select(GameLesson).where(GameLesson.user_id == user_id)
    if game:
        query = query.where(GameLesson.game == game)
    return list(db.scalars(query.order_by(
        case((GameLesson.status == "confirmed", 0), (GameLesson.status == "trial", 1), else_=2),
        GameLesson.updated_at.desc())))


def _owned(db: Session, model, user_id: uuid.UUID, item_id: uuid.UUID):
    item = db.scalar(select(model).where(model.id == item_id, model.user_id == user_id).with_for_update())
    if item is None:
        raise LearningError("not_found", 404, "not found")
    return item


def set_lesson_status(db: Session, user_id: uuid.UUID, lesson_id: uuid.UUID, status: str) -> GameLesson:
    """The user retires a lesson (out of her prompt, never re-proposed) or
    restores one to trial."""
    lesson = _owned(db, GameLesson, user_id, lesson_id)
    if lesson.status != status:
        lesson.status = status
        lesson.revision += 1
        lesson.evidence = [*lesson.evidence, {"verdict": f"user_{status}", "at": _now().isoformat()}]
    db.commit()
    return lesson


def delete_lesson(db: Session, user_id: uuid.UUID, lesson_id: uuid.UUID) -> None:
    db.delete(_owned(db, GameLesson, user_id, lesson_id))
    db.commit()


def list_proposals(db: Session, user_id: uuid.UUID, status: str | None = None) -> list[GameProposal]:
    query = select(GameProposal).where(GameProposal.user_id == user_id)
    if status:
        query = query.where(GameProposal.status == status)
    return list(db.scalars(query.order_by(
        case((GameProposal.status == "open", 0), (GameProposal.status == "accepted", 1), else_=2),
        GameProposal.occurrences.desc(), GameProposal.updated_at.desc())))


def update_proposal(db: Session, user_id: uuid.UUID, proposal_id: uuid.UUID, *,
                    status: str | None, user_note: str | None) -> GameProposal:
    proposal = _owned(db, GameProposal, user_id, proposal_id)
    if status is not None:
        proposal.status = status
    if user_note is not None:
        proposal.user_note = user_note.strip() or None
    db.commit()
    return proposal


def recent_reflections(db: Session, user_id: uuid.UUID, limit: int = 20) -> list[GameReflectionJob]:
    return list(db.scalars(
        select(GameReflectionJob).where(GameReflectionJob.user_id == user_id)
        .order_by(GameReflectionJob.created_at.desc()).limit(limit)))


# --------------------------------------------------------------------------- #
# Measuring learning (scripts/game_learning_report.py)
# --------------------------------------------------------------------------- #


def session_stats(db: Session, session: DesktopSession) -> dict:
    """Counted facts about one session, for comparing play with and without
    lessons. A stopped skill (cut short by a plan change or danger) counts
    neither way; a refused one is a failure (she chose something impossible)."""
    experiences = list(db.scalars(
        select(GameExperience).where(GameExperience.session_id == session.id).order_by(GameExperience.id)))
    events = list(db.scalars(select(DesktopSessionEvent.type).where(DesktopSessionEvent.session_id == session.id)))
    outcomes = [e.payload for e in experiences if e.kind == "skill_outcome"]
    judged = [o for o in outcomes if o.get("status") != "stopped"]
    done = sum(1 for o in judged if o.get("status") == "done")
    repeats, previous = 0, None
    for o in outcomes:
        key = (o.get("skill"), str(o.get("params")), o.get("status"), o.get("reason"))
        if o.get("status") != "done" and key == previous:
            repeats += 1
        previous = key
    applied = [e.payload for e in experiences if e.kind == "lessons_applied"]
    minutes = ((session.ended_at or _now()) - session.started_at).total_seconds() / 60
    return {
        "session_id": str(session.id), "started_at": session.started_at, "minutes": round(minutes, 1),
        "lessons_on": any(a.get("enabled") for a in applied) if applied else None,
        "lessons": max((len(a.get("keys", [])) for a in applied), default=0),
        "outcomes": len(outcomes), "success_rate": round(done / len(judged), 3) if judged else None,
        "repeated_failures": repeats,
        "deaths": sum(1 for e in experiences if e.kind == "notice" and e.payload.get("kind") == "died"),
        "steps_done": events.count("desktop.step_done"), "goals": events.count("desktop.plan_set"),
        "stuck": events.count("desktop.stuck"),
    }
