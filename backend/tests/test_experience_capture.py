"""Post-audit 1, fill missing experience capture (docs/learning-system/POST-AUDIT-01-implementation.md, "Completion
evidence").

Real PostgreSQL (TEST_DATABASE_URL); no model calls. Her trajectory is made by the pack's own recorder
(game_packs/minecraft/src/trajectory.js, run in Node with a scripted bot), pulled by the real collector, saved by the
real writer and read back here, checked against what the script set, not against the encoder's output. The planner,
a hard thought and her hands are scripted learners on the real capture path.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.db.models import (
    CaptureGap, DecisionAttempt, DecisionExecution, DesktopEnvironment, DesktopSession, MemoryEvent, Observation,
    TrajectoryChunk, TrajectoryStream,
)
from app.services import capture_chains, learning_capture, trajectory, trajectory_capture
from tests.db_support import create_user, database, db_env  # noqa: F401  (fixtures)

PACK = Path(__file__).resolve().parents[2] / "game_packs" / "minecraft"
NODE = shutil.which("node")

SCENARIO = r"""
import { EventEmitter } from 'node:events'
import { createRequire } from 'node:module'
const [pack, scenario] = process.argv.slice(2)
const { Trajectory } = await import(pack + '/src/trajectory.js')
const { Vec3 } = createRequire(pack + '/package.json')('vec3')

function fakeBot () {
  const bot = new EventEmitter()
  bot._client = new EventEmitter()
  bot.username = 'Jophiel'
  bot.version = '26.1.2'
  bot.entity = { id: 1, position: new Vec3(0, 64, 0), velocity: new Vec3(0, 0, 0), yaw: 0, pitch: 0, onGround: true }
  bot.entities = { 1: bot.entity }
  bot.inventory = { slots: new Array(46).fill(null) }
  bot.controlState = { forward: false, back: false, left: false, right: false, jump: false, sprint: false, sneak: false }
  bot.health = 20
  bot.food = 20
  bot.game = { dimension: 'overworld', gameMode: 'survival' }
  return bot
}
let t = Date.now() - 120000
const traj = new Trajectory({ now: () => t, timers: false, chunkTicks: 20, bufferChunks: scenario === 'drop' ? 3 : 4000 })
let bot = fakeBot()
traj.attach(bot)
bot._client.emit('registry_data', { id: 'minecraft:damage_type', entries: [{ key: 'minecraft:fall' }, { key: 'minecraft:lava' }] })
const truth = []
const reads = []
const tick = (i) => {
  t += 50
  bot.emit('physicsTick')
  truth.push({ tick: i, at_ms: t, x: bot.entity.position.x, h: bot.health, logs: bot.inventory.slots[36]?.count ?? 0, jump: bot.controlState.jump })
}
const hit = (type) => { bot._client.emit('damage_event', { entityId: 1, sourceTypeId: type, sourceCauseId: 0, sourceDirectId: 0 }) }
if (scenario === 'full') {
  for (let i = 0; i < 70; i++) {
    if (i < 30) bot.entity.position = new Vec3(i / 10, 64, 0)
    if (i === 10) traj.event('skill_start', { run_id: 'run-1', skill: 'mine', params: { block: 'dirt' } })
    if (i === 20) traj.event('skill_finish', { run_id: 'run-1', skill: 'mine', status: 'done' })
    if (i === 25) { hit(0); bot.health = 17; bot.emit('health') } // after the run said done
    if (i === 40) for (let k = 0; k < 6; k++) { t += 5; hit(1); bot.health -= 2; bot.emit('health') }
    if (i === 45) bot.inventory.slots[36] = { name: 'oak_log', count: 2 }
    if (i === 50) hit(9) // an id the registry never named
    if (i === 60) bot.controlState.jump = true
    tick(i)
  }
  bot.emit('death')
  t += 3000 // dead: the client exposes no ticks
  bot.emit('respawn')
  for (let i = 70; i < 75; i++) tick(i)
  traj.end('test over')
} else {
  for (let i = 0; i < 20; i++) tick(i)
  await traj.settle()
  reads.push(traj.since(null))
  for (let i = 20; i < 120; i++) tick(i) // seq 1-5; the ring keeps 3, so 1 and 2 leave unread
  bot.emit('end', 'socketClosed')
  bot = fakeBot()
  traj.attach(bot)
  for (let i = 0; i < 20; i++) tick(i)
  traj.end('test over')
}
await traj.settle()
reads.push(traj.since(null, { maxChunks: 100000, maxBytes: Infinity }))
console.log(JSON.stringify({ reads, truth }))
"""


def _pack_run(tmp_path: Path, scenario: str) -> dict:
    if NODE is None:
        pytest.skip("node is needed to run the pack's recorder")
    script = tmp_path / "scenario.mjs"
    script.write_text(SCENARIO)
    out = subprocess.run([NODE, str(script), PACK.as_uri(), scenario], capture_output=True, text=True, timeout=60,
                         check=True)
    return json.loads(out.stdout)


class FakePackClient:
    """The pack's GET /trajectory, as the recorder answered it, one canned answer per call."""

    def __init__(self, reads: list[dict]):
        self.reads = list(reads)
        self.calls: list[tuple] = []

    def trajectory(self, *, stream=None, seq=None, limit=40):
        self.calls.append((stream, seq))
        if self.reads:
            return self.reads.pop(0)
        return {"run": "00000000-0000-0000-0000-000000000000", "chunks": [], "dropped": [], "streams": [],
                "more": False}


@pytest.fixture
def capture(db_env, tmp_path):  # noqa: F811
    svc = learning_capture.CaptureService(base_delay=0.01, max_delay=0.05, max_tries=3,
                                          gap_path=str(tmp_path / "gaps.jsonl"))
    before = learning_capture.install(svc)
    trajectory_capture.reset()
    yield svc
    svc.flush(5)
    learning_capture.install(before)
    trajectory_capture.reset()


@pytest.fixture
def desktop_on(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings().model_copy(update={"desktop_agent_enabled": True})
    monkeypatch.setattr(trajectory_capture, "get_settings", lambda: settings)
    return settings


def _pack_env(db, user, *, session_state: str | None = "running"):
    env = DesktopEnvironment(owner_user_id=user.id, display_name="pack", hosting_kind="game_pack",
                             allowed_profile_ids=["minecraft_pack"], status="online")
    db.add(env)
    db.flush()
    session = None
    if session_state:
        session = DesktopSession(owner_user_id=user.id, environment_id=env.id, profile_id="minecraft_pack",
                                 profile_revision=1, state=session_state, control_owner="gameplay",
                                 max_duration_seconds=3600,
                                 started_at=datetime.now(timezone.utc) - timedelta(hours=1))
        db.add(session)
    db.commit()
    return env, session


def _count(db, model, **where) -> int:
    query = select(func.count()).select_from(model)
    for key, value in where.items():
        query = query.where(getattr(model, key) == value)
    return db.scalar(query)


# --------------------------------------------------------------------------- #
# 3, 4, 8: every tick, the server's causes, what it costs
# --------------------------------------------------------------------------- #


def test_every_tick_is_rebuilt_from_saved_chunks_and_matches_what_the_bot_did(db_env, capture, desktop_on, tmp_path):
    pack = _pack_run(tmp_path, "full")
    with db_env.db() as db:
        user = create_user(db)
        _, session = _pack_env(db, user)
        observations, memories = _count(db, Observation), _count(db, MemoryEvent)
    client = FakePackClient(pack["reads"])
    assert trajectory_capture.run_once(client) is True
    assert capture.flush(5)
    with db_env.db() as db:
        [stream] = trajectory.streams(db, user.id)
        got = trajectory.read(db, user.id, uuid.UUID(stream["id"]))
        # Routine ticks become no observation and no memory, and nothing here called a model.
        assert (_count(db, Observation), _count(db, MemoryEvent)) == (observations, memories)
        assert _count(db, TrajectoryChunk, game_session_id=session.id) == stream["chunks"]
        measured = trajectory.measure(db, user.id)
    truth = pack["truth"]
    assert got["complete"], got["missing"]
    assert stream["end_seen"] and stream["end_reason"] == "test over" and stream["missing"] == []
    assert got["chunks"] == [0, 1, 2, 3]  # checkpoint boundaries every 20 ticks, the last one short
    ticks = got["ticks"]
    assert [t["tick"] for t in ticks] == [t["tick"] for t in truth] == list(range(75))
    for t, want in zip(ticks, truth):
        s = t["state"]
        assert abs(s["p"][0] - want["x"]) < 1e-3, t["tick"]
        assert s["h"] == want["h"] and (s["inv"].get("36") or [None, 0])[1] == want["logs"], t["tick"]
        assert bool(s["c"] & 16) == want["jump"], t["tick"]
        assert t["at"] == datetime.fromtimestamp(want["at_ms"] / 1000, timezone.utc).isoformat(timespec="milliseconds")
    standing = [t for t in ticks if 30 <= t["tick"] < 40]
    assert len(standing) == 10 and len({tuple(t["state"]["p"]) for t in standing}) == 1  # unchanged ticks are there
    # Dead for 3 s: time without ticks, never invented ticks.
    assert (datetime.fromisoformat(ticks[70]["at"]) - datetime.fromisoformat(ticks[69]["at"])).total_seconds() > 2.9
    events = got["events"]
    damage = [e["data"] for e in events if e["kind"] == "damage"]
    assert [d["type"] for d in damage] == ["minecraft:fall", *["minecraft:lava"] * 6, None]
    assert damage[-1]["type_id"] == 9 and "unknown" in damage[-1]["type_source"]
    assert len([e for e in events if e["kind"] == "health"]) == 7  # six hits in 30 ms: none lost
    kinds = [e["kind"] for e in events]
    # The run said done, and a later hit is still in its place: in order, with no cause made up for it.
    assert kinds.index("skill_finish") < kinds.index("damage")
    assert "cause" in damage[0] and damage[0]["cause"] is None
    assert kinds[-1] == "stream_end"
    assert measured["chunks"] == 4 and measured["ticks"] == 75 and measured["bytes"] > 0
    assert measured["bytes_per_minute"] > 0 and "projected_30_days_continuous_bytes" in measured
    assert measured["tick_handler_ms_mean"] is not None


def test_a_full_pack_buffer_a_reconnect_and_a_restart_are_never_shown_as_continuous(db_env, capture, desktop_on,
                                                                                   tmp_path):
    pack = _pack_run(tmp_path, "drop")
    with db_env.db() as db:
        user = create_user(db)
        _pack_env(db, user, session_state=None)  # in the world outside any session: still recorded
    client = FakePackClient(pack["reads"])
    assert trajectory_capture.run_once(client)  # what was there first
    assert trajectory_capture.run_once(client)  # the rest, after the ring dropped some
    assert trajectory_capture.run_once(client) is False  # nothing new
    first_stream = pack["reads"][0]["chunks"][0]["stream"]
    assert client.calls[:2] == [(None, None), (first_stream, 0)]  # each pull asks after the last chunk it took
    assert capture.flush(5)
    with db_env.db() as db:
        streams = sorted(trajectory.streams(db, user.id), key=lambda s: s["index"])
        first = trajectory.read(db, user.id, uuid.UUID(streams[0]["id"]), ticks=False)
        rows = _count(db, TrajectoryChunk, user_id=user.id)
        assert db.scalar(select(func.count()).select_from(TrajectoryChunk)
                         .where(TrajectoryChunk.game_session_id.is_not(None))) == 0
    assert [s["index"] for s in streams] == [0, 1]
    assert streams[0]["end_reason"] == "socketClosed" and streams[1]["end_seen"]
    # The ring kept 3 chunks: stream 0's seq 1-5 left it unread (its final chunk and stream 1's two are kept).
    assert streams[0]["missing"] == [{"from_seq": 1, "to_seq": 5}]
    assert [g["reason"] for g in first["gaps"]] == ["pack_dropped"] and not first["complete"]
    assert "seq 1-5" in first["gaps"][0]["detail"]
    assert first["chunks"] == [0, 6] and streams[1]["missing"] == []
    # A restart pulls again from the newest chunk saved; a chunk pulled twice is still one row.
    trajectory_capture.reset()
    again = FakePackClient([pack["reads"][-1]])
    trajectory_capture.run_once(again)
    assert again.calls[0][0] == streams[1]["id"]  # resumed from what was saved, not from nothing
    assert capture.flush(5)
    with db_env.db() as db:
        assert _count(db, TrajectoryChunk, user_id=user.id) == rows
        assert _count(db, TrajectoryStream, user_id=user.id) == 2


def test_storage_down_or_behind_never_blocks_and_never_fabricates_a_trajectory(db_env, desktop_on, tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    pack = _pack_run(tmp_path, "full")
    with db_env.db() as db:
        user = create_user(db)
        _pack_env(db, user)
    trajectory_capture.reset()
    # Storage down: the pull still returns at once; the chunks retry, then are gaps in the file; no rows.
    nowhere = sessionmaker(create_engine("postgresql+psycopg://nobody@127.0.0.1:1/none",
                                         connect_args={"connect_timeout": 1}))
    gaps = tmp_path / "gaps.jsonl"
    down = learning_capture.CaptureService(session_factory=nowhere, base_delay=0.01, max_delay=0.02, max_tries=2,
                                           gap_path=str(gaps))
    before = learning_capture.install(down)
    try:
        trajectory_capture.run_once(FakePackClient(pack["reads"]))
        assert down.flush(20)
        # Writer behind: no room, so nothing is pulled (the pack keeps buffering) and nothing is lost here.
        trajectory_capture.reset()
        behind = learning_capture.CaptureService(chunk_queue_max=1, gap_path=str(gaps))
        learning_capture.install(behind)
        behind._chunks_waiting = 1
        client = FakePackClient(pack["reads"])
        assert trajectory_capture.run_once(client) is False and client.calls == []
    finally:
        learning_capture.install(before)
        trajectory_capture.reset()
    lines = [json.loads(line) for line in gaps.read_text().splitlines()]
    assert {line["reason"] for line in lines} == {"retries_exhausted"} and len(lines) >= 4
    assert all(line["label"] in ("trajectory", "trajectory_stream") for line in lines)
    with db_env.db() as db:
        assert _count(db, TrajectoryChunk) == 0 and _count(db, TrajectoryStream) == 0


# --------------------------------------------------------------------------- #
# 7: retention, deletion, access, expired links
# --------------------------------------------------------------------------- #


def test_retention_deletion_and_access_cover_the_trajectory(db_env, capture, desktop_on, tmp_path):
    pack = _pack_run(tmp_path, "full")
    with db_env.db() as db:
        user = create_user(db)
        other = create_user(db)
        _, session = _pack_env(db, user)
    trajectory_capture.run_once(FakePackClient(pack["reads"]))
    assert capture.flush(5)
    with db_env.db() as db:
        [stream] = trajectory.streams(db, user.id)
        sid = uuid.UUID(stream["id"])
        assert trajectory.read(db, other.id, sid) is None  # another user's: not found
        assert trajectory.streams(db, other.id) == []
        chunk = db.scalar(select(TrajectoryChunk).where(TrajectoryChunk.stream_id == sid, TrajectoryChunk.seq == 1))
        inside = chunk.started_at + (chunk.ended_at - chunk.started_at) / 2
        found = trajectory.resolve(db, user.id, inside)
        assert (found["status"], found["seq"]) == ("found", 1)
        assert trajectory.resolve(db, user.id, datetime.now(timezone.utc) - timedelta(days=40))["status"] == "expired"
        assert trajectory.resolve(db, user.id, datetime.now(timezone.utc) + timedelta(days=1))["status"] == "missing"
        # Raw retention: past it, chunks go, then their stream.
        removed = learning_capture.prune(db, datetime.now(timezone.utc) + timedelta(days=31))
        assert removed["trajectory_chunks"] == 4 and removed["trajectory_streams"] == 1
        assert _count(db, TrajectoryChunk) == 0 and _count(db, TrajectoryStream) == 0
    # A deleted game session takes the chunks collected in it.
    trajectory_capture.reset()
    trajectory_capture.run_once(FakePackClient(_pack_run(tmp_path, "full")["reads"]))
    assert capture.flush(5)
    with db_env.db() as db:
        assert _count(db, TrajectoryChunk, game_session_id=session.id) == 4
        db.delete(db.get(DesktopSession, session.id))
        db.commit()
        assert _count(db, TrajectoryChunk) == 0
        # The memory reset's capture scope clears what's left of her trajectory.
        out = learning_capture.forget_user_raw(db, user.id)
        db.commit()
        assert out["trajectory_streams"] == 1 and _count(db, TrajectoryStream) == 0


def test_the_api_serves_only_the_owners_own_trajectory(db_env, capture, desktop_on, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.db.session import get_db
    from app.main import app

    pack = _pack_run(tmp_path, "full")
    with db_env.db() as db:
        user = create_user(db)
        stranger = create_user(db)
        _, session = _pack_env(db, user)
    trajectory_capture.run_once(FakePackClient(pack["reads"]))
    assert capture.flush(5)
    who = {"user": user}

    def session_db():
        with db_env.db() as db:
            yield db

    app.dependency_overrides[get_db] = session_db
    app.dependency_overrides[deps.get_current_user] = lambda: who["user"]
    app.dependency_overrides[deps.require_owner] = lambda: who["user"]
    try:
        client = TestClient(app)
        listed = client.get("/api/learning/capture/trajectory").json()
        sid = listed["streams"][0]["id"]
        body = client.get(f"/api/learning/capture/trajectory/{sid}", params={"from_tick": 40, "to_tick": 44,
                                                                              "readable": True}).json()
        assert [t["tick"] for t in body["ticks"]] == [40, 41, 42, 43, 44]
        assert body["ticks"][0]["state"]["health"] == 5 and body["ticks"][0]["state"]["in_lava"] is False
        assert client.get(f"/api/learning/capture/sessions/{session.id}").status_code == 200
        who["user"] = stranger
        assert client.get(f"/api/learning/capture/trajectory/{sid}").status_code == 404
        assert client.get(f"/api/learning/capture/sessions/{session.id}").status_code == 404
        assert client.get("/api/learning/capture/trajectory").json()["streams"] == []
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 1, 2, 5: planner, hard thought and hands on one session, reconstructed
# --------------------------------------------------------------------------- #

from app.ai.desktop.driver import ModelReply  # noqa: E402
from app.ai.mind.hard import HardInput, HardThought, Problem  # noqa: E402
from app.ai.mind.plan import PlanDecision, PlanStep  # noqa: E402
from app.ai.mind.contracts import CommandResult, Scope  # noqa: E402
from app.services import game_planning, mind, mind_hard, mind_minecraft  # noqa: E402
from tests.desktop_support import isolated_redis  # noqa: E402,F401  (autouse fixture)
from tests.test_desktop_sessions import _user, alice, db  # noqa: E402,F401  (fixtures)
from tests.test_game_planning import ScriptedPlanner, _session, planning  # noqa: E402,F401  (fixtures)
from tests.test_skill_driver import FakePack, Harness, ScriptedModel, _decide, _view  # noqa: E402


@pytest.fixture
def writer(database, tmp_path):  # noqa: F811
    svc = learning_capture.CaptureService(base_delay=0.01, max_delay=0.05, max_tries=3,
                                          gap_path=str(tmp_path / "gaps.jsonl"))
    before = learning_capture.install(svc)
    yield svc
    svc.flush(5)
    learning_capture.install(before)


def _planned(decision: str = "revise", **kw) -> PlanDecision:
    out = PlanDecision(decision, why="the old way can't reach iron", steps=[PlanStep(do="mine stone",
                       done_when="gained 3 cobblestone"), PlanStep(keep="s3")], rules=["no swimming"], **kw)
    out.raw, out.system, out.model, out.settings = json.dumps({"decision": decision}), "SYSTEM TEXT", "gpt-test", {
        "reasoning_effort": "low"}
    return out


class StatePack(FakePack):
    """Her state as the pack reads it: what she carries changes when a craft finishes."""

    def __init__(self):
        super().__init__()
        self.inventory = [{"name": "iron_ingot", "count": 4}]

    def state(self):
        return {"text": "Jophiel at (0, 64, 0).", "at": "2026-11-04T12:00:00.000Z", "position": {"x": 0, "y": 64, "z": 0},
                "health": 20, "food": 20, "inventory": list(self.inventory), "armor": {}}

    def get_run(self, run_id, *, wait=0):
        record = super().get_run(run_id, wait=wait)
        if record["status"] == "done" and record["skill"] == "craft":
            self.inventory = [{"name": "iron_boots", "count": 1}]
        return record


def test_planner_hard_thought_and_hands_reconstruct_with_three_separate_reports(db, alice, planning, writer,  # noqa: F811
                                                                              monkeypatch):
    session = _session(db, alice)  # its plan already holds a rule from before: "stay dry"
    plan_before = json.loads(json.dumps(session.plan))
    job = uuid.uuid4()
    # The first try fails (no answer), the job's second try decides: two attempts, the second linked to the first.
    failed = PlanDecision(None, error="APITimeoutError: timed out")
    failed.system, failed.model = "SYSTEM TEXT", "gpt-test"
    with pytest.raises(RuntimeError):
        game_planning.run(alice.id, session.id, ["stuck"], {"session_id": str(session.id)},
                          planner=ScriptedPlanner(failed), job_id=job, try_number=1)
    planner = ScriptedPlanner(_planned())
    outcome = game_planning.run(alice.id, session.id, ["stuck"], {"session_id": str(session.id)}, planner=planner,
                                job_id=job, try_number=2)
    assert not outcome.startswith(("dropped", "refused", "nothing")), outcome
    db.expire_all()
    revision = db.get(DesktopSession, session.id).plan_revision

    # A hard thought on the same session, with what it read.
    message_id = uuid.uuid4()
    problem = Problem("review", "Step s2 ended.", topic="look back: iron", query="iron", anchor={"step_id": "s2"})
    inp = HardInput(problem=problem, user="Vincent", conversation=({"role": "user", "content": "dig in steps",
                                                                     "ago": 30, "id": str(message_id)},),
                    memory_refs=("event:42",))
    live = SimpleNamespace(id=session.id, owner_user_id=alice.id, state="running", plan=session.plan,
                           commentary_mode="occasional", goal_item_id=None)

    class FakeDb:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, model, key):
            return live

        def commit(self):
            pass

    thought = HardThought("decision", "Dig down in steps, not straight.", thinking="lava was near",
                          for_hands="dig in steps", raw='{"kind": "decision"}', system="HARD SYSTEM", model="gpt-test")
    monkeypatch.setattr(mind_hard, "SessionLocal", FakeDb)
    monkeypatch.setattr(mind_hard, "gather", lambda d, source, scope, p: (inp, None, "Vincent"))
    monkeypatch.setattr(mind_hard, "_log_hard", lambda *a: None)
    monkeypatch.setattr(mind_hard, "thinker", lambda: SimpleNamespace(
        think=lambda i, situation: thought, instructions=lambda *a, **k: "HARD SYSTEM"))
    thought_id = uuid.uuid4()
    monkeypatch.setattr(mind, "save_thought", lambda d, **kw: SimpleNamespace(id=thought_id))
    monkeypatch.setattr(mind_hard, "route", lambda *a, **k: {"tip": CommandResult("refused", "her hands are learning")})
    scope = Scope(alice.id, "minecraft", session.id)
    att = mind_hard.Attention(running=problem, running_scope=scope)
    mind_hard.run(mind_minecraft.SOURCE, scope, att, problem, 100.0)

    # Her hands: a craft that works, then her claim that the whole set is done; a run that fails; one that a
    # danger interrupts; and getting it done after all.
    from app.workers import desktop

    view = _view(session_id=session.id, plan_revision=revision, plan={"goal": "", "steps": [
        {"id": "s1", "do": "make iron armor", "done_when": "wearing iron armor", "state": "active"}]})
    model = ScriptedModel(_decide("craft", {"item": "iron_boots"}),
                          _decide(status="step_done", note="I finished the armor set"),
                          _decide("craft", {"item": "iron_helmet"}), _decide("mine", {"block": "iron_ore"}),
                          _decide("mine", {"block": "iron_ore"}))
    harness = Harness(model, view=view)
    harness.clock = time.time()  # her hands' records carry real times (the session is read for the last hour)
    harness.pack = harness.run.deps.pack = StatePack()
    harness.run.session_id = session.id
    harness.run.deps.capture = desktop._hands_capture(session.id)
    harness.steps(3)  # craft boots: runs, done
    harness.steps(1)  # claims the set is done
    harness.pack.fail_reasons = ["missing ingredients"]
    harness.steps(3)  # craft helmet: fails
    harness.steps(1)  # mine: starts
    harness.pack.notice("creeper", "urgent", "a creeper right behind you")
    harness.steps(1)  # interrupted
    harness.steps(3)  # mine again: done
    assert writer.flush(5)

    # The harness's clock runs a second ahead per step: read a window that holds all of it.
    chain = capture_chains.session(db, alice.id, session.id, until=datetime.now(timezone.utc) + timedelta(minutes=1))
    by_learner = {}
    for item in chain["decisions"]:
        by_learner.setdefault(item["learner"], []).append(item)
    first_try, planned = by_learner["planner"]
    assert first_try["status"] == "unavailable" and first_try["applied"] is None
    attempt = db.get(DecisionAttempt, uuid.UUID(planned["id"]))
    assert attempt.prior_attempt_id == uuid.UUID(first_try["id"])
    assert attempt.input["text"] == planner.texts[0]  # exactly what the planner read
    assert attempt.input["plan"]["before"] == plan_before  # as it was then, not as it is now
    full = capture_chains.attempt(db, alice.id, attempt.id)
    assert full["attempts"][0]["input"]["system"] == "SYSTEM TEXT" and full["complete"]
    # The proposal and what code applied, side by side: the old rule came from code, not from the planner.
    applied = planned["applied"]
    assert applied["result"] == "deviated" and applied["revision_after"] == revision
    assert "stay dry (from previous_plan)" in applied_deviation(db, attempt.id)
    assert planned["proposal"]["rules"] == ["no swimming"]
    assert {r["rule"]: r["from"] for r in applied["rules"]["applied"]} == {
        "stay dry": ["previous_plan"], "no swimming": ["proposed"]}

    [hard] = by_learner["hard_thought"]
    assert hard["conclusion"]["kind"] == "decision" and hard["went"]["result"] == "deviated"  # the tip was refused
    hard_row = db.get(DecisionAttempt, uuid.UUID(hard["id"]))
    assert hard_row.input["messages"] == [str(message_id)] and hard_row.output["raw"] == '{"kind": "decision"}'
    assert hard_row.trigger == "hard:review"

    hands = by_learner["hands"]
    assert all(h["plan"]["made_by_planner_attempt"] == planned["id"] for h in hands)
    assert all(h["plan"]["goal"] == "" for h in hands)  # no goal: still a valid record, none invented
    craft, claim, helmet, mine_stopped, mine_done = hands
    # Three reports, never one label: the craft worked, she said the set was done, her state shows boots only.
    assert craft["reports"]["action"]["result"] == "succeeded"
    assert craft["reports"]["observed"]["after"]["inventory"] == {"iron_boots": 1}
    assert craft["reports"]["observed"]["before"]["inventory"] == {"iron_ingot": 4}
    assert claim["reports"]["claim"] == {"status": "step_done", "note": "I finished the armor set", "step_id": "s1",
                                         "by": "her hands (the decision's own words)"}
    assert claim["reports"]["action"] is None
    assert helmet["reports"]["action"]["result"] == "failed" and "missing ingredients" in helmet["reports"]["action"]["deviation"]
    assert mine_stopped["reports"]["action"]["result"] == "cancelled"
    assert "urgent" in mine_stopped["reports"]["action"]["reason"]
    assert mine_done["reports"]["action"]["result"] == "succeeded"
    assert chain["leaks"] == {}
    assert not [m for m in chain["missing"] if m["what"] in ("skill run", "plan_apply", "thought_route")]


def applied_deviation(db, attempt_id: uuid.UUID) -> str:
    row = db.scalar(select(DecisionExecution).where(DecisionExecution.attempt_id == attempt_id,
                                                    DecisionExecution.kind == "plan_apply"))
    return row.deviation or ""


def test_a_missing_record_stays_visible_in_the_session(db, alice, planning, writer):  # noqa: F811
    session = _session(db, alice)
    ctx = learning_capture.Ctx(user_id=alice.id, attempt_id=uuid.uuid4(), trigger="hands_step",
                               game_session_id=session.id)
    learning_capture.attempt(ctx, learner="hands", provider="openai", model="m", input={"text": "x", "plan": {
        "revision": 99, "step_id": "s1"}}, output={"output": {"status": "working"}, "action": ["mine", {}]},
        status="ok", started_at=datetime.now(timezone.utc), produced_at=datetime.now(timezone.utc))
    learning_capture.service().gap(user_id=alice.id, record_kind="execution", record_id=uuid.uuid4(),
                                   label="skill_run", reason="queue_full", detail="test")
    assert writer.flush(5)
    chain = capture_chains.session(db, alice.id, session.id)
    assert not chain["complete"]
    assert {"what": "skill run", "attempt": str(ctx.attempt_id), "note": "no record of what it did"} in chain["missing"]
    assert "unknown here" in chain["decisions"][0]["plan"]["made_by"]  # no planner record made revision 99
    assert [g["reason"] for g in chain["gaps"]] == ["queue_full"]


def test_code_counting_a_step_done_is_its_own_record(db, alice, planning, writer):  # noqa: F811
    from app.workers import desktop

    session = _session(db, alice)

    class CountedPack(StatePack):
        def state(self):
            return {**super().state(), "inventory": [{"name": "oak_log", "count": 3}]}

    view = _view(session_id=session.id, plan={"goal": "logs", "steps": [
        {"id": "s1", "do": "chop 3 oak logs", "done_when": "3 oak_log", "state": "active",
         "check": {"kind": "has", "what": "oak_log", "count": 3}}]})
    harness = Harness(ScriptedModel(), view=view)
    harness.pack = harness.run.deps.pack = CountedPack()
    harness.run.session_id = session.id
    harness.run.deps.capture = desktop._hands_capture(session.id)
    harness.steps(1)
    assert writer.flush(5)
    assert harness.model.prompts == []  # counted done: no decision was asked for
    row = db.scalar(select(DecisionExecution).where(DecisionExecution.kind == "step_check"))
    assert row.attempt_id is None and row.intended["step_id"] == "s1" and row.result == "succeeded"
    assert row.executed["counted"]
    assert row.executed["observed"]["inventory"] == {"oak_log": 3}
