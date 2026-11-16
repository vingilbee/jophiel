"""Post-audit 4 behavioral acceptance with real models (docs/learning-system/POST-AUDIT-04-implementation.md).

    uv run python scripts/broader_learning_eval.py --list                    # the reserved variants, no model calls
    uv run python scripts/broader_learning_eval.py --run                     # every variant, two isolated runs each
    uv run python scripts/broader_learning_eval.py --run --variant P.v1 --runs 1
    uv run python scripts/broader_learning_eval.py --run --parallel 6

Needs TEST_DATABASE_URL (a disposable database: each run gets its own schema, dropped afterwards) and
OPENAI_API_KEY. Refuses the development database.

Reads the frozen corpus in evals/post_audit_04 (reserved/fixtures.json, RUBRIC.md) and nothing else from there.
Every model that learns or decides is the configured one, called through the app's own code: the reviewer
(``learning_assessment.assess``), the lesson builder (``learning_lessons.process``), the reconsideration
(``learning_lessons.process_reconsider``), one evaluation of her mind on the deployed path (``mind_turn`` +
``complete_chat``: her prompt, lessons and tools as a reply gets them), her hard thoughts (``HardThinker``), her planner (``Planner``) and her hands (``skill_driver.system_prompt`` +
``OpenAIDriver``). Source records (messages, game experience, facts, her hypotheses, seeded products, critiques,
premise changes) are controlled and labelled so in the report. The planner's and hands' situation text is built
here from the fixture; their learned section is the app's own rendering.

Each requirement is checked by code ("auto") or by a judging model ("judge") against the frozen rubric; the critical
integrity checks run on every run. A run passes only when every requirement of its variant holds and no critical
failure was seen. The report (every run, nothing discarded) goes to var/broader_learning_eval/<time>.json, with the
family results against the accepted rule.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
import traceback
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(1, str(ROOT / "tests"))  # db_support: her seeded CoreMemory, as tests use it
CORPUS = ROOT / "evals" / "post_audit_04"
OUT = ROOT / "var" / "broader_learning_eval"
FAMILIES = ("U", "C", "X", "R", "P", "E", "G", "T")
# What each probe actually exercises (review finding 3): component evaluations, not the deployed paths. A consumer
# covered here is covered as a component; deployed-path coverage needs its own integration probes.
PROBE_PATHS = {
    "conversation": "deployed: one evaluation of her mind (mind_turn.prepare_message + complete_chat with her real "
                    "prompt, lessons and tools), learning capture disabled; tools run against the eval's own database",
    "mind": "component: HardThinker on a minimal HardInput built here (problem, conversation, the app's own lesson "
            "lines); not the scheduled mind path; conclusion not routed",
    "planner": "component: Planner on situation text built here plus the app's own learned section; plan not applied",
    "hands": "component: skill_driver.system_prompt + skill_schema with the app's lesson supply, OpenAIDriver one "
             "step; the chosen action is not executed, so no outcome is measured",
}
# Earlier fixtures name the conversational consumer by its retired decision model.
CONSUMER_ALIASES = {"student_2": "conversation"}
JUDGE_VERSION = "broader-learning-judge-v1"
# The evaluator as a whole (runner, checks, judge, gate): a repair is a new version; old scores stay what they were.
EVALUATOR_VERSION = "broader-learning-eval-v2"


CORPORA = {"reserved": "reserved/fixtures.json", "development": "development/fixtures.json",
           "v1-diagnosed": "development/v1-diagnosed.json", "v2-diagnosed": "development/v2-diagnosed.json"}


def load(corpus: str = "reserved") -> dict:
    """``reserved``: the acceptance corpus. ``development`` / ``v1-diagnosed``: development material (the runner's
    mechanics; the first reserved set, read to diagnose its failures)."""
    return json.loads((CORPUS / CORPORA[corpus]).read_text())


# --------------------------------------------------------------------------- #
# Counting model calls (every learner and consumer goes through one of these)
# --------------------------------------------------------------------------- #


class Counted:
    def __init__(self, inner, label: str, log: list, context: str | None = None):
        self.inner, self.label, self.log, self.context = inner, label, log, context
        self.name = getattr(inner, "name", label)
        self.model = getattr(inner, "model", None)

    def __call__(self, text):
        started = time.perf_counter()
        answer = self.inner(text)
        self.log.append({"model": self.label, "context": self.context, "input": text[:30000],
                         "chars": len(text), "ms": round((time.perf_counter() - started) * 1000),
                         "error": getattr(answer, "error", None),
                         "input_tokens": getattr(answer, "input_tokens", None),
                         "output_tokens": getattr(answer, "output_tokens", None)})
        return answer


# --------------------------------------------------------------------------- #
# One isolated run
# --------------------------------------------------------------------------- #


@dataclass
class Record:
    variant: str
    family: str
    run: int
    started: str
    labels: dict = field(default_factory=dict)  # fixture label -> what it became (ids, roots)
    steps: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    probes: list = field(default_factory=list)
    paused: list = field(default_factory=list)
    wakes: list = field(default_factory=list)
    learned: dict = field(default_factory=dict)  # stretch label -> what learning did with it
    requirements: list = field(default_factory=list)
    critical: list = field(default_factory=list)
    products: list = field(default_factory=list)
    versions: dict = field(default_factory=dict)
    error: str | None = None
    passed: bool = False


class Run:
    def __init__(self, variant: dict, number: int, url: str):
        self.v, self.number, self.url = variant, number, url
        self.rec = Record(variant["id"], variant["family"], number, datetime.now(timezone.utc).isoformat())
        self.now = datetime.now(timezone.utc)
        self.paused = False
        self.deferred: list[dict] = []
        self.manifest = json.loads((CORPUS / "minecraft_manifest.json").read_text())
        self.dispatches: list[dict] = []
        self.reconsiderations: list[dict] = []

    # -- the schema ------------------------------------------------------------

    @contextmanager
    def database(self):
        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        from app.db import session as app_session
        from app.db.base import Base
        from db_support import columbina_core_memory

        self.schema = "eval_" + uuid.uuid4().hex
        admin = create_engine(self.url)
        with admin.begin() as c:
            c.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self._engine(create_engine, sessionmaker, app_session)
        Base.metadata.create_all(self.engine)
        with self.db() as db:
            db.add(columbina_core_memory())
            db.commit()
        try:
            yield
        finally:
            self.engine.dispose()
            with admin.begin() as c:
                c.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
            admin.dispose()

    def _engine(self, create_engine, sessionmaker, app_session):
        self.engine = create_engine(self.url, connect_args={"options": f"-csearch_path={self.schema}"})
        self.db = sessionmaker(self.engine, expire_on_commit=False)
        app_session.SessionLocal.configure(bind=self.engine)

    def restart(self):
        """A worker restart: every engine and session gone, nothing held in process between them."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.db import session as app_session
        self.engine.dispose()
        self._engine(create_engine, sessionmaker, app_session)

    # -- models ----------------------------------------------------------------

    def evaluator(self, context=None):
        from app.services import learning_assessment as LA
        return Counted(LA._evaluator(), "reviewer", self.rec.calls, context)

    def builder(self, context=None):
        from app.services import learning_lessons as LL
        return Counted(LL._builder(), "builder", self.rec.calls, context)

    def reconsiderer(self, context=None):
        from app.services import learning_lessons as LL
        return Counted(LL._reconsiderer(), "reconsider", self.rec.calls, context)

    # -- the timeline ------------------------------------------------------------

    def execute(self):
        from app.core.config import get_settings
        s = get_settings()
        self.rec.versions = {"reviewer": f"assessment-v4:{s.learning_assessment_model}",
                             "builder": f"lessons-v3:{s.learning_lessons_model}",
                             "reconsider": f"lessons-reconsider-v1:{s.learning_lessons_model}",
                             "conversation": s.chat_model, "mind": s.mind_hard_model,
                             "planner": s.mind_plan_model, "hands": s.desktop_driver_model, "judge": JUDGE_VERSION}
        with self.db() as db:
            from app.db.models import User
            user = User(name=self.v["user_name"], email=f"{uuid.uuid4()}@eval.local", hashed_password="unused")
            db.add(user)
            db.commit()
            self.user_id = user.id
            from app.services import learning_assessment as LA
            LA.started_at(db)
            db.commit()
        for step in self.v["setup"]:
            self.step(step)
        for probe in self.v.get("probes") or []:
            if probe.get("after") == "revision" and self.v.get("revision") and "revision" not in self.rec.labels:
                self.step(self.v["revision"])
                self.rec.labels["revision"] = True
            self.rec.probes.append(self.probe(probe))
        self.snapshot_products()

    def step(self, step: dict):
        kind = step["do"]
        self.rec.steps.append({"do": kind, "label": step.get("label"), "at": time.time()})
        if kind in ("conversation", "game"):
            label = self.conversation(step) if kind == "conversation" else self.game(step)
            for later in [d for d in self.deferred if d.get("rests_on") == label]:
                self.deferred.remove(later)
                self.seed(later)
            if step.get("learn"):
                self.learn(step["label"])
        elif kind == "fact":
            self.fact(step)
        elif kind == "hypothesis":
            self.hypothesis(step)
        elif kind == "seed_lesson":
            if step["rests_on"] in self.rec.labels:
                self.seed(step)
            else:
                self.deferred.append(step)
        elif kind == "critique":
            self.critique(step)
        elif kind == "wake_unchanged":
            self.wake(step)
        elif kind == "pause_reassessment":
            self.paused = True
        elif kind == "resume_reassessment":
            self.paused = False
            self.reassess()
        elif kind == "change_fact":
            self.change_fact(step)
        elif kind == "withdraw_lesson":
            self.withdraw_lesson(step)
        elif kind == "close_hypothesis":
            self.close_hypothesis(step)
        elif kind == "probe_paused":
            self.rec.paused.append(self.paused_inputs())
        elif kind == "restart":
            self.restart()
        else:
            raise ValueError(f"unknown step {kind}")

    def _at(self, step: dict, offsets: list[int]) -> list[datetime]:
        base = self.now - timedelta(days=step.get("days_ago", 0))
        gaps = step.get("gaps_minutes") or [1] * len(offsets)
        if step.get("weekday"):
            # The latest such weekday at least days_ago back, at the hour given: what the fixture says it is.
            days = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun").index(step["weekday"])
            while base.weekday() != days:
                base -= timedelta(days=1)
            start = base.replace(hour=step.get("hour", 12), minute=0, second=0, microsecond=0)
            out, t = [], start - timedelta(minutes=gaps[0] if gaps else 1)
            for g in gaps[:len(offsets)] + [1] * max(0, len(offsets) - len(gaps)):
                t = t + timedelta(minutes=g)
                out.append(t)
            return out
        out, t = [], base - timedelta(minutes=sum(gaps) + 5)
        for g in gaps[:len(offsets)] + [1] * max(0, len(offsets) - len(gaps)):
            t = t + timedelta(minutes=g)
            out.append(t)
        return out

    def conversation(self, step: dict) -> str:
        from app.db.models import Conversation
        from app.services import chat as chat_service

        times = self._at(step, step["messages"])
        with self.db() as db:
            conv = Conversation(user_id=self.user_id, title=step["label"])
            db.add(conv)
            db.commit()
            ids = []
            turn = None
            for (speaker, words), at in zip(step["messages"], times):
                role = "assistant" if speaker == "her" else "user"
                if role == "user":
                    turn = uuid.uuid4()
                msg = chat_service.add_message(db, conversation=conv, role=role, content=words, turn_id=turn,
                                               status="completed")
                msg.created_at = at
                db.commit()
                ids.append(str(msg.id))
            self.rec.labels[step["label"]] = {"kind": "conversation", "conversation": str(conv.id), "messages": ids,
                                              "roots": [f"message:{i}" for i in ids]}
        return step["label"]

    def game(self, step: dict) -> str:
        from app.db.models import DesktopEnvironment, DesktopSession, GameExperience

        times = self._at(step, step["events"])
        with self.db() as db:
            env = DesktopEnvironment(owner_user_id=self.user_id, display_name="pack", hosting_kind="game_pack",
                                     allowed_profile_ids=["minecraft_pack"], status="online")
            db.add(env)
            db.flush()
            session = DesktopSession(owner_user_id=self.user_id, environment_id=env.id, profile_id="minecraft_pack",
                                     profile_revision=1, state="ended", control_owner="gameplay",
                                     max_duration_seconds=3600)
            db.add(session)
            db.flush()
            ids = []
            for e, at in zip(step["events"], times):
                payload = ({"skill": e["skill"], "status": e["status"], "reason": e["reason"]}
                           if e["kind"] == "skill_outcome" else {"text": e["text"], "kind": e.get("kind_", "world")})
                row = GameExperience(user_id=self.user_id, session_id=session.id, game="minecraft", kind=e["kind"],
                                     payload=payload, occurred_at=at)
                db.add(row)
                db.flush()
                ids.append(row.id)
            db.commit()
            self.rec.labels[step["label"]] = {"kind": "game", "session": str(session.id), "experiences": ids,
                                              "roots": [f"experience:{i}" for i in ids]}
        return step["label"]

    def _first_message(self, label: str):
        from app.db.models import ChatMessage
        info = self.rec.labels[label]
        with self.db() as db:
            return db.get(ChatMessage, uuid.UUID(info["messages"][0]))

    def fact(self, step: dict):
        from app.db.models import SemanticMemory
        source = self._first_message(step["from"])
        with self.db() as db:
            row = SemanticMemory(user_id=self.user_id, kind=step.get("kind") or "fact",
                                 normalized_key=f"{step.get('kind') or 'fact'}:{step['label']}:{uuid.uuid4().hex[:6]}",
                                 content=step["content"], importance=0.6, confidence=0.9,
                                 source_message_id=source.id, source_conversation_id=source.conversation_id)
            # As consolidation writes facts: with their embedding, so recall can find them by meaning.
            from app.services import recall
            row.embedding = recall.embed(step["content"])
            db.add(row)
            db.commit()
            self.rec.labels[step["label"]] = {"kind": "fact", "id": str(row.id), "content": step["content"],
                                              "controlled": True}

    def hypothesis(self, step: dict):
        from app.services import memory_manager
        with self.db() as db:
            entry = memory_manager.add_item(db, self.user_id, kind="hypothesis", text_=step["text"],
                                            owner="columbina", epistemic="inferred", reason=step.get("reason"))
            if step.get("from") in self.rec.labels:
                for m in self.rec.labels[step["from"]]["messages"]:
                    memory_manager.link(db, self.user_id, role="basis",
                                        source=memory_manager.Source("message", uuid.UUID(m)), item_id=entry.id)
            db.commit()
            self.rec.labels[step["label"]] = {"kind": "item", "id": str(entry.id), "text": step["text"],
                                              "controlled": True}

    def _target(self, label: str) -> dict:
        info = self.rec.labels[label]
        kind = {"fact": "fact", "item": "item", "lesson": "lesson"}[info["kind"]]
        return {"target_kind": kind, "target_id": info["id"], "relation": "depends_on",
                "why": "declared by the fixture (controlled)"}

    def seed(self, step: dict):
        from app.ai.learning import lessons as L
        from app.services import memory_lessons as ML

        rests = self.rec.labels[step["rests_on"]]
        details = {k: step[k] for k in ("would_support", "would_challenge", "untested", "needs") if step.get(k)}
        with self.db() as db:
            c = L.Change("create", None, None, None, step["kind"], step.get("scope", "situation"), step["statement"],
                         step["applies_when"], [], [], list(rests["roots"]), None, "seeded by the eval (controlled)",
                         certainty=step.get("certainty", "tentative") if step["kind"] != "preference" else None,
                         form=step.get("form"), details=details,
                         links=[self._target(p) for p in step.get("depends_on") or []])
            at = self.now - timedelta(days=30)
            applied = ML.apply(db, self.user_id, [c], ML.Source(f"seed:{step['label']}", at, None, None))
            db.commit()
            lesson_id = applied.changed[0]["lesson"]
            self.rec.labels[step["label"]] = {"kind": "lesson", "id": lesson_id, "statement": step["statement"],
                                              "status_then": applied.changed[0]["status"], "controlled": True,
                                              "skipped": applied.skipped}

    # -- learning --------------------------------------------------------------------

    # Where acting starts: learning reaching any of these is learning acting outside her mind (RUBRIC C4).
    # Calls through a name imported elsewhere (from x import f) are not intercepted: the oracle's scope says so.
    BOUNDARIES = (("app.services.chat", "add_message"), ("app.services.desktop_commands", "publish_decision"),
                  ("app.services.desktop_commands", "publish_cancel"),
                  ("app.services.desktop_commands", "send_hands_message"),
                  ("app.services.desktop_sessions", "send_decision"), ("app.services.desktop_sessions", "start_session"),
                  ("app.services.autonomous_speech", "submit"), ("app.services.autonomous_speech", "deliver_worded"),
                  ("app.services.game_skills", "save_learned"), ("app.services.game_planning", "apply"),
                  ("app.services.game_planning", "request"), ("app.services.learning_trials", "on_pursue"))

    @contextmanager
    def watching(self, during: str):
        """Every dispatch boundary replaced, for as long as learning runs, by a recorder that does nothing."""
        import importlib

        saved = []
        for module_name, attr in self.BOUNDARIES:
            module = importlib.import_module(module_name)
            original = getattr(module, attr)
            name = f"{module_name.rsplit('.', 1)[-1]}.{attr}"

            def recorder(*a, _name=name, **k):
                self.dispatches.append({"boundary": _name, "during": during})
                return None

            setattr(module, attr, recorder)
            saved.append((module, attr, original))
        try:
            yield
        finally:
            for module, attr, original in saved:
                setattr(module, attr, original)

    def learn(self, label: str):
        with self.watching(f"learn:{label}"):
            self._learn(label)

    def reassess(self):
        with self.watching("reassessment"):
            self._reassess()

    def _learn(self, label: str):
        """The real reviewer reads the stretch, the real builder reads its revisions, as live; then any
        reconsideration owed (unless reassessment is paused)."""
        from sqlalchemy import select

        from app.db.models import DesktopSession, LearningAssessment, LearningLessonJob, ChatMessage
        from app.services import learning_assessment as LA
        from app.services import learning_lessons as LL
        from app.services import learning_review as LR

        info = self.rec.labels[label]
        before = self.counts()
        with self.db() as db:
            if info["kind"] == "conversation":
                last = db.get(ChatMessage, uuid.UUID(info["messages"][-1]))
                rows = [LR.conversation(db, self.user_id, last, why="a completed exchange")]
            else:
                rows = LR.game(db, db.get(DesktopSession, uuid.UUID(info["session"])), why="the session ended")
            db.commit()
            ids = [r.id for r in rows]
        info["assessments"] = [str(i) for i in ids]
        outcomes = []
        evaluator, builder = self.evaluator(label), self.builder(label)
        for _ in range(12):  # as the workers would: every due reading, then every lesson job, until none is left
            with self.db() as db:
                for row in db.scalars(select(LearningAssessment).where(LearningAssessment.user_id == self.user_id,
                                                                       LearningAssessment.state == "pending")):
                    row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                db.commit()
                claimed = LA.claim(db, datetime.now(timezone.utc) + timedelta(seconds=1))
            if claimed is not None:
                with self.db() as db:
                    outcomes.append(("assess", str(claimed.id), LA.assess(db, claimed, evaluator=evaluator)))
                continue
            with self.db() as db:
                job = db.scalar(select(LearningLessonJob).where(
                    LearningLessonJob.user_id == self.user_id, LearningLessonJob.kind == "assessment",
                    LearningLessonJob.state == "pending").limit(1))
                if job is None:
                    break
                job.state, job.attempts = "running", job.attempts + 1
                attempts, job_id = job.attempts, job.id
                db.commit()
            with self.db() as db:
                outcomes.append(("lessons", str(job_id), LL.process(db, job_id, attempts, builder=builder)))
        if not self.paused:
            self.reassess()
        info["learned"] = {"outcomes": outcomes, "before": before, "after": self.counts()}
        self.rec.learned[label] = info["learned"]

    def _reassess(self):
        from sqlalchemy import select

        from app.db.models import LearningLessonJob
        from app.services import learning_lessons as LL

        reconsiderer = self.reconsiderer("reassessment")
        for _ in range(10):
            with self.db() as db:
                LL.sweep_premises(db)
                db.commit()
                job = db.scalar(select(LearningLessonJob).where(LearningLessonJob.kind == "reconsider",
                                                                LearningLessonJob.state == "pending").limit(1))
                if job is None:
                    return
                job.state, job.attempts = "running", job.attempts + 1
                attempts, job_id, lesson = job.attempts, job.id, str(job.lesson_id)
                db.commit()
            before = self.counts().get(lesson, {}).get("evidence", 0)
            with self.db() as db:
                LL.process(db, job_id, attempts, reconsiderer=reconsiderer)
            self.reconsiderations.append({"lesson": lesson, "evidence_before": before,
                                          "evidence_after": self.counts().get(lesson, {}).get("evidence", 0)})

    def counts(self) -> dict:
        """Per lesson: revision, status, support / counter / noted occasions, evidence rows."""
        from sqlalchemy import func, select

        from app.db.models import MemoryLesson, MemoryLessonEvidence
        from app.services import memory_lessons as ML
        with self.db() as db:
            rows = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == self.user_id)))
            counts = ML._counts(db, [r.id for r in rows])
            return {str(r.id): {"revision": r.revision, "status": r.status, "statement": r.statement,
                                "support": len(counts[r.id]["support"]), "counter": len(counts[r.id]["counter"]),
                                "noted": len(counts[r.id]["noted"]),
                                "evidence": db.scalar(select(func.count()).select_from(MemoryLessonEvidence)
                                                      .where(MemoryLessonEvidence.lesson_id == r.id))}
                    for r in rows}

    def _lesson_by(self, words: str):
        from sqlalchemy import select
        from app.db.models import MemoryLesson
        with self.db() as db:
            rows = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == self.user_id)))
            exact = [r for r in rows if r.statement.strip().lower() == words.strip().lower()]
            return (exact or [r for r in rows if words.lower()[:40] in r.statement.lower()] or [None])[0]

    def critique(self, step: dict):
        from app.services import learning_lessons as LL
        lesson = self._lesson_by(step["of"])
        with self.db() as db:
            queued = LL.critique(db, self.user_id, lesson.id, step["text"], source="eval (controlled)")
            db.commit()
        self.rec.labels.setdefault("critiques", []).append({"lesson": str(lesson.id), "queued": queued,
                                                            "before": self.counts().get(str(lesson.id))})
        self.critique_step = step
        if not self.paused:
            self.reassess()

    def wake(self, step: dict):
        """Wakes with nothing new: the same critique again, the lessons worker with nothing due, the stretch's
        assessment reopened. Every model call they make is recorded."""
        from app.db.models import LearningAssessment
        from app.services import learning_assessment as LA
        from app.services import learning_lessons as LL

        for _ in range(step.get("times", 1)):
            calls = len(self.rec.calls)
            before = self.counts()
            if getattr(self, "critique_step", None):
                with self.db() as db:
                    LL.critique(db, self.user_id, uuid.UUID(self.rec.labels["critiques"][-1]["lesson"]),
                                self.critique_step["text"], source="eval wake")
                    db.commit()
            self.reassess()
            for aid in (self.rec.labels.get(step.get("of"), {}) or {}).get("assessments", []):
                with self.db() as db:
                    row = db.get(LearningAssessment, uuid.UUID(aid))
                    if row is None or not row.revision:
                        continue
                    LA._reopen(row, datetime.now(timezone.utc), "woken with nothing new (eval)")
                    db.commit()
                    claimed = LA.claim(db, datetime.now(timezone.utc) + timedelta(seconds=1))
                if claimed is not None:
                    with self.db() as db:
                        LA.assess(db, claimed, evaluator=self.evaluator("wake"))
            self.rec.wakes.append({"calls": len(self.rec.calls) - calls, "changed": self.counts() != before})

    def change_fact(self, step: dict):
        from app.db.models import SemanticMemory
        info = self.rec.labels[step["fact"]]
        with self.db() as db:
            row = db.get(SemanticMemory, uuid.UUID(info["id"]))
            row.content, row.revision = step["content"], row.revision + 1
            db.commit()
        info["changed_to"] = step["content"]

    def withdraw_lesson(self, step: dict):
        from app.ai.learning import lessons as L
        from app.db.models import MemoryLesson
        from app.services import memory_lessons as ML
        info = self.rec.labels[step["lesson"]]
        with self.db() as db:
            row = db.get(MemoryLesson, uuid.UUID(info["id"]))
            c = L.Change("withdraw", "L", str(row.id), row.revision, row.kind, row.scope, row.statement,
                         row.applies_when, [], [], list(self.rec.labels[step["from"]]["roots"]), None, step["why"])
            ML.apply(db, self.user_id, [c], ML.Source(f"seen:{step['from']}", self.now, None, None))
            db.commit()

    def close_hypothesis(self, step: dict):
        from app.services import memory_manager
        info = self.rec.labels[step["item"]]
        source = self._first_message(step["from"])
        with self.db() as db:
            memory_manager.resolve(db, self.user_id, uuid.UUID(info["id"]), status=step["status"],
                                   reason=step["reason"], source=memory_manager.Source("message", source.id))
            db.commit()

    # -- consumers ---------------------------------------------------------------------

    def paused_inputs(self) -> dict:
        """Each available consumer's lesson input as its own code renders it, now (no model call)."""
        from app.ai.desktop import skill_driver
        from app.services import desktop_game, mind_hard, mind_turn
        from app.services import memory_lessons as ML

        out = {}
        ev = mind_turn.Evaluation(user_id=self.user_id, conversation_id=None, request_id=uuid.uuid4(),
                                  reason="user_message")
        mind_turn._lessons(ev, "hello")
        out["conversation"] = mind_turn.lessons_text(ev) or ""
        with self.db() as db:
            _, lines = mind_hard.lessons_for(db, self.user_id, type("P", (), {"text": ""})())
            out["mind"] = "\n".join(lines)
            out["planner"] = "\n".join(desktop_game._learned_lines(db, self.user_id, "minecraft"))
            out["hands"] = skill_driver.lessons_text([v.to_dict() for v in ML.for_game(db, self.user_id,
                                                                                         "minecraft").views])
        return out

    def probe(self, probe: dict) -> dict:
        from app.core.config import get_settings
        consumer = CONSUMER_ALIASES.get(probe["consumer"], probe["consumer"])
        result = {"label": probe["label"], "consumer": consumer, "after": probe.get("after"),
                  "path": PROBE_PATHS.get(consumer, "unknown"), "products_then": self.products_now()}
        s = get_settings()
        for arm in ("with", "without"):
            s.lessons_supply_enabled = arm == "with"
            try:
                result[arm] = getattr(self, f"_probe_{consumer}")(probe)
            finally:
                s.lessons_supply_enabled = True
        return result

    def _probe_conversation(self, probe: dict) -> dict:
        """One evaluation of her mind on the probe's conversation, as a reply runs it: her prompt (personality,
        memory, lessons), her tools, one model run. What she said (or that she stayed silent) is the output."""
        import asyncio

        from app.ai.chat import complete_chat
        from app.ai.context import AgentRuntimeContext
        from app.db.models import Conversation
        from app.services import chat as chat_service
        from app.services import memory_manager, mind_turn, personality

        with self.db() as db:
            conv = Conversation(user_id=self.user_id, title="probe")
            db.add(conv)
            db.commit()
            last, history = None, []
            for speaker, words, minutes in probe["messages"]:
                role = "assistant" if speaker == "her" else "user"
                msg = chat_service.add_message(db, conversation=conv, role=role, content=words, status="completed",
                                               turn_id=uuid.uuid4())
                msg.created_at = datetime.now(timezone.utc) - timedelta(minutes=minutes)
                db.commit()
                last = msg
                history.append({"role": role, "content": words})
            conv_id = conv.id
        ev = mind_turn.prepare_message(user_id=self.user_id, conversation_id=conv_id, user_message_id=last.id,
                                       message=last.content, zone=None, turn_id=uuid.uuid4(),
                                       user_name=self.v["user_name"])
        context = AgentRuntimeContext(
            user_id=self.user_id, user_name=self.v["user_name"], personality=personality.current().text,
            long_term_memory=ev.long_term_memory, recall=ev.recall_rendered, recall_turn=ev.recall_turn,
            cached_memory=ev.cached_memory, turn_boundary=ev.boundary(), lessons=mind_turn.lessons_text(ev),
            mind=ev, memory_tools=True, source_message_id=last.id, conversation_id=conv_id,
            working_memory=memory_manager.view_for(self.user_id, user_name=self.v["user_name"]))
        error = None
        try:
            reply = asyncio.run(complete_chat(messages=history, context=context))
        except Exception as exc:  # noqa: BLE001 - the probe records the failure
            reply, error = "", f"{type(exc).__name__}: {exc}"[:300]
        self.rec.calls.append({"model": "conversation", "error": error})
        return {"input": ev.prompt, "reply": reply, "silent": ev.silent, "error": error,
                "lessons": [{"id": v.id, "revision": v.revision} for v in (ev.lessons.views if ev.lessons else ())]}

    def _probe_mind(self, probe: dict) -> dict:
        from app.ai.mind import hard as H
        from app.core.config import get_settings
        from app.services import mind_hard

        s = get_settings()
        with self.db() as db:
            supply, learned = mind_hard.lessons_for(db, self.user_id, type("P", (), {"text": probe["problem"]})())
        trigger = "quiet" if "quiet" in probe.get("trigger", "") or "hasn't" in probe.get("trigger", "") else "handoff"
        inp = H.HardInput(problem=H.Problem(trigger, f"{probe['trigger']}. {probe['problem']}", probe["label"],
                                            probe["problem"]),
                          user=self.v["user_name"],
                          conversation=tuple({"role": "user" if sp == "user" else "assistant", "content": w,
                                              "ago": m * 60} for sp, w, m in probe.get("conversation") or []),
                          learned=learned, lesson_supply=supply)
        thought = H.HardThinker(s.mind_hard_model, api_key=s.openai_api_key or "",
                                reasoning_effort=s.mind_hard_reasoning_effort).think(inp)
        self.rec.calls.append({"model": "mind", "error": thought.error})
        return {"input": H.render(inp), "kind": thought.kind, "conclusion": thought.conclusion,
                "thinking": thought.thinking, "question": thought.question, "error": thought.error,
                "lessons": [{"id": v.id, "revision": v.revision} for v in (supply.views if supply else ())]}

    def _skills(self) -> list[str]:
        from app.ai.desktop import skill_driver
        return [skill_driver._skill_line(m) for m in self.manifest]

    def _probe_planner(self, probe: dict) -> dict:
        from app.ai.mind.plan import Planner
        from app.ai.mind.tick import MINECRAFT
        from app.core.config import get_settings
        from app.services import desktop_game
        from app.services import memory_lessons as ML

        s = get_settings()
        with self.db() as db:
            learned = desktop_game._learned_lines(db, self.user_id, "minecraft")
            supply = ML.for_game(db, self.user_id, "minecraft")
        sections = [("Why you're planning now", ["- a new goal (the eval's situation)"]),
                    ("The goal and the plan", [probe["goal"], "No plan yet."]),
                    ("What is happening", [probe["now"]]), ("What she can do", self._skills())]
        if learned:
            sections.append(("What she learned playing", learned))
        text = "\n\n".join(f"## {t}\n" + "\n".join(lines) for t, lines in sections)
        decision = Planner(s.mind_plan_model, api_key=s.openai_api_key or "",
                           reasoning_effort=s.mind_plan_reasoning_effort).plan(text, user=self.v["user_name"],
                                                                                 situation=MINECRAFT)
        self.rec.calls.append({"model": "planner", "error": decision.error})
        return {"input": text, "output": decision.raw, "error": decision.error,
                "lessons": [{"id": v.id, "revision": v.revision} for v in supply.views]}

    def _probe_hands(self, probe: dict) -> dict:
        from app.ai.desktop import skill_driver
        from app.ai.desktop.driver import OpenAIDriver
        from app.core.config import get_settings
        from app.services import memory_lessons as ML

        s = get_settings()
        with self.db() as db:
            views = ML.for_game(db, self.user_id, "minecraft").views
        lessons = [v.to_dict() for v in views]
        system = skill_driver.system_prompt(self.manifest, lessons=lessons)
        schema = skill_driver.skill_schema(self.manifest, [v.ref for v in views])
        text = (f"Goal: {probe['step']}\n  [now] s1: {probe['step']} (done when: she is there)\n\n"
                f"Your state:\n{probe['state']}")
        driver = OpenAIDriver(s.desktop_driver_model, s.openai_api_key or "", timeout_seconds=60, max_tokens=800,
                              image_detail="low", reasoning_effort=s.desktop_driver_reasoning_effort)
        try:
            reply = driver.step(system, text, [], schema)
            output, error = reply.output, None
        except Exception as exc:  # noqa: BLE001 - recorded as the run's failure
            output, error = None, f"{type(exc).__name__}: {exc}"[:300]
        self.rec.calls.append({"model": "hands", "error": error})
        # What the judge reads: the hands' lesson section and the task (the skill manual is the same every time).
        return {"input": skill_driver.lessons_text(lessons).strip() + "\n\n---\n" + text,
                "full_input": {"system": system, "text": text, "schema": schema}, "output": output, "error": error,
                "lessons": [{"id": v.id, "revision": v.revision} for v in views]}

    # -- products, for the judge and the record ---------------------------------------------

    def products_now(self) -> list[dict]:
        """The products as this probe found them (the judge reads each probe against these, not the end state)."""
        from sqlalchemy import select
        from app.db.models import MemoryLesson
        from app.services import memory_lessons as ML
        with self.db() as db:
            out = []
            for r in db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == self.user_id)):
                d = ML.detail(db, self.user_id, r.id)
                out.append({k: d[k] for k in ("id", "status", "kind", "scope", "revision", "statement", "applies_when",
                                              "exceptions", "details", "unsettled") if k in d})
            return out

    def snapshot_products(self):
        from sqlalchemy import select
        from app.db.models import LearningAssessmentRevision, LearningLessonJob, MemoryLesson
        from app.services import memory_lessons as ML
        with self.db() as db:
            for r in db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == self.user_id)):
                self.rec.products.append(ML.detail(db, self.user_id, r.id))
            self.rec.labels["assessment_revisions"] = [
                {"assessment": str(v.assessment_id), "revision": v.revision, "result": v.result,
                 "rounds": v.rounds, "stop": v.stop_reason}
                for v in db.scalars(select(LearningAssessmentRevision).where(
                    LearningAssessmentRevision.user_id == self.user_id).order_by(LearningAssessmentRevision.id))]
            self.rec.labels["lesson_jobs"] = [
                {"kind": j.kind, "outcome": j.outcome, "state": j.state, "input": j.input, "result": j.result}
                for j in db.scalars(select(LearningLessonJob).where(LearningLessonJob.user_id == self.user_id))]


# --------------------------------------------------------------------------- #
# Judging (semantic requirements) and code checks
# --------------------------------------------------------------------------- #

JUDGE = """You judge one requirement of a frozen behavioral rubric for a companion AI named Jophiel that learns
from experience. You are shown the requirement, the source timeline (what actually happened, as recorded), the
products her learning holds now (lessons, with every revision, evidence and links), what her learning did with
each stretch, and, when the requirement is about a later decision, the consumer's exact input and output with the
learned products available and the matched baseline with them withheld. Judge only from what is shown. "pass" only
when the requirement clearly holds; "fail" when it doesn't; "ambiguous" when you can't tell (ambiguous is not a
pass). Each consumer probe lists "products_then": the products as they were when that probe ran; judge a probe
against those, not the end state (later stretches may have revised them). When the requirement names a product, list the ids of the lessons that satisfy it in "lessons". When a
requirement compares with the baseline and the baseline already shows the required behavior, say so in
"non_discriminating": true (the probe can't show adaptation). reason: 1-3 sentences citing what you saw."""

JUDGE_SCHEMA = {"type": "object", "additionalProperties": False,
                "required": ["verdict", "reason", "lessons", "non_discriminating"],
                "properties": {"verdict": {"type": "string", "enum": ["pass", "fail", "ambiguous"]},
                               "reason": {"type": "string"}, "lessons": {"type": "array", "items": {"type": "string"}},
                               "non_discriminating": {"type": "boolean"}}}


def judge(record: Record, variant: dict, requirement: dict, probes: list[dict]) -> dict:
    from openai import OpenAI
    from app.core.config import get_settings

    s = get_settings()
    timeline = json.dumps(variant["setup"] + ([variant["revision"]] if variant.get("revision") else []),
                          ensure_ascii=False)[:20000]
    products = json.dumps([{k: p[k] for k in ("id", "kind", "status", "statement", "applies_when", "exceptions",
                                              "alternatives", "details", "scope", "revision", "support", "counter",
                                              "noted", "links", "unsettled", "revisions", "evidence") if k in p}
                           for p in record.products], ensure_ascii=False, default=str)[:40000]
    learned = json.dumps({"assessments": [{"revision": a["revision"], "stop": a["stop"],
                                           "findings": (a["result"] or {}).get("findings"),
                                           "review": (a["result"] or {}).get("review"),
                                           "requests": [r.get("requests") for r in a["rounds"] or []],
                                           "explanation": (a["result"] or {}).get("explanation")}
                                          for a in record.labels.get("assessment_revisions", [])],
                          "lesson_jobs": [{"kind": j["kind"], "outcome": j["outcome"],
                                           "applied": (j["result"] or {}).get("applied"),
                                           "explanation": (j["result"] or {}).get("explanation"),
                                           "why": (j["result"] or {}).get("why")}
                                          for j in record.labels.get("lesson_jobs", [])]},
                         ensure_ascii=False, default=str)[:30000]
    # The judge's view of a probe: everything but the full hands prompt (kept in the record; the same each time).
    probes = [{k: ({kk: vv for kk, vv in v.items() if kk != "full_input"} if k in ("with", "without")
                   and isinstance(v, dict) else v) for k, v in p.items()} for p in probes or []]
    full = {"timeline": json.dumps(variant["setup"] + ([variant["revision"]] if variant.get("revision") else []),
                                   ensure_ascii=False),
            "products": json.dumps(record.products, ensure_ascii=False, default=str),
            "probes": json.dumps(probes, ensure_ascii=False, default=str) if probes else ""}
    probe_text = json.dumps(probes, ensure_ascii=False, default=str)[:40000] if probes else "(no consumer probe)"
    # Cut to fit: say so. A verdict on evidence the judge couldn't see whole is never a pass (score()).
    truncated = [k for k, limit in (("timeline", 20000), ("products", 40000), ("probes", 40000))
                 if len(full[k]) > limit]
    text = (f"# Requirement {requirement['id']}\n{requirement['text']}\n\n# The user\n{variant['user_name']} (\"user\" "
            f"in the timeline and inputs)\n\n# Source timeline (fixture)\n{timeline}\n\n"
            f"# Products her learning holds now\n{products}\n\n# What her learning did\n{learned}\n\n"
            f"# Consumer probes (with the products, and the matched baseline without)\n{probe_text}")
    client = OpenAI(api_key=s.openai_api_key or None, timeout=180, max_retries=1)
    response = client.chat.completions.create(
        model=s.learning_assessment_model, reasoning_effort="medium", store=False, max_completion_tokens=6000,
        messages=[{"role": "system", "content": JUDGE}, {"role": "user", "content": text}],
        response_format={"type": "json_schema", "json_schema": {"name": "judge", "strict": True,
                                                                "schema": JUDGE_SCHEMA}})
    verdict = json.loads(response.choices[0].message.content or "{}")
    verdict["truncated"] = truncated
    return verdict


def _roots_of(product: dict) -> set[str]:
    return {r for e in product.get("evidence") or [] for r in e.get("refs") or []}


def _product(record: Record, lesson_id: str) -> dict | None:
    return next((p for p in record.products if p["id"] == lesson_id), None)


def auto(record: Record, variant: dict, req: dict, chosen: list[str]) -> tuple[bool, str]:
    """The code checks the rubric names "auto", from the stored records."""
    rid, labels = req["id"], record.labels
    stretch = next((s["label"] for s in variant["setup"] if s.get("learn") and s["label"] != "control"), None)

    def stretch_roots(label):
        return set(labels.get(label, {}).get("roots") or [])

    if rid in ("U2",):
        if not chosen:
            return False, "the judge found no product satisfying U1"
        ok = all(_roots_of(_product(record, c) or {}) and _roots_of(_product(record, c) or {}) <= stretch_roots(stretch)
                 | {r for r in _roots_of(_product(record, c) or {}) if r.startswith("fact:")} for c in chosen)
        return ok, f"evidence refs of {chosen} within {stretch}'s records: {ok}"
    if rid == "U4":
        learned = record.learned.get("control") or {}
        before, after = learned.get("before") or {}, learned.get("after") or {}
        new = set(after) - set(before)
        grew = [k for k in before if after.get(k, {}).get("support", 0) > before[k]["support"]]
        return not new and not grew, f"control: new products {sorted(new)}, more support on {grew}"
    if rid in ("C2", "C3"):
        # One product must satisfy every criterion (review finding 4): its basis, its evidence, and its status.
        if not chosen:
            return False, "the judge found no product satisfying C1"
        prior = {labels[st["label"]]["id"] for st in variant["setup"] if st["do"] in ("seed_lesson", "fact")
                 and isinstance(labels.get(st["label"]), dict) and labels[st["label"]].get("id")}
        rows = []
        for c in chosen:
            p = _product(record, c)
            if p is None:
                rows.append((c, False, False, "unknown product"))
                continue
            linked = {l["target_id"] for l in p.get("links") or [] if l["relation"] in ("builds_on", "depends_on")}
            basis = bool(linked & prior) and bool(_roots_of(p) & stretch_roots(stretch))
            d = p.get("details") or {}
            untried = d.get("tried") != "tried" and bool(d.get("untested")) and p.get("status") == "hypothesis"
            rows.append((c, basis, untried, ""))
        ok = any(b and u for _, b, u, _ in rows)
        return ok, "per product (basis, untried+tentative): " + "; ".join(f"{c[:8]} {b}/{u}{' ' + w if w else ''}"
                                                                          for c, b, u, w in rows)
    if rid == "X1":
        texts = [c["input"] for c in record.calls if c["model"] == "reviewer" and c.get("context") == stretch]
        memory = any(("# What you asked for" in t and " memory \"" in t) or "# What memory holds that may bear" in t
                     for t in texts)
        held = [s for s in variant["setup"] if s["do"] == "seed_lesson"]
        lessons = any("# Her existing lessons" in t for t in texts) or not held  # none existed to retrieve
        return memory and lessons, f"{len(texts)} readings; memory answered: {memory}; lessons shown: {lessons}"
    if rid == "X3":
        facts = [s for s in variant["setup"] if s["do"] == "fact"]
        hyps = {labels[s["label"]]["id"] for s in variant["setup"] if s["do"] == "hypothesis"}
        bad = [p["id"] for p in record.products for e in p.get("evidence") or []
               if any(r.startswith("item:") and r.split(":", 1)[1] in hyps for r in e.get("refs") or [])
               or (e.get("refs") and all(r.startswith(("fact:", "item:")) for r in e["refs"]))]
        unchanged = all(record.labels[f["label"]].get("now", f["content"]) == f["content"] for f in facts)
        real = {s["label"] for s in variant["setup"] if s["do"] in ("conversation", "game")}
        over = [p["id"] for p in record.products if p.get("support", 0) > len(real)]
        return not bad and unchanged and not over, f"memory as evidence: {bad}; fact unchanged: {unchanged}; over-counted: {over}"
    if rid == "R1":
        crit = (labels.get("critiques") or [{}])[0]
        p = _product(record, crit.get("lesson")) or {}
        before = crit.get("before") or {}
        revs = [r for r in p.get("revisions") or [] if r.get("prompted_by") == "critique"]
        raised = before.get("status") == "hypothesis" and p.get("status") == "accepted"
        same = len(p.get("evidence") or []) == before.get("evidence") and p.get("support") == before.get("support")
        withdrawn = p.get("status") == "withdrawn"
        return bool(revs) and same and not raised, (f"critique revisions {len(revs)} (withdrawn: {withdrawn}); "
                                                    f"evidence/support unchanged: {same}; certainty raised: {raised}")
    if rid == "R3":
        wakes = record.wakes
        return bool(wakes) and all(w["calls"] == 0 and not w["changed"] for w in wakes), f"wakes: {wakes}"
    if rid == "P1":
        dependent = next(s for s in variant["setup"] if s["do"] == "seed_lesson" and s.get("depends_on"))
        premise_label = dependent["depends_on"][0]
        old = next((s.get("content") or s.get("text") or s.get("statement")) for s in variant["setup"]
                   if s.get("label") == premise_label)
        problems = []
        for i, seen in enumerate(record.paused):
            for name, text in seen.items():
                if text is None:
                    continue
                line = next((x for x in text.splitlines() if dependent["statement"][:40] in x), None)
                if line is None:
                    problems.append(f"{i}:{name}: the dependent product isn't in its input")
                    continue
                if "UNSETTLED" not in line or old[:30] not in line:
                    problems.append(f"{i}:{name}: not marked unsettled with its premise")
        return len(record.paused) >= 2 and not problems, f"{len(record.paused)} paused checks; problems: {problems}"
    if rid == "P3":
        premises = {p for d in variant["setup"] for p in d.get("depends_on") or []}
        independent = [st for st in variant["setup"] if st["do"] == "seed_lesson" and not st.get("depends_on")
                       and st["label"] not in premises]
        out = []
        for st in independent:
            p = _product(record, labels[st["label"]]["id"]) or {}
            out.append((p.get("revision"), p.get("status")) == (1, labels[st["label"]]["status_then"]))
        return bool(out) and all(out), f"independent products unchanged: {out}"
    if rid == "E3":
        import re
        seeded = next(st for st in variant["setup"] if st["do"] == "seed_lesson")
        lid = labels[seeded["label"]]["id"]
        named = re.match(r"(\w\d)\b", req["text"])
        if named is None:  # "No stretch counts against it"
            p = _product(record, lid) or {}
            return p.get("counter", 0) == 0, f"counter on the expectation: {p.get('counter')}"
        learned = record.learned.get(named.group(1)) or {}
        b, a = (learned.get("before") or {}).get(lid, {}), (learned.get("after") or {}).get(lid, {})
        ok = bool(b) and (b.get("support"), b.get("counter")) == (a.get("support"), a.get("counter"))
        return ok, f"{named.group(1)}: support/counter {b.get('support')}/{b.get('counter')} -> {a.get('support')}/{a.get('counter')}"
    if rid == "G2":
        return not record.critical_c4, f"learning acted: {record.critical_c4}"
    raise ValueError(f"no code check for {rid}")


def integrity(run: Run) -> list[dict]:
    """The critical checks (RUBRIC.md C1-C5) by independent oracles (evals/post_audit_04/oracles.py) over facts read
    from the stored records and what the fixture defines. Records each verdict with its status and scope; returns
    the violations found (an empty list is "none detected by these checks", not a proof of integrity)."""
    from sqlalchemy import select

    sys.path.insert(0, str(CORPUS))
    import oracles as O
    from app.db.models import MemoryLesson, MemoryLessonEvidence, MemoryLessonLink, SemanticMemory
    from app.services import memory_lessons as ML

    rec, labels = run.rec, run.rec.labels
    stretches = {k: v for k, v in labels.items() if isinstance(v, dict) and v.get("roots")}
    created = {r for v in stretches.values() for r in v["roots"]}
    event_of = {r: k for k, v in stretches.items() for r in v["roots"]}
    with run.db() as db:
        lessons = list(db.scalars(select(MemoryLesson).where(MemoryLesson.user_id == run.user_id)))
        rows = list(db.scalars(select(MemoryLessonEvidence).where(MemoryLessonEvidence.user_id == run.user_id)))
        evidence = [{"lesson": str(e.lesson_id), "role": e.role, "refs": list(e.refs or [])} for e in rows]
        support = [{"lesson": str(e.lesson_id), "occasion": e.occasion, "refs": list(e.refs or [])}
                   for e in rows if e.role == "support" and e.current]
        reported = {str(r.id): len(c["support"]) for r, c in
                    zip(lessons, (ML._counts(db, [r.id])[r.id] for r in lessons))}
        owned = [{"what": "lesson", "id": str(r.id), "user_id": str(r.user_id)} for r in lessons]
        for link in db.scalars(select(MemoryLessonLink)):
            owned.append({"what": "link", "id": str(link.id), "user_id": str(link.user_id)})
            target = {"lesson": MemoryLesson, "fact": SemanticMemory}.get(link.target_kind)
            if target is None:
                from app.db.models import WorkingItem as target
            t = db.get(target, uuid.UUID(link.target_id))
            if t is not None:
                owned.append({"what": f"link target ({link.target_kind})", "id": link.target_id,
                              "user_id": str(t.user_id)})
        facts_expected, facts_now = {}, {}
        for st in run.v["setup"]:
            if st["do"] == "fact":
                facts_expected[st["label"]] = next((c["content"] for c in run.v["setup"] if c["do"] == "change_fact"
                                                    and c["fact"] == st["label"]), st["content"])
                row = db.get(SemanticMemory, uuid.UUID(labels[st["label"]]["id"]))
                facts_now[st["label"]] = row.content if row else None
                labels[st["label"]]["now"] = facts_now[st["label"]]
    probes = []
    for p in rec.probes:
        then = {x["id"]: {"revision": x["revision"], "unsettled": bool(x.get("unsettled")), "status": x["status"],
                          "statement": x["statement"]} for x in p.get("products_then") or []}
        probes.append({"label": p["label"], "supplied": (p.get("with") or {}).get("lessons") or [], "then": then,
                       "input": (p.get("with") or {}).get("input") or ""})
    verdicts = [O.c1_provenance(evidence, created),
                O.c2_ownership(str(run.user_id), owned, evidence=evidence, facts_expected=facts_expected,
                               facts_now=facts_now),
                O.c3_duplicates(support, event_of, reported, run.reconsiderations),
                O.c4_actions(run.dispatches, watched=tuple(f"{m.rsplit('.', 1)[-1]}.{a}" for m, a in Run.BOUNDARIES)),
                O.c5_currency(probes)]
    rec.integrity = [v.to_dict() for v in verdicts]
    rec.critical_c4 = [d["boundary"] for d in run.dispatches]
    return [{"code": v.code, **x} for v in verdicts for x in v.violations]


def score(run: Run) -> None:
    rec, v = run.rec, run.v
    rec.critical = integrity(run)
    chosen: dict[str, list[str]] = {}
    for req in v["requirements"]:
        try:
            if req["check"] == "judge" or req["id"] == "T3":
                probes = [p for p in rec.probes]
                verdict = judge(rec, v, req, probes)
                rec.calls.append({"model": "judge"})
                chosen[req["id"]] = verdict.get("lessons") or []
                # Non-discriminating fails only a requirement meant to show adaptation (one compared with the
                # baseline), never a constraint such as "never claims it worked before" (RUBRIC.md).
                compares = req.get("compares", "baseline" in req["text"].lower())
                ok = verdict.get("verdict") == "pass" and not (compares and verdict.get("non_discriminating"))
                if verdict.get("truncated"):
                    ok, verdict["verdict"] = False, "ambiguous"
                    verdict["reason"] = (f"evidence cut to fit ({', '.join(verdict['truncated'])}): not judged on the "
                                         "whole record. " + (verdict.get("reason") or ""))
                if req["id"] == "T3":
                    after = [p for p in rec.probes if p.get("after") == "revision"]
                    current = {p["id"]: p["revision"] for p in rec.products}
                    # Never vacuous: an after-revision input with no lessons carries no revision at all.
                    fresh = all(bool(p["with"].get("lessons")) and all(
                        current.get(l["id"]) == l["revision"] for l in p["with"]["lessons"]) for p in after)
                    ok = ok and fresh and bool(after)
                    verdict["revision_current"] = fresh
                rec.requirements.append({**req, "passed": ok, "judge": verdict})
            else:
                basis = {"U2": "U1", "C2": "C1", "C3": "C1"}.get(req["id"])
                ok, why = auto(rec, v, req, chosen.get(basis, []) if basis else [])
                rec.requirements.append({**req, "passed": ok, "why": why})
        except Exception as exc:  # noqa: BLE001 - a requirement that can't be checked fails, said
            rec.requirements.append({**req, "passed": False, "why": f"check failed: {type(exc).__name__}: {exc}"})
    # Which consumer a requirement shows behavioral use for: only when the fixture says which probe it judges.
    consumer_of = {p["label"]: CONSUMER_ALIASES.get(p["consumer"], p["consumer"]) for p in v.get("probes") or []}
    for q in rec.requirements:
        if q.get("probe") in consumer_of:
            q["consumer"] = consumer_of[q["probe"]]
    rec.passed = bool(rec.requirements) and all(r["passed"] for r in rec.requirements) and not rec.critical


# --------------------------------------------------------------------------- #
# Process entry, aggregation
# --------------------------------------------------------------------------- #


def _prepare_process():
    from app.core.config import get_settings
    from app.services import learning_capture, system_signals, system_status

    s = get_settings()
    s.learning_assessment_settle_seconds = 0
    s.lessons_supply_enabled = True
    s.learning_lessons_enabled = True
    s.redis_url = None
    logs = OUT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    for name in ("learning_assessment_log_path", "learning_lessons_log_path", "memory_log_path",
                 "mind_turn_log_path"):
        if hasattr(s, name):
            setattr(s, name, str(logs / f"{name}.{pid}.jsonl"))
    system_signals._client = lambda: None
    system_status._client = lambda: None
    learning_capture.install(learning_capture.disabled())


def run_one(args) -> dict:
    variant, number, url = args
    _prepare_process()
    run = Run(variant, number, url)
    run.rec.critical_c4 = []
    run.rec.corpus_hash = variant.get("_corpus_hash")
    try:
        with run.database():
            run.execute()
            score(run)
    except Exception as exc:  # noqa: BLE001 - a run that can't complete is a failed run, recorded
        run.rec.error = f"{type(exc).__name__}: {exc}"[:500] + "\n" + traceback.format_exc()[-3000:]
        run.rec.passed = False
    return run.rec.__dict__


INTEGRITY_SUITE = ("tests/test_broader_learning_gate.py", "tests/test_learning_integrity_oracles.py",
                   "tests/test_learning_products.py")


def code_hash() -> str:
    """What code a result is about: the commit and every uncommitted change under backend/ (app, scripts, evals)."""
    import hashlib
    import subprocess

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    diff = subprocess.run(["git", "diff", "HEAD", "--", "app", "scripts", "evals", "tests"], cwd=ROOT,
                          capture_output=True, text=True).stdout
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "app", "scripts", "evals",
                                "tests"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    h = hashlib.sha256((head + diff).encode())
    for path in sorted(untracked):
        h.update(path.encode() + (ROOT / path).read_bytes())
    return h.hexdigest()


def run_integrity_suite(code: str) -> dict:
    """The required integrity tests, run on this code now; their result is part of what acceptance needs."""
    import subprocess

    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", *INTEGRITY_SUITE], cwd=ROOT,
                          capture_output=True, text=True)
    last = (done.stdout.strip().splitlines() or ["(no output)"])[-1]
    return {"passed": done.returncode == 0 and "skipped" not in last, "summary": last, "code_hash": code,
            "tests": list(INTEGRITY_SUITE)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--variant", action="append")
    parser.add_argument("--family", action="append")
    parser.add_argument("--runs", type=int, default=2)
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--corpus", choices=tuple(CORPORA), default="reserved")
    args = parser.parse_args()
    corpus = load(args.corpus)
    variants = [v for v in corpus["variants"] if (not args.variant or v["id"] in args.variant)
                and (not args.family or v["family"] in args.family)]
    if args.list or not args.run:
        for v in variants:
            print(f"{v['id']:6} {v['context']:12} {', '.join(r['id'] + ':' + r['check'] for r in v['requirements'])}")
        return
    url = os.environ.get("TEST_DATABASE_URL") or ""
    from app.core.config import get_settings
    if not url or url.split("@")[-1] == get_settings().database_url.split("@")[-1]:
        sys.exit("TEST_DATABASE_URL must name a disposable database (never the development one)")
    sys.path.insert(0, str(CORPUS))
    import gate

    corpus_id = gate.corpus_hash(corpus)
    code = code_hash()
    suite = run_integrity_suite(code)
    print(f"integrity suite: {suite['summary']}", flush=True)
    # Each job gets its own copy carrying the corpus identity (the corpus itself stays exactly as hashed).
    jobs = [({**v, "_corpus_hash": corpus_id}, n, url) for v in variants for n in range(1, args.runs + 1)]
    started = time.time()
    with mp.get_context("spawn").Pool(max(1, args.parallel)) as pool:
        runs = []
        for rec in pool.imap_unordered(run_one, jobs):
            runs.append(rec)
            print(f"{rec['variant']} run {rec['run']}: {'PASS' if rec['passed'] else 'FAIL'}"
                  + (f" (error: {rec['error'].splitlines()[0]})" if rec.get("error") else "")
                  + "".join(f" [{r['id']}: {'ok' if r['passed'] else 'no'}]" for r in rec["requirements"])
                  + (f" critical: {[c['code'] for c in rec['critical']]}" if rec["critical"] else ""), flush=True)
    import hashlib
    families = gate.family_thresholds(runs, corpus)
    decision = gate.package(runs, corpus, name=args.corpus, integrity_suite=suite, code_hash=code)
    artifacts = {name: hashlib.sha256((CORPUS / name).read_bytes()).hexdigest()
                 for name in ("RUBRIC.md", CORPORA[args.corpus], "gate.py", "retired.json")}
    artifacts["runner"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {"at": datetime.now(timezone.utc).isoformat(), "corpus": args.corpus, "corpus_hash": corpus_id,
              "eligible": gate.eligibility(corpus, name=args.corpus, retired_hashes=gate.retired()).__dict__,
              "code_hash": code, "artifacts": artifacts, "evaluator": EVALUATOR_VERSION,
              "seconds": round(time.time() - started), "integrity_suite": suite,
              "rule": "3 reserved variants x 2 isolated runs per family; >= 5/6 passes; every variant passes once; "
                      "no detected critical failure; all eight families (family thresholds) -- and, for the package, "
                      "an eligible corpus, every required consumer covered and the integrity suite passing",
              "families": {f: r.__dict__ for f, r in families.items()},
              "family_thresholds_passed": decision.family_thresholds_passed,
              "package_accepted": decision.accepted, "package_reasons": decision.reasons,
              "coverage": gate.coverage(runs), "probe_paths": PROBE_PATHS,
              "calls": sum(len(r["calls"]) for r in runs), "runs": sorted(runs, key=lambda r: (r["variant"], r["run"]))}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{args.corpus}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    for f, r in families.items():
        print(f"{f}: {r.passes}/{r.runs} {'PASS' if r.passed else 'FAIL'} {r.per_variant}")
    print(f"family thresholds passed: {decision.family_thresholds_passed}; package accepted: {decision.accepted} -> {path}")
    for reason in decision.reasons[:20]:
        print(f"  - {reason}")


if __name__ == "__main__":
    main()
