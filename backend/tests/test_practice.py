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


def test_a_new_note_about_the_place_starts_as_something_to_try():
    review = P.Review(summary="s", changes=[
        P.NoteChange(action="create", place="Clash of Clans", status="known", text="The purple tower hits hardest."),
        P.NoteChange(action="create", place="Clash of Clans", tool="phone_tap_on", status="known",
                     text="By look works."),
        P.NoteChange(action="rewrite", note="n1", place="Clash of Clans", status="known", text="Seen again: it does."),
    ])
    kept, _ = P.check(review, _stretch(), tools={"phone_tap_on"})
    assert [c.status for c in kept] == ["to_try", "known", "known"]  # a later stretch agreeing makes it known


def test_a_place_holds_at_most_its_share_of_her_notes():
    full = [P.Note(id=f"n{i}", place="Clash of Clans", tool=None, status="known", text=f"note {i}")
            for i in range(P.NOTES_PER_PLACE)]
    new = P.NoteChange(action="create", place="Clash of Clans", tool="phone_tap_on", text="One more.")
    kept, dropped = P.check(P.Review(summary="s", changes=[new]), _stretch(notes=full), tools={"phone_tap_on"})
    assert not kept and "rewrite or merge" in dropped[0]
    merged = P.Review(summary="s", changes=[P.NoteChange(action="remove", note="n0", place="Clash of Clans"), new])
    kept, _ = P.check(merged, _stretch(notes=full), tools={"phone_tap_on"})
    assert [c.action for c in kept] == ["remove", "create"]  # a remove in the same review makes room


def test_step_numbers_stay_out_of_the_note():
    review = P.Review(summary="s", changes=[
        P.NoteChange(action="create", place="Clash of Clans", tool="phone_tap_on",
                     text="Taps at x=270 were refused (steps 32, 77). Tap the far edge (step 4) instead.",
                     evidence="steps 32, 77"),
    ])
    kept, _ = P.check(review, _stretch(), tools={"phone_tap_on"})
    assert kept[0].text == "Taps at x=270 were refused. Tap the far edge instead."
    assert kept[0].evidence == "steps 32, 77"


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
    watch_asked: list[str] = []
    monkeypatch.setattr(practice, "watch_for", lambda user_id, place, **k: watch_asked.append(place) or [
        "Select one card, place it, then the next."])
    tools, control = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    return type("Phone", (), {"tools": tools_by_name(tools), "control": control, "sent": sent, "recorded": recorded,
                              "pictures": pictures, "notes_asked": notes_asked, "watch_asked": watch_asked})


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
                             tool="phone_tap_on", text="Try describing troops by look.")
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
        return P.Review(summary="by name missed", result="57%, victory",
                        tallies=[P.Tally(what="place a troop", tried=25, worked=9)], changes=[
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
    assert rows["By look works; by game name it picked the wrong card."].tool is None  # a rewrite can drop the tool
    assert rows["Warden last."].source == "user"
    assert rows["Drop a whole group in one phone_tap_points."].tool == "phone_tap_points"
    assert [n.text for n in practice.for_place(user_id, "clash of clans")][0] == "Warden last."  # theirs first

    # The next review of the place reads how this one went, and the rewritten note's earlier version.
    def judge_again(text, images):
        seen.update(again=text)
        return P.Review(summary="again", result="20%, defeat")

    record = practice.review(user_id, "phone", taken, judge=judge_again)
    assert record["past"] == 1 and record["result"] == "20%, defeat"
    assert "Your earlier stretches in these places (newest first):" in seen["again"]
    assert "by name missed" in seen["again"] and "57%" not in seen["again"]  # her play, not the score
    assert "place a troop: 9 of 25 worked" in seen["again"]
    assert "earlier version (" in seen["again"] and "Try describing troops by look." in seen["again"]
    with db_env.db() as db:
        from app.db.models import PracticeReview
        kept = db.query(PracticeReview).filter_by(user_id=user_id).order_by(PracticeReview.created_at).all()
        assert [r.result for r in kept] == ["57%, victory", "20%, defeat"]
        note = db.query(PracticeNote).filter_by(user_id=user_id, text=
                                                "By look works; by game name it picked the wrong card.").one()
        assert note.history[0]["text"] == "Try describing troops by look." and note.history[0]["status"] == "to_try"


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


def test_the_review_sees_advice_that_was_reworded_again_and_again():
    stretch = _stretch(steps=[P.Step(at=1.0, turn="t", tool="phone_tap_point", did="tapped", place="Clash of Clans")],
                       notes=[P.Note(id="n1", place="Clash of Clans", tool=None, status="known", text="red area",
                                     revision=6),
                              P.Note(id="n2", place="Clash of Clans", tool=None, status="known", text="bar", revision=2),
                              P.Note(id="n3", place="Clash of Clans", tool=None, status="known", text="theirs",
                                     source="user", revision=4)])
    text = P.render_stretch(stretch)
    assert "(known, rewritten 5 times) red area" in text
    assert "(known) bar" in text and "(the user's) theirs" in text
    assert "rewritten" not in P.notes_text("Clash of Clans", stretch.notes)  # she reads her notes, not their history


def test_the_review_reads_earlier_stretches_and_earlier_versions_of_her_notes():
    stretch = _stretch(steps=[P.Step(at=1.0, turn="t", tool="phone_tap_point", did="tapped", place="Clash of Clans")],
                       notes=[P.Note(id="n1", place="Clash of Clans", tool=None, status="known", text="bar y 590-700",
                                     history=[{"text": "bar y 475-570", "at": "2026-11-26 18:47"}]),
                              P.Note(id="n2", place="Clash of Clans", tool=None, status="known", text="theirs",
                                     source="user", history=[{"text": "old theirs", "at": "x"}])],
                       past=[P.Past(at="2026-11-26 21:38", places=["Clash of Clans"], summary="red area again",
                                    result="30%", tallies=[{"what": "place a troop", "tried": 25, "worked": 9}])]
                       + [P.Past(at=f"old {i}", places=["Clash of Clans"], summary="s") for i in range(10)])
    text = P.render_stretch(stretch)
    assert "- 2026-11-26 21:38 [Clash of Clans] red area again Counts: place a troop: 9 of 25 worked." in text
    assert "30%" not in text  # an earlier result isn't what the next review compares
    assert text.count("[Clash of Clans] s") == P.PAST_SHOWN - 1  # the newest few only
    assert "earlier version (2026-11-26 18:47): bar y 475-570" in text
    assert "old theirs" not in text  # the user's notes are theirs, not something to settle
    assert text.index("Your earlier stretches") < text.index("Your notes so far")
    fields = list(P.Review.model_fields)
    assert fields.index("looking_back") < fields.index("changes")  # it looks back before it changes anything


def test_pictures_come_with_the_screen_after_and_are_spread_over_the_whole_stretch():
    steps = [P.Step(at=float(i), turn="t", tool="phone_tap_point", did="t", picture=i) for i in range(0, 80, 2)]
    pics = {i: {"jpeg": "", "caption": str(i)} for i in range(80)}  # odd ones: what came after each tap
    shown, chosen = practice._pictures(steps, pics, last_look=79)
    assert len(chosen) <= P.PICTURES_REVIEWED and 79 in shown  # the last look too
    aimed = [k for k in shown if k % 2 == 0]
    assert aimed[0] == 0 and aimed[-1] >= 70  # early and late
    assert all(k + 1 in shown for k in aimed if k + 1 != 79)  # each aimed one with the screen after it
    assert chosen[shown[1]]["after"] == 0 and chosen[shown[0]]["after"] is None
    # Taps one after another: the screen after one is the next one's aim, so more of them fit.
    steps = [P.Step(at=float(i), turn="t", tool="phone_tap_point", did="t", picture=i) for i in range(40)]
    shown, chosen = practice._pictures(steps, {i: {"jpeg": "", "caption": str(i)} for i in range(40)}, None)
    assert P.PICTURES_REVIEWED - 1 <= len(chosen) <= P.PICTURES_REVIEWED and 0 in shown and 39 in shown


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


def test_the_reviewer_keeps_pixels_for_what_is_fixed_to_the_screen_only():
    prompt = P.system_prompt()
    assert "Controls fixed to the screen" in prompt and "never by pixels" in prompt
    assert "Deploy by tapping ground above y 560" not in prompt  # the old example aimed at the place by pixels


def test_reviewer_has_its_own_output_limit(monkeypatch):
    """A long stretch with pictures can spend the chat's limit on reasoning alone: the reviewer gets its own."""
    from app.ai import agent

    calls = []

    class Model:
        def with_structured_output(self, schema):
            return self

    monkeypatch.setattr(agent, "get_chat_model", lambda *a: calls.append(a) or Model())
    monkeypatch.setattr(agent, "invoke_private", lambda runnable, messages: "review")
    assert practice._judge("steps", []) == "review"
    settings = practice.get_settings()
    assert calls == [(settings.practice_review_model, settings.practice_review_reasoning_effort,
                      settings.practice_review_max_tokens)]
    assert settings.practice_review_max_tokens > settings.chat_max_tokens


def test_a_failed_review_keeps_why_without_the_reasoning_blob():
    exc = ValueError("no parsed field. content=[{'type': 'reasoning', 'encrypted_content': '" + "A" * 5000
                     + "'}] response_metadata={'status': 'incomplete', 'reason': 'max_output_tokens'}")
    text = practice._failure(exc)
    assert "AAAA" not in text
    assert "max_output_tokens" in text


def test_a_pinned_note_is_read_but_never_rewritten_or_removed():
    pinned = P.Note(id="p1", place="Clash of Clans", tool=None, status="known", text="the plan", pinned=True,
                    revision=4, history=[{"text": "old plan", "at": "x"}])
    stretch = _stretch(notes=[pinned])
    review = P.Review(summary="s", changes=[
        P.NoteChange(action="rewrite", note="p1", place="Clash of Clans", text="shorter plan"),
        P.NoteChange(action="remove", note="p1", place="Clash of Clans"),
    ])
    kept, dropped = P.check(review, stretch, tools={"phone_tap_on"})
    assert kept == [] and len(dropped) == 2 and all("pinned by the user" in d for d in dropped)
    text = P.render_stretch(stretch)
    assert "p1 [Clash of Clans] (known, pinned by the user: follow it, never rewrite or remove it) the plan" in text
    assert "old plan" not in text and "rewritten" not in text  # nothing of its versions to reword


def test_pinning_leaves_the_words_and_whose_it_is(db_env):
    from app.db.models import PracticeNote, User
    with db_env.db() as db:
        user = User(email=f"{uuid.uuid4().hex}@x.test", hashed_password="x", name="T")
        db.add(user)
        db.flush()
        row = PracticeNote(user_id=user.id, surface="phone", place="Clash of Clans", status="known",
                           source="practice", text="the plan")
        db.add(row)
        db.flush()
        practice.edit_note(db, user.id, row.id, pinned=True)
        assert row.pinned and row.source == "practice" and row.text == "the plan" and row.revision == 1
        assert [n.id for n in practice._rows(db, user.id, "phone", ["Clash of Clans"])] == [row.id]
        practice.edit_note(db, user.id, row.id, pinned=False)
        assert not row.pinned


# --------------------------------------------------------------------------- #
# What she watches for
# --------------------------------------------------------------------------- #


def test_what_she_watches_for_comes_with_every_screen_read_once_a_turn(phone):
    outs = [asyncio.run(phone.tools["read_phone_screen"].ainvoke({}))]
    outs += [asyncio.run(phone.tools["phone_tap_point"].ainvoke({"x": i, "y": 1})) for i in range(3)]
    assert all("Watching for in Clash of Clans" in out and "- Select one card, place it, then the next." in out
               for out in outs)
    assert sum("Your notes from using" in out for out in outs) == 1  # the notes stay every few actions
    assert phone.watch_asked == ["Clash of Clans"]


def test_a_review_watches_for_at_most_three_things_in_the_places_it_saw():
    stretch = _stretch(steps=[P.Step(at=1.0, turn="t", tool="phone_tap_point", did="tapped", place="Clash of Clans")],
                       watch={"Clash of Clans": ["Old line."]})
    assert "What you watch for now (you saw it with every screen there):\n- [Clash of Clans] Old line." in \
        P.render_stretch(stretch)
    fields = list(P.Review.model_fields)
    assert fields.index("looking_back") < fields.index("watch") < fields.index("changes")
    review = P.Review(summary="s", watch=[P.Watch(place="clash of clans", text="One card at a time (steps 3-4)."),
                                          P.Watch(place="Clash of Clans", text="One card at a time."),
                                          P.Watch(place="Clash of Clans", text="x" * 400),
                                          P.Watch(place="Clash of Clans", text="third"),
                                          P.Watch(place="Clash of Clans", text="fourth"),
                                          P.Watch(place="Hay Day", text="not here")])
    watch = P.check_watch(review, stretch)
    assert list(watch) == ["Clash of Clans"]
    assert watch["Clash of Clans"][0] == "One card at a time."  # step numbers out, the repeat once
    assert watch["Clash of Clans"][2] == "third" and len(watch["Clash of Clans"]) == P.WATCH_PER_PLACE == 3
    assert len(watch["Clash of Clans"][1]) == P.WATCH_CHARS  # the old line gave up its slot to newer ones
    assert P.check_watch(P.Review(summary="s"), stretch) == {"Clash of Clans": ["Old line."]}  # a line stays


def test_a_watch_line_leaves_only_when_shown_wrong_or_its_slot_is_needed():
    stretch = _stretch(steps=[P.Step(at=1.0, turn="t", tool="phone_tap_point", did="tapped", place="Clash of Clans")],
                       watch={"Clash of Clans": ["Cards one at a time.", "Away from the red area."]})
    wrong = P.Review(summary="s", unwatch=[P.Unwatch(place="Clash of Clans", text="Away from the red area.",
                                                     why="the warning came from the card bar, not the ground")])
    assert P.check_watch(wrong, stretch) == {"Clash of Clans": ["Cards one at a time."]}
    new = P.Review(summary="s", watch=[P.Watch(place="Clash of Clans", text="Ability by tapping its card.")])
    assert P.check_watch(new, stretch) == {"Clash of Clans": [
        "Ability by tapping its card.", "Cards one at a time.", "Away from the red area."]}
    full = P.Review(summary="s", watch=[P.Watch(place="Clash of Clans", text=t) for t in ("a", "b", "c")])
    assert P.check_watch(full, stretch) == {"Clash of Clans": ["a", "b", "c"]}  # a newer mistake took the slots


def test_the_newest_review_of_a_place_says_what_she_watches_for_there(db_env):
    from app.db.models import PracticeReview, User
    with db_env.db() as db:
        user = User(email=f"{uuid.uuid4().hex}@x.test", hashed_password="x", name="T")
        db.add(user)
        db.flush()
        user_id = user.id
        db.commit()
    now = time.time()
    taken = {"meta": {"since": str(now - 30), "last": str(now)}, "steps": [
        P.Step(at=now, turn="t", tool="phone_tap_point", did="tapped", place="Clash of Clans").__dict__]}
    seen = []

    def judge(text, images):
        seen.append(text)
        return P.Review(summary="s", watch=[P.Watch(place="Clash of Clans", text="One card, then place it.")])

    practice.review(user_id, "phone", taken, judge=judge)
    assert practice.watch_for(user_id, "Clash of Clans") == ["One card, then place it."]
    with db_env.db() as db:  # a review of somewhere else leaves it as it was
        db.add(PracticeReview(user_id=user_id, surface="phone", places=["Hay Day"], summary="farm", watch=[]))
        db.commit()
    assert practice.watch_for(user_id, "Clash of Clans") == ["One card, then place it."]
    record = practice.review(user_id, "phone", taken, judge=lambda text, images: seen.append(text) or P.Review(
        summary="better"))
    assert "- [Clash of Clans] One card, then place it." in seen[-1]  # the next review sees what she watched for
    assert record["watch"] == {"Clash of Clans": ["One card, then place it."]}  # a clean stretch keeps it
    record = practice.review(user_id, "phone", taken, judge=lambda text, images: P.Review(summary="s", unwatch=[
        P.Unwatch(place="Clash of Clans", text="One card, then place it.", why="shown wrong")]))
    assert record["watch"] == {"Clash of Clans": []} and practice.watch_for(user_id, "Clash of Clans") == []


def test_the_review_counts_only_her_own_notes_against_the_cap():
    stretch = _stretch(notes=[P.Note(id=f"u{i}", place="Clash of Clans", tool=None, status="known", text=f"theirs {i}",
                                     source="user", pinned=True) for i in range(5)]
                       + [P.Note(id="n1", place="Clash of Clans", tool=None, status="known", text="mine")])
    text = P.render_stretch(stretch)
    assert f"[Clash of Clans] 1 of yours (at most {P.NOTES_PER_PLACE}), 5 of the user's (not counted)" in text
    assert "never remove a" in P.system_prompt() and "the user's notes don't count" in P.system_prompt()

