"""Her practice notes (app/ai/practice.py, services/practice.py): the trail her phone tools leave, a settled stretch
taken whole for one review, what a review may change, and what she reads with the screen. The reviewer is faked."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid

import pytest

from app.ai import practice as P
from app.ai.tools import phone_control as control_tools
from app.ai.tools import phone_grounding
from app.services import phone_actions, phone_control, practice
from test_phone_phase9 import screen, tools_by_name
from db_support import database, db_env  # noqa: F401


class FakeRedis:
    """Enough of Redis for the trail: hashes, sets, lists, one sorted set, rename."""

    def __init__(self):
        self.data: dict[str, object] = {}

    def pipeline(self):
        return self

    def execute(self):
        return []

    def _b(self, v):
        return v if isinstance(v, bytes) else str(v).encode()

    def hsetnx(self, k, f, v):
        h = self.data.setdefault(k, {})
        if self._b(f) not in h:
            h[self._b(f)] = self._b(v)

    def hset(self, k, f, v):
        self.data.setdefault(k, {})[self._b(f)] = self._b(v)

    def hincrby(self, k, f, n):
        h = self.data.setdefault(k, {})
        h[self._b(f)] = self._b(int(h.get(self._b(f), b"0")) + n)
        return int(h[self._b(f)])

    def hdel(self, k, f):
        self.data.get(k, {}).pop(self._b(f), None)

    def hgetall(self, k):
        return dict(self.data.get(k, {}))

    def sadd(self, k, v):
        self.data.setdefault(k, set()).add(self._b(v))

    def smembers(self, k):
        return set(self.data.get(k, set()))

    def rpush(self, k, v):
        self.data.setdefault(k, []).append(self._b(v))

    def lrange(self, k, a, b):
        return list(self.data.get(k, []))

    def expire(self, k, s):
        pass

    def zadd(self, k, mapping):
        self.data.setdefault(k, {}).update({self._b(m): s for m, s in mapping.items()})

    def zrangebyscore(self, k, lo, hi, start=0, num=None):
        return [m for m, s in sorted(self.data.get(k, {}).items(), key=lambda x: x[1]) if lo <= s <= hi][:num]

    def zrem(self, k, m):
        return 1 if self.data.get(k, {}).pop(self._b(m), None) is not None else 0

    def rename(self, src, dst):
        import redis
        if src not in self.data:
            raise redis.ResponseError("no such key")
        self.data[dst] = self.data.pop(src)

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


def _jpeg(width=400, height=200):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (width, height), (30, 120, 40)).save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode()


def _stretch(**over) -> P.Stretch:
    values = dict(surface="phone", asked=["attack this base"], replies=[], steps=[
        P.Step(at=1.0, turn="t1", tool="phone_tap_on", did="tapped point (10, 20)", said="the Grand Warden",
               place="Clash of Clans", result="Phone screen s2: Clash of Clans in front")],
        notes=[P.Note(id="n1", place="Clash of Clans", tool=None, status="to_try", text="try by look"),
               P.Note(id="u1", place="Clash of Clans", tool=None, status="known", text="Warden last",
                      source="user")])
    values.update(over)
    return P.Stretch(**values)


# --------------------------------------------------------------------------- #
# What a review may change
# --------------------------------------------------------------------------- #


def test_check_keeps_what_the_stretch_carries_and_says_why_the_rest_went():
    review = P.Review(summary="s", changes=[
        P.NoteChange(action="create", place="clash of clans", tool="phone_tap_on", text="By look works."),
        P.NoteChange(action="rewrite", note="n1", place="x", status="known", text="Tried: by look hit."),
        P.NoteChange(action="rewrite", note="u1", place="Clash of Clans", text="mine now"),
        P.NoteChange(action="remove", note="nope", place="Clash of Clans"),
        P.NoteChange(action="create", place="Spotify", text="unrelated"),
        P.NoteChange(action="create", place="Clash of Clans", tool="made_up", text="x" * (P.NOTE_CHARS + 1)),
        P.NoteChange(action="create", place="Clash of Clans", tool="made_up", text="  "),
    ])
    kept, dropped = P.check(review, _stretch(), tools={"phone_tap_on"})
    assert [(c.action, c.place, c.tool) for c in kept] == [("create", "Clash of Clans", "phone_tap_on"),
                                                           ("rewrite", "Clash of Clans", None)]
    assert kept[1].note == "n1" and kept[1].status == "known"
    assert len(dropped) == 5
    assert any("the user's note" in d for d in dropped) and any("isn't in this stretch" in d for d in dropped)


def test_the_stretch_reads_as_steps_with_her_words_and_her_notes():
    text = P.render_stretch(_stretch(pictures=["where step 1 aimed"]))
    assert 'you said "the Grand Warden"' in text
    assert "picture 0: where step 1 aimed" in text
    assert "- n1 [Clash of Clans] (to try) try by look" in text
    assert "(the user's) Warden last" in text


def test_notes_read_with_the_screen_say_whose_they_are():
    out = P.notes_text("Clash of Clans", _stretch().notes)
    assert out.startswith("Your notes from using Clash of Clans")
    assert "- (to try) try by look" in out and "- (the user's) Warden last" in out


# --------------------------------------------------------------------------- #
# The trail her phone tools leave
# --------------------------------------------------------------------------- #


@pytest.fixture
def phone(monkeypatch):
    sent, recorded, pictures, notes_asked = [], [], [], []
    picture = _jpeg()

    def act(user_id, conversation_id, action, args, **kwargs):
        sent.append((action, args))
        shown = {**screen("s1", app="Clash of Clans"), "image": {"jpeg_base64": picture, "width": 400, "height": 200},
                 "items": [{"n": 1, "role": "button", "label": "Attack!"}]}
        return phone_actions.Outcome("done", uuid.uuid4(), "Done", screen=shown)

    monkeypatch.setattr(phone_actions, "act", act)
    monkeypatch.setattr(phone_control, "begin_turn", lambda *a, **k: None)
    monkeypatch.setattr(phone_control, "remember", lambda *a, **k: None)
    monkeypatch.setattr(practice, "record", lambda user_id, step, **k: recorded.append(step))
    monkeypatch.setattr(practice, "picture", lambda user_id, jpeg, **k: pictures.append(k) or len(pictures) - 1)

    def for_place(user_id, place, **k):
        notes_asked.append(place)
        return [P.Note(id="n1", place=place, tool=None, status="known", text="Drop the Warden last.")]

    monkeypatch.setattr(practice, "for_place", for_place)
    tools, control = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    return type("Phone", (), {"tools": tools_by_name(tools), "control": control, "sent": sent, "recorded": recorded,
                              "pictures": pictures, "notes_asked": notes_asked})


def test_every_step_is_recorded_and_aimed_steps_keep_the_picture_with_her_marks(phone):
    call = lambda name, **a: asyncio.run(phone.tools[name].ainvoke(a))  # noqa: E731
    first = call("read_phone_screen")
    call("phone_tap", n=1)
    call("phone_tap_points", points=[[10, 20], [30, 40]])
    again = call("phone_tap_point", x=5, y=6)
    assert [s.tool for s in phone.recorded] == ["read_phone_screen", "phone_tap", "phone_tap_points",
                                                "phone_tap_point"]
    assert phone.recorded[1].item == '[1] button "Attack!"'
    assert phone.recorded[2].picture == 0 and phone.pictures[0]["marks"] == [{"tap": [10, 20]}, {"tap": [30, 40]}]
    assert phone.recorded[0].picture is None and phone.recorded[0].screen.startswith("Keyboard down")
    assert all(s.place == "Clash of Clans" for s in phone.recorded)
    # Her notes come with the first screen of a place in the turn, once.
    assert "Your notes from using Clash of Clans" in first and "Drop the Warden last." in first
    assert "Your notes from using" not in again and phone.notes_asked == ["Clash of Clans"]


def test_tap_on_records_what_she_said_to_the_pointing_model(phone, monkeypatch):
    monkeypatch.setattr(phone_grounding, "available", lambda: True)
    monkeypatch.setattr(phone_grounding, "_trace", lambda *a, **k: None)

    async def ask(picture_b64, target):
        return "Action: click(start_box='(100,50)')"

    monkeypatch.setattr(phone_grounding, "ask", ask)
    tools, _ = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    asyncio.run(named["phone_tap_on"].ainvoke({"targets": ["old man with white beard, labelled 60"]}))
    step = phone.recorded[-1]
    assert step.tool == "phone_tap_on" and step.said == "old man with white beard, labelled 60"
    assert step.picture is not None and "tap" in phone.pictures[-1]["marks"][0]


def test_typed_text_never_reaches_the_trail(phone):
    asyncio.run(phone.tools["read_phone_screen"].ainvoke({}))
    asyncio.run(phone.tools["phone_type"].ainvoke({"n": 1, "text": "my secret password"}))
    assert "secret" not in json.dumps([s.__dict__ for s in phone.recorded])


# --------------------------------------------------------------------------- #
# A settled stretch, taken whole
# --------------------------------------------------------------------------- #


def test_a_stretch_waits_until_her_hands_are_still_then_is_taken_whole(monkeypatch):
    bus = FakeRedis()
    user = uuid.uuid4()
    for i, turn in enumerate(("t1", "t1", "t2")):  # the user's words cut it into two turns: one stretch
        practice.record(user, P.Step(at=100.0 + i, turn=turn, tool="phone_tap", did="tapped [1]"),
                        client=bus, now=100.0 + i, asked=uuid.uuid4(), conversation_id=uuid.UUID(int=1))
    practice.picture(user, _jpeg(), caption="aimed", marks=[{"tap": [1, 2]}], client=bus)
    seen = []
    monkeypatch.setattr(practice, "review", lambda u, s, taken, judge=None: seen.append((u, s, taken)) or {})
    monkeypatch.setattr(practice, "_trace", lambda record: None)
    settle = practice.get_settings().practice_settle_seconds
    assert practice.run_once(client=bus, now=102.0 + settle - 1) is False  # still moving
    assert practice.run_once(client=bus, now=102.0 + settle + 1) is True
    (u, surface, taken), = seen
    assert u == user and surface == "phone" and len(taken["steps"]) == 3 and len(taken["asked"]) == 3
    assert taken["convs"] == [str(uuid.UUID(int=1))] and list(taken["pics"]) == [0]
    assert not [k for k in bus.data if k.startswith("practice:") and k != "practice:due"]  # read, then deleted
    assert practice.run_once(client=bus, now=10_000.0) is False


def test_turning_control_off_wipes_the_trail():
    bus = FakeRedis()
    user = uuid.uuid4()
    practice.record(user, P.Step(at=1.0, turn="t", tool="phone_tap", did="tapped [1]"), client=bus, now=1.0)
    practice.wipe(user, client=bus)
    assert not [k for k, v in bus.data.items() if k.startswith("practice:") and v]


def test_pictures_drawn_with_marks_stay_pictures():
    out = practice._draw(_jpeg(2000, 1000), [{"tap": [100, 100]}, {"swipe": [10, 10, 500, 500]}])
    from PIL import Image
    image = Image.open(io.BytesIO(base64.b64decode(out)))
    assert max(image.size) == 2000  # under PICTURE_SIDE: kept at its own pixels, the ones she taps
    out = practice._draw(_jpeg(4000, 1800), [])
    assert max(Image.open(io.BytesIO(base64.b64decode(out))).size) == practice.PICTURE_SIDE


# --------------------------------------------------------------------------- #
# The review, against real PostgreSQL
# --------------------------------------------------------------------------- #


def test_a_review_writes_rewrites_and_leaves_the_users_notes_alone(db_env, monkeypatch, tmp_path):
    from app.db.models import ChatMessage, Conversation, PracticeNote, User
    with db_env.db() as db:
        user = User(email=f"{uuid.uuid4().hex}@x.test", hashed_password="x", name="T")
        db.add(user)
        db.flush()
        conv = Conversation(user_id=user.id)
        db.add(conv)
        db.flush()
        asked = ChatMessage(conversation_id=conv.id, role="user", content="attack this base")
        db.add(asked)
        tried = PracticeNote(user_id=user.id, surface="phone", place="Clash of Clans", status="to_try",
                             text="Try describing troops by look.")
        theirs = PracticeNote(user_id=user.id, surface="phone", place="Clash of Clans", source="user",
                              text="Warden last.")
        db.add_all([tried, theirs])
        db.commit()
        user_id, tried_id, theirs_id, conv_id, asked_id = user.id, tried.id, theirs.id, conv.id, asked.id
    now = time.time()
    taken = {"meta": {"since": str(now - 30), "last": str(now)}, "convs": [str(conv_id)], "asked": [str(asked_id)],
             "pics": {0: {"jpeg": _jpeg(), "caption": "where step 1 aimed", "marks": [{"tap": [5, 5]}]}},
             "steps": [P.Step(at=now - 30, turn="t", tool="phone_tap_on", did="tapped point (5, 5)",
                              said="the Grand Warden", place="Clash of Clans", result="Phone screen s2",
                              picture=0).__dict__]}
    seen = {}

    def judge(text, images):
        seen.update(text=text, images=images)
        return P.Review(summary="by name missed", changes=[
            P.NoteChange(action="rewrite", note=str(tried_id), place="Clash of Clans", status="known",
                         text="By look works; by game name it picked the wrong card.", evidence="step 1"),
            P.NoteChange(action="rewrite", note=str(theirs_id), place="Clash of Clans", text="changed"),
            P.NoteChange(action="create", place="Clash of Clans", tool="phone_tap_points",
                         status="to_try", text="Drop a whole group in one phone_tap_points."),
        ])

    record = practice.review(user_id, "phone", taken, judge=judge)
    assert '"attack this base"' in seen["text"] and len(seen["images"]) == 1
    assert record["outcome"] == "reviewed" and len(record["changes"]) == 2 and len(record["dropped"]) == 1
    with db_env.db() as db:
        rows = {r.text: r for r in practice.list_notes(db, user_id)}
    assert rows["By look works; by game name it picked the wrong card."].status == "known"
    assert rows["Warden last."].source == "user"
    assert rows["Drop a whole group in one phone_tap_points."].tool == "phone_tap_points"
    assert [n.text for n in practice.for_place(user_id, "clash of clans")][0] == "Warden last."  # theirs first


def test_her_notes_come_again_every_few_actions_in_a_long_stretch(phone, monkeypatch):
    had = []
    monkeypatch.setattr(practice, "shown", lambda user_id, ids, **k: had.append(ids))
    outs = [asyncio.run(phone.tools["read_phone_screen"].ainvoke({}))]
    for i in range(P.REMIND_EVERY + 1):
        outs.append(asyncio.run(phone.tools["phone_tap_point"].ainvoke({"x": i, "y": 1})))
    shown_at = [i for i, out in enumerate(outs) if "Your notes from using" in out]
    assert shown_at == [0, P.REMIND_EVERY] and had == [["n1"], ["n1"]]


def test_the_review_is_told_which_notes_she_had_and_where_she_only_passed_through():
    stretch = _stretch(steps=[
        P.Step(at=1.0, turn="t", tool="phone_press", did="pressed home", place="One UI Home"),
        P.Step(at=2.0, turn="t", tool="phone_tap_point", did="tapped point (1, 2)", place="Clash of Clans")],
        notes=[P.Note(id="n1", place="Clash of Clans", tool=None, status="known", text="bar is y 600-650", had=True)])
    text = P.render_stretch(stretch)
    assert "(known, you had it) bar is y 600-650" in text
    assert "Places in this stretch: Clash of Clans." in text
    kept, dropped = P.check(P.Review(summary="s", changes=[
        P.NoteChange(action="create", place="One UI Home", text="home")]), stretch, tools=set())
    assert not kept and "isn't in this stretch" in dropped[0]


def test_pictures_are_spread_over_the_whole_stretch():
    steps = [P.Step(at=float(i), turn="t", tool="phone_tap_point", did="t", picture=i) for i in range(40)]
    pics = {i: {"jpeg": "", "caption": str(i)} for i in range(40)}
    shown, chosen = practice._pictures(steps, pics, last_look=None)
    assert len(chosen) == P.PICTURES_REVIEWED and 0 in shown and 39 in shown


def test_each_tap_on_target_is_shown_with_where_it_landed(phone, monkeypatch):
    monkeypatch.setattr(phone_grounding, "available", lambda: True)
    monkeypatch.setattr(phone_grounding, "_trace", lambda *a, **k: None)
    answers = iter(["Action: click(start_box='(100,50)')", "Action: click(start_box='(200,60)')"])

    async def ask(picture_b64, target):
        return next(answers)

    monkeypatch.setattr(phone_grounding, "ask", ask)
    tools, _ = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    named = tools_by_name(tools)
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    asyncio.run(named["phone_tap_on"].ainvoke({"targets": ["the healer card", "grass at the bottom"]}))
    step = phone.recorded[-1]
    assert [a["said"] for a in step.aims] == ["the healer card", "grass at the bottom"]
    text = P.render_stretch(_stretch(steps=[step]))
    assert '1. "the healer card" → (' in text and '2. "grass at the bottom" → (' in text
