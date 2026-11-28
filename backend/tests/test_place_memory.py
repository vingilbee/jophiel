"""Her picture of a place (app/ai/place_memory.py, services/place_memory.py): screenshots offered as she uses a place,
updated at once and one at a time, edits (not rewrites) checked and kept, lasting entries per place and world, her
own lines kept apart, and everything of this sitting gone with her screens. The updater's model is scripted."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time
import uuid
from contextlib import contextmanager

import pytest

from app.ai import place_memory as P
from app.services import phone_control, place_memory
from db_support import database, db_env  # noqa: F401


class FakeRedis:
    """Enough of Redis for her pictures: strings, hashes, sets, one sorted set, a pipeline that answers."""

    def __init__(self):
        self.data: dict[str, object] = {}

    def pipeline(self, transaction=True):
        return _Pipe(self)

    def _b(self, v):
        return v if isinstance(v, bytes) else str(v).encode()

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v, ex=None, nx=False):
        if nx and k in self.data:
            return None
        self.data[k] = self._b(v)
        return True

    def rpush(self, k, v):
        self.data.setdefault(k, []).append(v)

    def ltrim(self, k, start, end):
        self.data[k] = self.data.get(k, [])[start:]

    def hset(self, k, f=None, v=None, mapping=None):
        h = self.data.setdefault(k, {})
        for key, value in (mapping or {f: v}).items():
            h[self._b(key)] = self._b(value)

    def hgetall(self, k):
        return dict(self.data.get(k, {}))

    def sadd(self, k, v):
        self.data.setdefault(k, set()).add(self._b(v))

    def smembers(self, k):
        return set(self.data.get(k, set()))

    def expire(self, k, s):
        pass

    def zadd(self, k, mapping, nx=False):
        z = self.data.setdefault(k, {})
        for m, s in mapping.items():
            if not (nx and self._b(m) in z):
                z[self._b(m)] = s

    def zrangebyscore(self, k, lo, hi, start=0, num=None):
        return [m for m, s in sorted(self.data.get(k, {}).items(), key=lambda x: x[1]) if lo <= s <= hi][:num]

    def zrem(self, k, *ms):
        return sum(1 for m in ms if self.data.get(k, {}).pop(self._b(m), None) is not None)

    def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)


class _Pipe:
    def __init__(self, client):
        self.client, self.calls = client, []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        return [getattr(self.client, name)(*args, **kwargs) for name, args, kwargs in self.calls]


def _jpeg():
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (160, 72), (30, 120, 40)).save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode()


class _Session:
    """Stands in for the database where a test doesn't keep layouts: what's added is only collected."""

    added: list = []

    def add(self, row):
        _Session.added.append(row)

    def commit(self):
        pass


@contextmanager
def _no_db():
    yield _Session()


@pytest.fixture
def world(monkeypatch):
    client = FakeRedis()
    state = {"epoch": 0, "control_on": True, "layout": {}}
    monkeypatch.setattr(place_memory, "_client", lambda: client)
    monkeypatch.setattr(place_memory, "_epoch", lambda user_id: state["epoch"])
    monkeypatch.setattr(phone_control, "_control_off", lambda user_id: not state["control_on"])
    monkeypatch.setattr(place_memory, "SessionLocal", _no_db)
    monkeypatch.setattr(place_memory, "_layout_row", lambda db, user_id, surface, place: None)
    monkeypatch.setattr(place_memory, "_apply_lasting", lambda db, user_id, surface, place, before, applied:
                        state.setdefault("lasting", []).append(applied) or {})
    monkeypatch.setattr(place_memory, "_trace", lambda record: state.setdefault("traces", []).append(record))
    return client, state


def _edit(op, keep="sitting", text="", **kw):
    return P.Edit(why="seen on the screen", op=op, keep=keep, text=text, **kw)


def test_a_screen_updates_at_once_and_screens_during_an_update_are_the_next_one(world):
    client, state = world
    user, now = uuid.uuid4(), 1000.0
    first, second, newest = _jpeg(), _jpeg()[:-4] + "AAA=", _jpeg()[:-4] + "BBB="
    place_memory.offer(user, "Clash of Clans", first, size=(1600, 720), action="tapped [3] → Phone screen s1",
                       marks=[{"tap": [100, 600]}], now=now)
    assert client.data[place_memory.WAKEUPS]  # the worker is woken: no wait before the first update
    seen, drawn = [], []

    def model(text, images):
        seen.append((text, images))
        if len(seen) == 1:  # her hands go on while the first update runs
            assert not place_memory.run_once(client=client, now=now + 1, model=model)  # one at a time per place
            place_memory.offer(user, "Clash of Clans", second, size=(1600, 720),
                               action="tapped [5] → Phone screen s2", marks=[{"tap": [300, 650]}], now=now + 1)
            place_memory.offer(user, "Clash of Clans", newest, size=(1600, 720),
                               action="swiped left → Phone screen s3", marks=[{"tap": [1540, 400]}], now=now + 2)
            return P.Update(world="", doing="Attacking, 2:40 left.", where="",
                            edits=[_edit("add", text="4 dragons dropped beyond the right wall.")])
        return P.Update(world="", doing="Attacking, 2:30 left.", where="", edits=[
            _edit("change", id="s1", text="4 dragons beyond the right wall, out of view since I panned left.")])

    from app.services import practice
    original = practice._draw
    practice._draw = lambda jpeg, marks: drawn.append((jpeg, marks)) or jpeg
    try:
        assert place_memory.run_once(client=client, now=now, model=model)
        assert place_memory.run_once(client=client, now=now + 3, model=model)  # right after, nothing to wait out
    finally:
        practice._draw = original
    assert drawn == [(first, [{"tap": [100, 600]}]),
                     (newest, [{"tap": [300, 650]}, {"tap": [1540, 400]}])]  # the newest, with every tap since
    assert seen[0][1] == [first] and seen[1][1] == [first, newest]  # the one it saw last time, then the newest
    assert "- tapped [3] → Phone screen s1" in seen[0][0] and "(1600x720, landscape)" in seen[0][0]
    assert "- s1 4 dragons dropped beyond the right wall." in seen[1][0] and "Two screenshots" in seen[1][0]
    picture = place_memory.read(user, "clash of clans")
    assert picture.doing == "Attacking, 2:30 left."
    assert [e.text for e in picture.sitting] == ["4 dragons beyond the right wall, out of view since I panned left."]
    assert not place_memory.run_once(client=client, now=now + 60)  # nothing waits any more
    assert not any(k.startswith(("place:pending", "place:busy")) for k in client.data)  # screenshots gone once read
    trace = state["traces"][-1]
    assert trace["outcome"] == "updated" and trace["images"] == 2 and trace["whys"]
    assert "dragons" not in json.dumps(state["traces"])  # an entry's words never go in the trace


def test_edits_are_checked_against_what_the_updater_was_shown():
    picture = P.Picture(place="Growtopia", world="BUSYFARM",
                        sitting=(P.Entry(id="s1", text="Holding 20 dirt seeds."),),
                        lasting=(P.Entry(id="L1", text="World door, far left.", kind="landmark", key="k1"),
                                 P.Entry(id="L2", text="Farm, right of the door.", kind="area", key="k2")))
    applied = P.apply(picture, P.Update(world="BUSYFARM", doing="Planting.", where="On the farm.", edits=[
        _edit("change", id="s1", text="Holding 12 dirt seeds."),
        _edit("add", text="Watering can in slot 3."),
        _edit("remove", id="s9"),  # never shown
        _edit("add", keep="lasting", kind="thing", near="L2", text="Chest with seeds, top-left of the farm."),
        _edit("add", keep="lasting", text="no kind given"),
        _edit("change", keep="lasting", id="L1", text="World door, far left, blue."),
        _edit("remove", keep="lasting", id="L7"),
    ]), next_sitting=2)
    assert [(e.id, e.text) for e in applied.sitting] == [("s1", "Holding 12 dirt seeds."),
                                                         ("s2", "Watering can in slot 3.")]
    assert applied.next_sitting == 3
    assert [(e.kind, e.near, e.text) for e in applied.lasting_add] == [
        ("thing", "L2", "Chest with seeds, top-left of the farm.")]
    assert applied.lasting_change == {"k1": "World door, far left, blue."}
    assert len(applied.refused) == 3 and len(applied.whys) == 7
    # Another world: the lasting entries shown were the old world's, so only adds stand, for the new one.
    moved = P.apply(picture, P.Update(world="START", doing="", where="", edits=[
        _edit("remove", keep="lasting", id="L1"),
        _edit("add", keep="lasting", kind="landmark", text="Signpost by the spawn.")]), next_sitting=2)
    assert moved.world == "START" and not moved.lasting_remove and len(moved.lasting_add) == 1
    # Full lists refuse adds.
    full = P.Picture(place="x", sitting=tuple(P.Entry(id=f"s{n}", text="t") for n in range(P.SITTING_MAX)))
    assert P.apply(full, P.Update(world="", doing="", where="", edits=[_edit("add", text="one more")]),
                   next_sitting=99).refused == ["sitting full"]


def test_her_own_lines_stay_hers_and_this_sitting_goes_with_her_screens(world):
    client, state = world
    user = uuid.uuid4()
    assert "Kept" in place_memory.keep_in_mind(user, "Clash of Clans", "King is for the second wave.")
    place_memory.offer(user, "Clash of Clans", _jpeg(), size=(1600, 720), action=None, now=time.time())
    seen = {}

    def model(text, images):
        seen["text"] = text
        return P.Update(world="", doing="Looking at the base.", where="", edits=[])

    place_memory.run_once(client=client, now=time.time() + 1, model=model)
    assert "Her own lines for now: King is for the second wave." in seen["text"]
    assert "No actions since your last update: she looked." in seen["text"]
    picture = place_memory.read(user, "Clash of Clans")
    assert picture.her_now == "King is for the second wave." and picture.doing == "Looking at the base."
    text = place_memory.for_turn(user)
    assert "What you know of Clash of Clans" in text and "Your own lines for now: King is for the second wave." in text
    state["epoch"] = 1  # Phone control went off: her screens were wiped
    assert place_memory.read(user, "Clash of Clans") is None and place_memory.for_turn(user) is None
    assert "Not kept: that's more than" in place_memory.keep_in_mind(user, "Clash of Clans", "x" * 500)


def test_a_screenshot_waiting_when_control_goes_off_is_dropped(world):
    client, state = world
    user = uuid.uuid4()
    place_memory.offer(user, "Growtopia", _jpeg(), size=(720, 1600), action="tapped [1] → s1", now=100.0)
    state["control_on"] = False
    called = []
    assert place_memory.run_once(client=client, now=200.0, model=lambda *a: called.append(a))
    assert not called and "dropped" in state["traces"][-1]["outcome"]
    place_memory.offer(user, "Growtopia", _jpeg(), size=(720, 1600), now=300.0)
    place_memory.wipe(user, client=client)
    assert not any(k.startswith("place:") and "Growtopia".lower() in k for k in client.data)


def test_nothing_is_offered_without_a_place_or_a_picture(world):
    client, _ = world
    place_memory.offer(uuid.uuid4(), None, _jpeg(), size=None)
    place_memory.offer(uuid.uuid4(), "Settings", None, size=None)
    assert client.data == {}


def test_how_a_picture_reads_to_her():
    farm = P.Entry(id="L2", text="Farm, right of the door.", kind="area", key="k2")
    picture = P.Picture(
        place="Growtopia", world="BUSYFARM", doing="Planting seeds.", where="Middle of the farm.",
        sitting=(P.Entry(id="s1", text="Holding 12 dirt seeds."),),
        lasting=(P.Entry(id="L1", text="Build button, lower right.", kind="control", key="k1"), farm,
                 P.Entry(id="L3", text="Chest with seeds, top-left.", kind="thing", near="L2", key="k3")),
        her_layout="Sell the extra seeds at the vend.", now_at=100.0)
    text = P.text_for_her(picture, now=130.0)
    assert text.startswith("What you know of Growtopia, world BUSYFARM (kept from your screenshots there, "
                           "last updated 30 s ago;")
    assert "- Doing: Planting seeds." in text and "- Where you are: Middle of the farm." in text
    assert "  - Holding 12 dirt seeds." in text
    assert "  - [area] Farm, right of the door.\n    - [thing] Chest with seeds, top-left." in text  # filed under it
    assert "- Your own lines about the place: Sell the extra seeds at the vend." in text
    assert "L2" not in text  # ids are the updater's, not hers
    seen = {f"{e.key}:{e.text}" for e in picture.lasting if e.id != "L3"}
    again = P.text_for_her(picture, now=130.0, lasting_seen=seen)
    assert "new or changed since you last read it" in again and "Chest" in again and "Build button" not in again
    prompt = P.system_prompt()
    assert "never by pixels" in prompt and "Out of view is not gone" in prompt and "go in why" in prompt


def test_lasting_entries_are_kept_per_place_and_world(db_env, world, monkeypatch):
    client, _ = world
    from db_support import create_user
    monkeypatch.setattr(place_memory, "SessionLocal", db_env.db)  # the fake Redis stays; entries are real rows
    monkeypatch.setattr(place_memory, "_layout_row", _real_layout_row)
    monkeypatch.setattr(place_memory, "_apply_lasting", _REAL_APPLY_LASTING)
    with db_env.db() as db:
        user = create_user(db).id
    answers = [
        P.Update(world="BUSYFARM", doing="Walking.", where="By the door.", edits=[
            _edit("add", keep="lasting", kind="landmark", text="World door, far left."),
            _edit("add", keep="lasting", kind="area", text="Farm, right of the door.")]),
        P.Update(world="BUSYFARM", doing="Farming.", where="On the farm.", edits=[
            _edit("add", keep="lasting", kind="thing", near="L1", text="Chest with seeds, top-left of the farm.")]),
        P.Update(world="START", doing="Visiting.", where="", edits=[
            _edit("add", keep="lasting", kind="landmark", text="Signpost by the spawn.")]),
    ]
    for n, answer in enumerate(answers):
        place_memory.offer(user, "Growtopia", _jpeg(), size=(720, 1600), action="walked → s1", now=10.0 + n)
        place_memory.run_once(client=client, now=10.0 + n, model=lambda text, images, a=answer: a)
    with db_env.db() as db:
        [row] = place_memory.list_places(db, user)
        by_text = {e.text: e for e in row.entries}
        assert {(e.world, e.kind) for e in row.entries} == {("BUSYFARM", "landmark"), ("BUSYFARM", "area"),
                                                            ("BUSYFARM", "thing"), ("START", "landmark")}
        # L1 was the newest-seen entry when the second update read the list: the farm area (added last).
        chest = by_text["Chest with seeds, top-left of the farm."]
        assert chest.near_id is not None and db.get(type(chest), chest.near_id).kind in ("area", "landmark")
    picture = place_memory.read(user, "Growtopia")
    assert picture.world == "START" and [e.text for e in picture.lasting] == ["Signpost by the spawn."]
    assert "Kept with what you know of Growtopia, for next time too." == place_memory.keep_in_mind(
        user, "Growtopia", "Sell extra seeds.", lasting=True)
    with db_env.db() as db:
        [row] = place_memory.list_places(db, user)
        assert row.her_layout == "Sell extra seeds."
        assert place_memory.remove_entry(db, user, row.entries[0].id)
        assert place_memory.remove_place(db, user, row.id)
        db.commit()
        assert place_memory.list_places(db, user) == []


_REAL_APPLY_LASTING = place_memory._apply_lasting


def _real_layout_row(db, user_id, surface, place):
    from sqlalchemy import func, select

    from app.db.models import PlaceLayout
    return db.scalar(select(PlaceLayout).where(
        PlaceLayout.user_id == uuid.UUID(str(user_id)), PlaceLayout.surface == surface,
        func.lower(PlaceLayout.place) == place.lower()))


def test_her_phone_tools_offer_each_screen_and_show_the_picture_when_it_changed(monkeypatch):
    from app.ai.tools import phone_control as control_tools
    from app.services import phone_actions, phone_state
    from test_phone_phase9 import screen, tools_by_name
    user, turn = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(phone_control, "_epoch", lambda *args, **kwargs: 0)
    monkeypatch.setattr(phone_state, "read", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_control, "_client", lambda: None)
    monkeypatch.setattr(phone_actions, "act", lambda *args, **kwargs: phone_actions.Outcome(
        "done", uuid.uuid4(), "Done", screen=screen("s7", app="Clash of Clans")))
    offered, kept = [], []
    monkeypatch.setattr(place_memory, "offer", lambda *args, **kwargs: offered.append((args, kwargs)))
    current = {"picture": P.Picture(place="Clash of Clans", doing="Battle running.", now_at=time.time(),
                                    lasting=(P.Entry(id="L1", text="Build button.", kind="control", key="k1"),))}
    monkeypatch.setattr(place_memory, "read", lambda user_id, place: current["picture"])
    monkeypatch.setattr(place_memory, "keep_in_mind",
                        lambda user_id, place, text, lasting=False: kept.append((place, text, lasting)) or "Kept")
    tools, _ = control_tools.build_phone_control_tools(user, uuid.uuid4(), turn_id=turn)
    named = tools_by_name(tools)
    first = asyncio.run(named["read_phone_screen"].ainvoke({}))
    again = asyncio.run(named["phone_tap_point"].ainvoke({"x": 300, "y": 400}))
    assert "What you know of Clash of Clans" in first and "What you know of" not in again  # only when it changed
    assert offered[0][1]["action"] is None  # a look
    assert offered[1][1]["action"].startswith("tapped point (300, 400) → Phone screen s7")
    assert offered[1][1]["marks"] == [{"tap": [300, 400]}] and offered[1][1]["size"] == (720, 1600)
    current["picture"] = P.Picture(place="Clash of Clans", doing="Dragons on the right.", now_at=time.time(),
                                   lasting=current["picture"].lasting)
    changed = asyncio.run(named["read_phone_screen"].ainvoke({}))
    assert "Dragons on the right." in changed and "Build button" not in changed  # lasting entries once a turn
    assert asyncio.run(named["keep_in_mind"].ainvoke({"text": "King last.", "lasting": False})) == "Kept"
    assert kept == [("Clash of Clans", "King last.", False)]
