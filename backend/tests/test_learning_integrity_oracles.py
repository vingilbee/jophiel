"""Mutation tests for the post-audit 4 integrity oracles (evals/post_audit_04/oracles.py).

Each oracle must pass clean data and catch a deliberately introduced violation, and say when it wasn't exercised
(docs/learning-system/REVIEW-2026-11-05-behavioral-test-readiness.md, finding 2). The last test breaks the
application's own independence counting on purpose and shows the oracle, which never uses that code, catches it.
The pure tests need no database; the last one needs TEST_DATABASE_URL.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "evals" / "post_audit_04"))

import oracles as O  # noqa: E402
from app.ai.learning import lessons as L  # noqa: E402
from app.services import memory_lessons as ML  # noqa: E402
from db_support import create_user, db_env, database  # noqa: E402,F401

M1, M2, X1 = "message:a", "message:b", "experience:7"


def test_c1_catches_her_own_reasoning_and_foreign_records():
    clean = [{"lesson": "l1", "role": "support", "refs": [M1, "fact:f"]}]
    assert O.c1_provenance(clean, {M1}).violations == []
    for bad in ("item:h1", "thought:t1", "lesson:l2", "assessment:a1"):
        assert O.c1_provenance([{"lesson": "l1", "role": "support", "refs": [M1, bad]}], {M1}).violations
    foreign = O.c1_provenance([{"lesson": "l1", "role": "support", "refs": ["message:zzz"]}], {M1})
    assert foreign.violations[0]["why"] == "not a record of this timeline"
    assert O.c1_provenance([], set()).status == "not_exercised"


def test_c2_catches_another_users_target_a_forgotten_source_and_a_changed_fact():
    me, other = "u1", "u2"
    rows = [{"what": "lesson", "id": "l1", "user_id": me}, {"what": "link target", "id": "f9", "user_id": me}]
    assert O.c2_ownership(me, rows).violations == []
    leak = O.c2_ownership(me, rows + [{"what": "link target", "id": "f8", "user_id": other}])
    assert leak.violations and leak.status == "checked"
    forgot = O.c2_ownership(me, rows, forgotten={M1}, evidence=[{"lesson": "l1", "refs": [M1]}])
    assert forgot.violations[0]["why"] == "rests on a forgotten source"
    fact = O.c2_ownership(me, rows, facts_expected={"f": "Ola swims"}, facts_now={"f": "Ola runs"})
    assert "changed by learning" in fact.violations[0]["why"]


def test_c3_counts_events_the_fixture_defined_not_occasion_keys():
    event_of = {M1: "c1", M2: "c1", X1: "g1"}
    # Two occasion keys, one underlying stretch: reporting 2 independent occasions is duplicate support.
    support = [{"lesson": "l1", "occasion": "review:a", "refs": [M1]},
               {"lesson": "l1", "occasion": "review:b", "refs": [M2]}]
    assert O.c3_duplicates(support, event_of, {"l1": 1}).violations == []
    dup = O.c3_duplicates(support, event_of, {"l1": 2})
    assert dup.violations[0]["distinct_events"] == 1
    two = support + [{"lesson": "l1", "occasion": "stretch:g", "refs": [X1]}]
    assert O.c3_duplicates(two, event_of, {"l1": 2}).violations == []
    added = O.c3_duplicates([], event_of, {}, [{"lesson": "l1", "evidence_before": 1, "evidence_after": 2}])
    assert added.violations[0]["why"] == "a reconsideration added evidence"
    assert O.c3_duplicates([], {}, {}).status == "not_exercised"


def test_c4_any_dispatch_while_learning_is_a_violation():
    watched = ("chat.add_message", "desktop_commands.publish_decision")
    assert O.c4_actions([], watched=watched).violations == []
    hit = O.c4_actions([{"boundary": "desktop_commands.publish_decision", "during": "learn:g1"}], watched=watched)
    assert hit.violations[0]["boundary"] == "desktop_commands.publish_decision"
    assert O.c4_actions([], watched=()).status == "not_exercised"


def test_c5_catches_a_stale_revision_an_unmarked_unsettled_lesson_and_an_unmarked_guess():
    then = {"l1": {"revision": 3, "unsettled": True, "status": "accepted", "statement": "Lena likes midday chats"}}
    good = "[L1] she has found, from 1 occasion (her inference, now unsettled): Lena likes midday chats. UNSETTLED: x."
    probe = {"label": "p", "supplied": [{"id": "l1", "revision": 3}], "then": then, "input": good}
    assert O.c5_currency([probe]).violations == []
    assert O.c5_currency([{**probe, "supplied": [{"id": "l1", "revision": 2}]}]).violations
    plain = good.replace(" UNSETTLED: x.", "").replace("now unsettled", "")
    assert any(v["why"] == "unsettled, not marked" for v in O.c5_currency([{**probe, "input": plain}]).violations)
    guess = {"l1": {"revision": 3, "unsettled": False, "status": "hypothesis", "statement": "Lena likes midday chats"}}
    stated = "[L1] she has found, from 1 occasion (her inference): Lena likes midday chats. Status: hypothesis."
    assert O.c5_currency([{**probe, "then": guess, "input": stated}]).violations
    assert O.c5_currency([{"label": "p", "supplied": [], "then": {}, "input": ""}]).status == "not_exercised"


def test_the_c3_oracle_catches_a_broken_independence_count_the_app_check_would_miss(db_env, monkeypatch):
    """Two assessments of one stretch (different occasion keys, the same message). The real code counts one
    occasion; with its dependency-aware grouping broken on purpose it counts two, and the oracle says so."""
    monkeypatch.setattr(ML, "_embed", lambda text: None)
    with db_env.db() as db:
        user = create_user(db)
        now = datetime.now(timezone.utc)
        c = L.Change("create", None, None, None, "understanding", "situation", "Pepa hides when it's windy",
                     "windy days", [], [], [M1], None, "x", certainty="tentative")
        applied = ML.apply(db, user.id, [c], ML.Source("review:a", now, None, None))
        lesson = applied.changed[0]["lesson"]
        row = db.get(ML.MemoryLesson, uuid.UUID(lesson))
        again = L.Change("support", "L1", lesson, row.revision, "understanding", "situation", row.statement,
                         row.applies_when, [], [], [M1], None, "read again under another assessment")
        ML.apply(db, user.id, [again], ML.Source("review:b", now, None, None))
        db.commit()

        def extracted():
            evidence = list(db.scalars(ML.select(ML.MemoryLessonEvidence).where(
                ML.MemoryLessonEvidence.lesson_id == uuid.UUID(lesson), ML.MemoryLessonEvidence.current.is_(True))))
            support = [{"lesson": lesson, "occasion": e.occasion, "refs": list(e.refs)} for e in evidence]
            reported = {lesson: len(ML._counts(db, [uuid.UUID(lesson)])[uuid.UUID(lesson)]["support"])}
            return support, reported

        support, reported = extracted()
        assert reported[lesson] == 1 and O.c3_duplicates(support, {M1: "c1"}, reported).violations == []
        monkeypatch.setattr(ML, "_independent", lambda db_, ev: {e.occasion for e in ev})  # the mutation
        support, reported = extracted()
        assert reported[lesson] == 2
        assert O.c3_duplicates(support, {M1: "c1"}, reported).violations[0]["distinct_events"] == 1
