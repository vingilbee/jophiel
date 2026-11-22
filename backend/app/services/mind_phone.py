"""Her phone as a source of her background mind (docs/MIND_ARCHITECTURE.md, "Her phone").

What the user's phone reports about itself (services/phone_state.py) and whether the app is open (presence) tick her
background mind (services/mind.py) the way a game does: one long-lived activity per user ("being with them"), a
tick as soon as something the phone reports changes and a heartbeat every PHONE_TICK_SECONDS otherwise (time alone
moves some of it: the part of the day, how long since either of them wrote). The gate (a model) reads each tick
with news; a quick thought may follow, and a line it proposes wakes her mind, which decides whether it's said.

What counts as news is a change of what a person would notice, never a number moving: the app opened or closed, a
call starting, the phone unlocked, plugged in or unplugged, the battery entering a lower band, headphones, the
network, Do Not Disturb, moving, a named place, the part of the day, a longer quiet stretch. A first tick (a worker
restart, a phone seen again) is the baseline, not news. No pressure is ever "urgent" here and nothing is a problem
for hard thinking: code reports facts; whether any of it matters is her mind's.

Phase 8: what's playing starting or stopping, a Maps trip starting or ending, the Messages switch, and the phone's
notifications (services/phone_notifications.py: a new message, a removal), always as someone else's words. A moment
with a posted notification is ``third_party``: the wake it leads to gets no device action (app/ai/chat.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.mind.contracts import Moment, Rhythm, Scope
from app.ai.mind.hard import HardInput, Problem
from app.ai.mind.situation import Situation
from app.core.config import get_settings
from app.db.models import ChatMessage, Conversation, WorkingItem
from app.services import phone_actions, phone_notifications, phone_state

NAME = "phone"
PHONE = Situation(
    NAME, "with {user}, through the phone they carry; you don't see them, you know what their phone tells you",
    examples={
        "none": "nothing new, a number moving (battery down a percent), the same place and the same quiet",
        "react": "something on the phone that needs them right now and won't wait (it's about to die mid-call)",
        "light": "they opened the app or picked up the phone, came home or left, plugged in late at night, put "
                 "headphones on, started moving, a long quiet stretch ended or began, the evening started, a message or "
                 "notification arrived (above all one you're waiting for)",
        "hard": "something in what the phone says doesn't add up with what they told you",
    },
)
QUIET_BANDS = ((12 * 3600, "12 h"), (6 * 3600, "6 h"), (3 * 3600, "3 h"), (3600, "1 h"))
TALK_LINES = 4


def activity_of(user_id: uuid.UUID) -> uuid.UUID:
    """Her one activity with this user's phone: stable, so her thoughts about it stay one thread."""
    return uuid.uuid5(user_id, "jophiel:phone")


def scope_of(user_id: uuid.UUID) -> Scope:
    return Scope(user_id, NAME, activity_of(user_id))


def part_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def quiet_band(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return next((label for limit, label in QUIET_BANDS if seconds >= limit), None)


def snapshot(state: phone_state.PhoneState) -> dict[str, Any]:
    """What the phone says, as news reads it (bands, not numbers): two equal snapshots mean nothing to notice."""
    return {
        "app": state.app, "voice": state.voice, "screen": state.get("screen"), "locked": state.get("locked"),
        "battery": phone_state.battery_band(state.get("battery"), state.get("charging")),
        "charging": state.get("charging"), "headphones": state.get("headphones"),
        "network": (state.get("network"), state.get("wifi_name")), "dnd": state.get("dnd"),
        "ringer": state.get("ringer"), "motion": state.get("motion"), "place": state.get("place"),
        # What's playing (the app and playing / paused, not each track) and whether a Maps trip is under way (not
        # each step): what a person notices; the track and the step are read in "THEIR PHONE".
        "media": _media(state.get("media")), "navigating": isinstance(state.get("navigation"), dict) or None,
        "messages_on": state.get("messages_on"),
    }


def _media(value) -> tuple | None:
    return (value.get("app"), value.get("state")) if isinstance(value, dict) else None


def _network(value) -> str:
    network, name = value if isinstance(value, (tuple, list)) else (value, None)
    return {"wifi": f'Wi-Fi "{name}"' if name else "Wi-Fi", "cellular": "mobile data",
            "none": "no network"}.get(network, str(network))


def news(now: dict[str, Any], before: dict[str, Any] | None, state: phone_state.PhoneState) -> list[str]:
    """What changed since the last tick, a line each. A first tick (``before`` None) is the baseline: no news."""
    if before is None:
        return []
    out = []

    def moved(key: str) -> bool:
        return key in now and now.get(key) != before.get(key) and now.get(key) is not None

    if moved("app"):
        out.append({"open": "They opened the Jophiel app", "background": "They left the app (still connected)",
                    "closed": "The app was closed"}[now["app"]])
    if now.get("voice") != before.get("voice") and before.get("voice") is not None:
        out.append("A voice call with you started" if now["voice"] else "The voice call ended")
    if moved("locked"):
        out.append("They unlocked their phone" if not now["locked"] else "Their phone locked")
    elif moved("screen"):
        out.append(f"Their phone's screen turned {now['screen']}")
    if moved("charging"):
        level = state.get("battery")
        at = f" (battery {level}%)" if level is not None else ""
        out.append(("Plugged in to charge" if now["charging"] else "Unplugged") + at)
    if moved("battery") and not moved("charging"):
        out.append(f"Battery {now['battery']}: {state.get('battery')}%")
    if moved("headphones"):
        out.append("Headphones connected" if now["headphones"] else "Headphones disconnected")
    if now.get("network") != before.get("network") and (now.get("network") or (None,))[0] is not None:
        out.append(f"Now on {_network(now['network'])}")
    if moved("dnd"):
        out.append("Do Not Disturb turned " + ("on" if now["dnd"] else "off"))
    if moved("ringer"):
        out.append(f"Ringer set to {now['ringer']}")
    if now.get("motion") != before.get("motion") and now.get("motion") is not None:
        out.append({"still": "Stopped moving", "vehicle": "Now in a vehicle"}.get(now["motion"],
                                                                                  f"Started {now['motion']}"))
    # ``place``: a named place, AWAY (the phone confirmed they left every named place), or None (unknown: location
    # off or unavailable). Losing sight of where they are is never news: only a confirmed departure is "Left".
    if now.get("place") != before.get("place") and now.get("place") is not None:
        place, was = now["place"], before.get("place")
        if place != phone_state.AWAY:
            out.append(f"Arrived at {place}")
        elif was is not None and was != phone_state.AWAY:
            out.append(f"Left {was}")
        else:
            out.append("Not at any of their named places")
    if now.get("media") != before.get("media") and "media" in now:
        media = state.get("media")
        if isinstance(media, dict):
            out.append(f"Media: {phone_state.now_playing(media)}")
        elif before.get("media") is not None:
            out.append("Nothing is playing on the phone now")
    if now.get("navigating") != before.get("navigating") and "navigating" in now:
        if now.get("navigating"):
            out.append(f"A Maps trip started: {phone_state.trip(state.get('navigation'))}")
        elif before.get("navigating"):
            out.append("The Maps trip ended")
    if moved("messages_on"):
        out.append("They turned their Messages switch " + ("back on: replies and drafts to other people are allowed"
                                                           if now["messages_on"] else
                                                           "off: your replies and drafts to other people are paused"))
    if moved("part_of_day"):
        out.append(f"It's {now['part_of_day']} now")
    if moved("quiet"):
        out.append(f"No message from them for {now['quiet']} now")
    return out


PROMISES_SHOWN = 6


def open_promises(db: Session | None, user_id: uuid.UUID) -> list[str]:
    """Her promises still open and her own intentions, newest first: what she's waiting for. Read into a moment
    that brings a notification, so "tell me when Rina writes" is with her when Rina does."""
    if db is None:
        return []
    rows = db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id, WorkingItem.closed_at.is_(None),
        ((WorkingItem.kind == "commitment") & (WorkingItem.owner == "columbina")) | (WorkingItem.kind == "intention"))
        .order_by(WorkingItem.created_at.desc()).limit(PROMISES_SHOWN))
    return [("- You promised: " if row.kind == "commitment" else "- You meant to: ") + " ".join(row.text.split())[:200]
            for row in rows]


def _last_message_at(db: Session | None, user_id: uuid.UUID, role: str) -> float | None:
    if db is None:
        return None
    at = db.scalar(select(func.max(ChatMessage.created_at)).join(
        Conversation, Conversation.id == ChatMessage.conversation_id).where(
        Conversation.user_id == user_id, ChatMessage.role == role, ChatMessage.content != ""))
    if at is None:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).timestamp()


def _zone(state: phone_state.PhoneState):
    """Their time zone as the phone app reported it, or None (then UTC, said as such)."""
    from app.ai.mind import when as W

    return W.zone_of(state.zone) if state.zone else None


class PhoneSource:
    """Her phone source: polled, no body, no hard problems of its own (handoffs from her mind still reach it)."""

    name = NAME
    rhythm = Rhythm("poll", 1.0)
    shadow = False
    # A tick with nothing new isn't read by the gate (a phone sits unchanged for hours: no model call for that).
    gate_needs_news = True

    def __init__(self) -> None:
        self._seen: dict[uuid.UUID, tuple[dict, float]] = {}  # per user: the last ticked snapshot, when
        self._live: set[uuid.UUID] = set()  # every user with a phone, ticked this loop or not

    def enabled(self) -> bool:
        return True

    def poll(self, db: Session | None) -> list[tuple[Scope, Any]] | None:
        """Users whose phone is due a tick after a state change, a late action outcome, or its heartbeat."""
        import time

        now = time.time()
        every = get_settings().phone_tick_seconds
        out = []
        live = set()
        action_news = phone_actions.pending_news(db) if db is not None else {}
        for user_id in set(phone_state.users(now=now)) | action_news.keys():
            live.add(user_id)
            state = phone_state.read(user_id, now=now)
            updates = action_news.get(user_id, [])
            notes = phone_notifications.pending_news(user_id)
            if state is None and not updates and not notes:
                continue
            state = state or phone_state.PhoneState()
            seen = self._seen.get(user_id)
            current = snapshot(state)
            if not updates and not notes and seen is not None and seen[0] == current and now - seen[1] < every:
                continue
            self._seen[user_id] = (current, now)
            out.append((scope_of(user_id), (state, updates, notes) if updates or notes else state))
        for user_id in list(self._seen):
            if user_id not in live:
                self._seen.pop(user_id, None)
        self._live = {activity_of(user_id) for user_id in live}
        return out

    def live_activities(self) -> set[uuid.UUID]:
        """What's still live after the last poll, due a tick or not: a phone between ticks keeps its mind (what
        the next tick compares against)."""
        return set(self._live)

    def moment(self, db: Session | None, scope: Scope, payload: Any, previous: Any, at: float, *,
               thoughts: list[dict], thinking: str | None) -> Moment:
        from app.services import mind_ticks

        state, action_news, notes = (payload + ([],))[:3] if isinstance(payload, tuple) else (payload, [], [])
        zone = _zone(state)
        local = datetime.fromtimestamp(at, zone or timezone.utc)
        theirs = _last_message_at(db, scope.user_id, "user")
        hers = _last_message_at(db, scope.user_id, "assistant")
        current = {**snapshot(state), "part_of_day": part_of_day(local.hour),
                   "quiet": quiet_band(at - theirs if theirs is not None else None)}
        fresh = [*(text for _, text in action_news), *news(current, previous, state),
                 *phone_notifications.news_lines(notes)]
        talk = mind_ticks.recent_talk(db, scope.user_id, datetime.fromtimestamp(at, timezone.utc)) if db else []

        text = [f"TIME\n- {local:%H:%M} on {local:%A %d %B}, {current['part_of_day']}"
                + ("" if zone else " (UTC: their time zone isn't known)") + "."]
        said = []
        if theirs is not None:
            said.append(f"their last message {phone_state.span(at - theirs)} ago")
        if hers is not None:
            said.append(f"yours {phone_state.span(at - hers)} ago")
        if said:
            text[0] += f"\n- In chat: {', '.join(said)}."
        text.append("THEIR PHONE\n" + "\n".join(phone_state.describe(state, at)))
        # What else is on the phone: who and where only (the words of a new one are under NEW, labelled).
        shown = phone_notifications.section(scope.user_id, with_text=False, now=at)
        if shown:
            text.append("\n".join(shown))
        if fresh:
            text.append("NEW SINCE THE LAST MOMENT\n" + "\n".join(f"- {line}" for line in fresh))
        else:
            text.append("NEW SINCE THE LAST MOMENT\n- nothing")
        if notes:
            promises = open_promises(db, scope.user_id)
            if promises:
                text.append("WHAT YOU'RE WAITING FOR (your open promises and intentions)\n" + "\n".join(promises))
        if talk:
            text.append("RECENT TALK\n" + "\n".join(
                f"- {'They' if m['role'] == 'user' else 'You'}: {' '.join(m['content'].split())[:200]}"
                for m in talk[-TALK_LINES:]))
        if thoughts:
            text.append("YOUR RECENT THOUGHTS ABOUT THIS\n" + "\n".join(
                f"- {t['text']}" + (f' (you said: "{t["said"]}")' if t.get("said") else "") for t in thoughts))
        if thinking:
            text.append(f"YOU'RE ALREADY THINKING ABOUT\n- {thinking}")
        changed = [k for k in current if previous is not None and current.get(k) != previous.get(k)]
        return Moment("\n\n".join(text), "calm", fresh, changed, "; ".join(fresh)[:200], can_act=False,
                      talk=talk, capture=lambda: {**snapshot(state), "_user": scope.user_id},
                      data={"snapshot": current, "action_news": [activity_id for activity_id, _ in action_news],
                            "notification_news": len(notes),
                            # Someone else's words are in this moment: what it leads to is theirs, not the user's
                            # (no device action, however far it's carried: app/ai/chat.py).
                            "third_party": phone_notifications.has_third_party(notes),
                            # Which notifications just arrived (handles only): the wake this leads to names them.
                            "arrived": phone_notifications.arrived(notes)})

    def previous(self, moment: Moment) -> Any:
        return moment.data["snapshot"]

    def after_tick(self, scope: Scope, moment: Moment, number: int, decision: dict) -> None:
        phone_actions.acknowledge_news(moment.data["action_news"])
        phone_notifications.acknowledge_news(scope.user_id, moment.data.get("notification_news", 0))

    def gate_situation(self) -> Situation:
        return PHONE

    def situation(self) -> Situation:
        return PHONE

    def say_ttl(self) -> float:
        return get_settings().phone_say_ttl_seconds

    def worse(self, danger: Any, before: Any) -> bool:
        return False

    def live(self, db: Session | None, scope: Scope) -> bool:
        return scope.user_id in phone_state.users()

    def changed_since(self, db: Session | None, token: Any) -> list[str]:
        """What the phone says now that it didn't when a thought began: a thought about an older moment isn't
        said (they plugged in again, came back)."""
        if not isinstance(token, dict):
            return []
        user_id = token.get("_user")
        state = phone_state.read(user_id) if user_id else None
        if state is None:
            return []
        now = snapshot(state)
        return [f"{key}: {token.get(key)} -> {now.get(key)}" for key in now if now.get(key) != token.get(key)]

    # Hard thinking: nothing of its own (a hand-off from her mind still lands; mind_hard.handoff).
    def new_state(self) -> None:
        return None

    def problems(self, scope: Scope, state: Any, moment: Moment, *, gate_hard: bool) -> list[Problem]:
        return []

    def drop_waiting(self, scope: Scope, moment: Moment, problem: Problem) -> str | None:
        return None

    def thinks_when_quiet(self) -> bool:
        return False  # a phone sits quiet most of the day: quiet time isn't a reason to think

    def gather(self, db: Session, scope: Scope, problem: Problem, base: HardInput) -> tuple[HardInput | None, Any]:
        from dataclasses import replace
        import time

        state = phone_state.read(scope.user_id)
        lines = phone_state.describe(state, time.time())
        return replace(base, now="\n".join(lines) if lines else base.now, goal=None), None

    def dropped(self, db: Session, scope: Scope, problem: Problem, cancelled: str | None) -> str | None:
        return cancelled

    def outdated(self, db: Session, scope: Scope, problem: Problem, token: Any) -> list[str]:
        return []

    def current_goal(self, db: Session, scope: Scope) -> str | None:
        return None

    def speaking(self, db: Session, scope: Scope) -> dict:
        return {"commentary_mode": "occasional"}

    def body(self, scope: Scope) -> None:
        return None

    def thought_session(self, scope: Scope) -> None:
        return None  # mind_thoughts.session_id is a game session; phone thoughts carry source + activity instead

    def handoff_scope(self, db: Session, user_id: uuid.UUID) -> None:
        return None


SOURCE = PhoneSource()
