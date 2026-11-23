"""Affinity B2 (docs/PHONE_PHASE_8_AFFINITY_B2.md): her mind's readings of phone exchanges become shadow relationship
proposals and, through the real learner, shadow lessons bound to their audience; requests for space hold at once; the
third-party writer's input is filtered by audience before anything is read. Fictional exchanges, scripted models.
Real Redis and PostgreSQL (TEST_REDIS_URL, TEST_DATABASE_URL)."""
from __future__ import annotations

import json
import threading
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from app.ai.learning import assessment as A
from app.ai.learning import lessons as L
from app.db.models import (
    CoordinatorWork, LearningAssessment, MemoryLesson, PhoneAffinity, PhoneAffinityShadow, PhoneContactBoundary,
    PhoneConversation, PhoneDisclosure, PhoneExchange, PhoneOutbound, PhonePerson, PhonePersonKey,
    PhoneRelationshipEvidence,
)
from app.services import learning_assessment as LA
from app.services import learning_lessons as LL
from app.services import memory_lessons as ML
from app.services import phone_notifications, phone_people, phone_relationships
from app.services import phone_reply_check as RC
from db_support import db_env, database  # noqa: F401
from test_phone_actions import channel, headers  # noqa: F401
from test_phone_people import _settings, chat, message, owner, report, send  # noqa: F401
from test_phone_phase8 import _forget_this_phone  # noqa: F401

DM_KEY, BAND_KEY = "n_rina", "n_band"


@pytest.fixture
def env(channel, db_env, monkeypatch, tmp_path):
    """The phone channel, her personality, and the learner with its delays off and its logs in tmp."""
    from app.services import personality

    settings = LA.get_settings().model_copy(update={
        "learning_assessment_settle_seconds": 0, "learning_assessment_log_path": str(tmp_path / "assess.jsonl"),
        "learning_lessons_log_path": str(tmp_path / "lessons.jsonl")})
    for module in (LA, LL, ML):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(ML, "_embed", lambda text: None)
    monkeypatch.setattr(personality, "_snapshot", None)
    return channel


def soon(**delta) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**delta)


def rina_dm(*messages: dict, revision: int = 1) -> dict:
    return chat(DM_KEY, "c_rina", "Rina", list(messages), revision=revision)


def band(*messages: dict, revision: int = 1) -> dict:
    return chat(BAND_KEY, "c_band", "Band", list(messages), group=True, revision=revision)


def ref(key: str) -> str:
    return phone_notifications.ref(key)


def msg(conversation: str, event: str) -> str:
    return phone_relationships.event_handle(conversation, event)


def person(env, name: str = "Rina") -> PhonePerson:
    with env.db() as db:
        return db.scalar(select(PhonePerson).where(PhonePerson.display_name == name))


def conversation(env, conversation_id: str) -> PhoneConversation:
    with env.db() as db:
        return db.scalar(select(PhoneConversation).where(PhoneConversation.conversation_id == conversation_id))


def count(env, model, **where) -> int:
    with env.db() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.scalar(q)


class Evaluator:
    name = "assessment:scripted"

    def __init__(self, raw):
        self.raw, self.texts = raw, []

    def __call__(self, text):
        self.texts.append(text)
        raw = self.raw(text) if callable(self.raw) else self.raw
        return A.Answer(json.loads(json.dumps(raw)), 1.0, 10, 5)


class Builder:
    name = "lessons:scripted"

    def __init__(self, answer):
        self.answer, self.texts = answer, []

    def __call__(self, text):
        self.texts.append(text)
        return L.Answer(json.loads(json.dumps(self.answer(text))), 1.0, 10, 5)


def line_ref(text: str, needle: str, prefix: str = "m") -> str:
    return next(line for line in text.splitlines() if needle in line and line.startswith(prefix)).split()[0]


def stated(quote: str):
    """The assessor's reading: the person stated a preference, in these exact words."""
    def raw(text):
        return {"feedback": "yes", "targets": [], "causes": [], "dimensions": [], "choice_vs_outcome": [],
                "user_stated": [{"kind": "preference", "quote": quote, "cites": [line_ref(text, quote)]}],
                "missing": [], "reopens": [], "next": {"step": "conclude", "look": [], "why": "they said it"},
                "explanation": "they asked for one question at a time"}
    return raw


def preference(quote: str, statement: str = "Ask Rina one question at a time"):
    def answer(text):
        return {"changes": [{"op": "create", "lesson": "", "kind": "preference", "scope": "standing",
                             "statement": statement, "applies_when": "when talking with Rina",
                             "exceptions": [], "user_quote": quote, "conclusion_cites": [line_ref(text, quote)],
                             "applicability_cites": [], "conflicts_with": [], "same_as_withdrawn": "",
                             "why": "she said so"}], "explanation": "a stated preference"}
    return answer


def learn(env, evaluator, builder) -> tuple[str, list]:
    """Run the real assessment worker step and the real lesson step once each (scripted models)."""
    with env.db() as db:
        claimed = LA.claim(db, soon(hours=1))
    assert claimed is not None
    with env.db() as db:
        outcome = LA.assess(db, claimed, evaluator=evaluator)
    outcomes = []
    for _ in range(5):
        with env.db() as db:
            job = LL.claim(db, soon(hours=1))
        if job is None:
            break
        with env.db() as db:
            outcomes.append(LL.process(db, *job, builder=builder, embed=lambda text: None))
    return outcome, outcomes


QUESTIONS = "Jophiel, one question at a time please, I get lost otherwise"


def rina_asks_for_one_question(env):
    """Rina's DM: she wrote, Jophiel sent two questions, Rina answers her directly."""
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "hi! Vincent said I can talk to you")))
    send(env, "Hi Rina! What do you play? And how long have you been in the band?")
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "hi! Vincent said I can talk to you"),
                        owner("e_o1", "Hi Rina! What do you play? And how long have you been in the band?"),
                        message("e_r2", "Rina", "p_rina", QUESTIONS), revision=2))


# --------------------------------------------------------------------------- #
# §1: what she can read of a conversation
# --------------------------------------------------------------------------- #

def test_the_conversation_shows_who_wrote_her_confirmed_reply_reply_to_and_the_unknowns(env):
    rina_asks_for_one_question(env)
    report(env, band(message("e_b1", "Rina", "p_rina", "rehearsal at 7"),
                     {**message("e_b2", "Budi", "n_budi", "can't make it", identity="name", in_contacts=False),
                      "reply_to": "e_b1"}))
    dm = phone_relationships.read_conversation(env.user, ref(DM_KEY))
    rina = person(env)
    assert f"[{msg('c_rina', 'e_r2')}]" in dm and f'"Rina" [{phone_people.handle(rina.id)}' in dm
    assert "you (your reply through a reply button, come back)" in dm
    assert "Not known from this: who each message is addressed to" in dm
    assert "What you've sent here through a reply button" in dm
    group = phone_relationships.read_conversation(env.user, ref(BAND_KEY))
    assert f"replying to [{msg('c_band', 'e_b1')}]" in group and "a message may be for anyone there" in group
    assert phone_relationships.read_conversation(env.user, "notif:00000000").startswith("No notification")


# --------------------------------------------------------------------------- #
# Scenario 1: one direct statement supports a DM lesson proposal, absent from live retrieval
# --------------------------------------------------------------------------- #

def test_a_direct_ask_in_a_dm_becomes_a_shadow_lesson_through_the_real_learner(env):
    rina_asks_for_one_question(env)
    said = phone_relationships.note_exchange(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r2")], addressed="you",
        meaning="Rina asked me to ask her one thing at a time", their_words="one question at a time please",
        worth_learning=True)
    assert "goes to your learning" in said, said
    rina = person(env)
    with env.db() as db:
        [exchange] = db.scalars(select(PhoneExchange)).all()
        assert (exchange.addressed, exchange.participated, exchange.lesson) == ("her", True, "assessing")
        row = db.get(LearningAssessment, exchange.assessment_id)
        assert (row.trigger_kind, row.phone_person_id, row.conversation_id) == ("phone", rina.id, None)
    evaluator = Evaluator(stated("one question at a time please"))
    outcome, outcomes = learn(env, evaluator, Builder(preference("one question at a time please")))
    assert outcome == "revised" and outcomes == ["changed"]
    # The learner read only that conversation: her send, Rina's words, nothing of the user's memory.
    [text] = evaluator.texts
    assert '"user" below is Rina, someone writing to her on the user\'s phone' in text
    assert "What do you play?" in text and "one question at a time please" in text
    with env.db() as db:
        [lesson] = db.scalars(select(MemoryLesson)).all()
        dm = conversation(env, "c_rina")
        assert (lesson.status, lesson.kind, lesson.phone_person_id, lesson.phone_conversation_id, lesson.phone_live) \
            == ("accepted", "preference", rina.id, dm.id, False)
        assert lesson.phone_applies_to == [str(dm.id)]
        # Absent from every ordinary retrieval: her mind, hard thoughts, games, the reviewer, the builder.
        assert ML.for_decision(db, env.user, "one question at a time", include_tentative=True).views == ()
        assert ML.for_learning(db, env.user, "one question at a time") == []
        assert ML.for_learning_ids(db, env.user, [lesson.id]) == []
        lessons, _ = ML.shown_for(db, env.user, assessment_id=uuid.uuid4(), assessment_revision=1)
        assert lessons == {}
        # Shadow: not even the reply writer of its own audience reads it yet.
        assert ML.for_phone_audience(db, env.user, rina.id, dm.id) == []
        # Warmth may stay unchanged: nothing was proposed.
        assert db.scalar(select(PhoneAffinity.warmth).where(PhoneAffinity.person_id == rina.id)) == "neutral"
        assert db.get(PhoneAffinityShadow, rina.id) is None
    assert ML.offered(env.user, "one question at a time").views == ()
    with env.db() as db:
        review = phone_relationships.review(db, env.user, rina.id)
    assert [x["live"] for x in review["lessons"]] == [False] and review["assessments"][0]["outcome"] == "concluded"


def test_an_ordinary_reading_never_touches_a_phone_lesson_and_a_phone_reading_only_its_persons(env):
    rina_asks_for_one_question(env)
    rina = person(env)
    dm = conversation(env, "c_rina")
    with env.db() as db:
        c = L.Change("create", None, None, None, "preference", "standing", "Ask one question at a time",
                     "with Rina", [], ["one question at a time please"], ["phone:x"], None, "she said so")
        scope = ML.PhoneScope(rina.id, dm.id)
        applied = ML.apply(db, env.user, [c], ML.Source("phone:x", soon(), None, None, phone=scope))
        lesson_id = applied.lessons[0]
        ordinary = ML.apply(db, env.user, [replace(c, op="support", lesson_id=lesson_id, expected_revision=1)],
                            ML.Source("message:y", soon(), None, None))
        assert ordinary.changed == [] and ordinary.skipped[0]["why"] == "a lesson of another audience"
        db.commit()


# --------------------------------------------------------------------------- #
# Scenario 2: a missed call, a message to the user, an ambiguous addressee
# --------------------------------------------------------------------------- #

def test_a_missed_call_a_message_to_the_user_and_an_ambiguous_one(env):
    missed = {"key": "n_call", "revision": 1, "app": "Phone", "package": "com.android.dialer",
              "title": "Missed call", "text": "Rina", "messages": []}
    report(env, missed, rina_dm(message("e_r1", "Rina", "p_rina", "Vin, are you bringing the amp tonight?"),
                                message("e_r2", "Rina", "p_rina", "who's this?")))
    # A missed call carries no one's words: nothing to rest a reading on.
    assert phone_relationships.note_exchange(
        env.user, ref("n_call"), about=["msg:00000000"], addressed="unclear", meaning="Rina called",
        worth_learning=True).startswith("Not recorded")
    # A message clearly to the user can shape an impression, never familiarity or a personal lesson.
    said = phone_relationships.note_exchange(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], addressed="the_user",
        meaning="Rina is organised about the band's gear", changed={"warmth": "warm", "familiarity": "acquainted"},
        worth_learning=True)
    assert "warmth warm" in said and "Familiarity not proposed" in said and "Nothing to learn" in said
    # Ambiguous: no personal lesson.
    said = phone_relationships.note_exchange(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r2")], addressed="unclear",
        meaning="can't tell who she's asking", worth_learning=True)
    assert "Nothing to learn" in said
    rina = person(env)
    with env.db() as db:
        rows = {x.addressed: x for x in db.scalars(select(PhoneExchange))}
        assert rows["owner"].lesson == rows["unclear"].lesson == "skipped"
        assert db.scalar(select(func.count()).select_from(LearningAssessment)) == 0
        shadow = db.get(PhoneAffinityShadow, rina.id)
        assert (shadow.warmth, shadow.familiarity) == ("warm", None)


def test_her_own_messages_alone_never_make_them_familiar(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "ok")))
    send(env, "Hi Rina, it's Jophiel!")
    # She wrote; Rina's "ok" may have been to the user: not mutual.
    said = phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")],
                                             addressed="unclear", meaning="a short ok",
                                             changed={"familiarity": "acquainted"})
    assert "Familiarity not proposed" in said
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "ok"), message("e_r2", "Rina", "p_rina", "hi Jophiel :)"),
                        revision=2))
    said = phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r2")], addressed="you",
                                             meaning="she greeted me back", changed={"familiarity": "acquainted"})
    assert "familiarity acquainted" in said and "not proposed" not in said


# --------------------------------------------------------------------------- #
# Scenario 3: her words include the band chat
# --------------------------------------------------------------------------- #

def test_a_preference_that_names_the_band_chat_covers_it_and_its_explanation_stays_in_the_dm(env):
    rina_asks_for_one_question(env)
    report(env, band(message("e_b1", "Rina", "p_rina", "rehearsal at 7")))
    words = "one question at a time please, in the band chat too"
    report(env, rina_dm(message("e_r2", "Rina", "p_rina", QUESTIONS),
                        message("e_r3", "Rina", "p_rina", f"I get anxious with lots of questions. {words}"),
                        revision=3))
    said = phone_relationships.note_exchange(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r3")], addressed="you",
        meaning="Rina finds many questions at once stressful", their_words=words, worth_learning=True,
        also_for=["Band", "Climbing"])
    assert "cover 1 other conversation" in said and '"Climbing": not one conversation' in said
    learn(env, Evaluator(stated(words)), Builder(preference(words)))
    rina, dm, group = person(env), conversation(env, "c_rina"), conversation(env, "c_band")
    with env.db() as db:
        [lesson] = db.scalars(select(MemoryLesson)).all()
        assert set(lesson.phone_applies_to) == {str(dm.id), str(group.id)} and lesson.phone_live is False
    # Shadow: no audience reads it.
    with env.db() as db:
        assert ML.for_phone_audience(db, env.user, rina.id, dm.id) == []
        assert ML.for_phone_audience(db, env.user, rina.id, group.id) == []
        db.get(MemoryLesson, lesson.id).phone_live = True  # B3's switch, simulated here
        db.commit()
    # Once live, the band gets the behaviour, never the explanation; the DM gets her words too.
    with env.db() as db:
        [in_dm] = ML.for_phone_audience(db, env.user, rina.id, dm.id)
        [in_group] = ML.for_phone_audience(db, env.user, rina.id, group.id)
    assert words in in_dm.their_words and not in_group.their_words
    assert in_group.statement == "Ask Rina one question at a time"
    band_seen = RC.material_for(env.user, ref(BAND_KEY)).render("hi!")
    assert "anxious" not in band_seen and words not in band_seen
    assert rina.id  # one person in both


# --------------------------------------------------------------------------- #
# Scenario 4: a request for space about the audition
# --------------------------------------------------------------------------- #

def test_a_request_for_space_holds_at_once_survives_a_note_rewrite_and_leaves_other_topics_open(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "please stop asking me about the audition, ok?")))
    assert phone_relationships.note_request_for_space(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], their_words="stop asking me about the audition",
        everywhere=True, topic="the result").startswith("Not recorded: the topic")
    said = phone_relationships.note_request_for_space(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], their_words="please stop asking me about the audition",
        everywhere=True, topic="the audition")
    assert said.startswith("Recorded, and it holds now") and "other topics stay open" in said
    assert phone_relationships.note_request_for_space(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], their_words="please stop asking me about the audition",
        everywhere=True, topic="the audition").startswith("Already noted")
    rina = person(env)
    # A later reading rewrites the note: the request stands.
    phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], addressed="you",
                                      meaning="she's tired of the topic", changed={"note": "Rina's busy lately"})
    with env.db() as db:
        assert db.get(PhoneAffinityShadow, rina.id).note == "Rina's busy lately"
    # No code matches topics: the check on a reply reads the request (their words only where they were said).
    report(env, band(message("e_b1", "Rina", "p_rina", "practice tonight?")))
    assert RC.material_for(env.user, ref(DM_KEY)).requests == [
        'the person she\'s answering asked her to leave the audition alone: "please stop asking me about the audition"']
    assert RC.material_for(env.user, ref(BAND_KEY)).requests == [
        "the person she's answering asked her to leave the audition alone (said elsewhere)"]
    assert "please stop asking me about the audition" in phone_people.read_person(env.user, "Rina")


# --------------------------------------------------------------------------- #
# Scenarios 5 and 6: the check on a reply, a DM and a group with the same person; what the user authorized
# --------------------------------------------------------------------------- #

class Judge:
    """A scripted check model: hands back its verdicts in order, records what it read."""

    def __init__(self, *verdicts):
        self.verdicts, self.seen = list(verdicts), []

    def __call__(self, system, user):
        self.seen.append((system, user))
        return self.verdicts.pop(0)


def _before_draft(user: str) -> str:
    return user.split("HER MESSAGE TO CHECK")[0]


def test_the_check_on_a_group_reply_never_reads_the_dm_and_holds_what_carries_it(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "I'll send you my rehearsal recording, it's my first solo")),
           band(message("e_b1", "Rina", "p_rina", "who's coming saturday?")))
    send(env, "I'd love to hear it!")
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "I'll send you my rehearsal recording, it's my first solo"),
                        owner("e_o1", "I'd love to hear it!"),
                        message("e_r2", "Rina", "p_rina", "haha ok, tomorrow"), revision=2))
    phone_relationships.note_exchange(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], addressed="you",
        meaning="Rina offered to share her rehearsal recording: her first solo",
        their_words="I'll send you my rehearsal recording", changed={"warmth": "fond", "note": "her first solo"})
    group_seen = RC.material_for(env.user, ref(BAND_KEY)).render("I'm in for saturday!")
    for private in ("recording", "solo", "I'd love to hear it", "fond"):
        assert private not in group_seen, private
    assert "who's coming saturday?" in group_seen
    dm = RC.material_for(env.user, ref(DM_KEY))
    assert any("I'd love to hear it!" in line for line in dm.messages)
    assert dm.their_words == [("Rina", "I'll send you my rehearsal recording")]
    # A draft carrying the DM into the group: the check reads the policy and the group's material only.
    judge = Judge(RC.Verdict(send=False, reveals=["her rehearsal recording"], why="it was only said in the DM"))
    held = RC.check_reply(env.user, ref(BAND_KEY), "Rina, how's the recording going?", model=judge)
    assert not held.send and held.reveals == ["her rehearsal recording"] and held.why == "it was only said in the DM"
    [(system, user)] = judge.seen
    assert system == RC.POLICY and "recording" not in _before_draft(user)
    assert "Rina, how's the recording going?" in user
    # Once Rina brings it up in the group herself, the group's material has it.
    report(env, band(message("e_b1", "Rina", "p_rina", "who's coming saturday?"),
                     message("e_b2", "Rina", "p_rina", "also I made a rehearsal recording!"), revision=2))
    ok = RC.check_reply(env.user, ref(BAND_KEY), "Ooh, can't wait to hear it!", model=Judge({"send": True}))
    assert ok.send and any("rehearsal recording" in line for line in ok.material.messages)


def test_a_detail_the_user_authorized_for_rinas_dm_reaches_only_that_audience(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "where's Vincent?")),
           band(message("e_b1", "Rina", "p_rina", "where's Vincent?")))
    said = phone_relationships.authorize_detail(env.user, "Rina", "Vincent will be at the library at seven")
    assert "in their direct chat only" in said
    assert phone_relationships.authorize_detail(env.user, "Sari", "anything").startswith("Not recorded")
    assert RC.material_for(env.user, ref(DM_KEY)).authorized == ["Vincent will be at the library at seven"]
    group = RC.material_for(env.user, ref(BAND_KEY))
    assert group.authorized == [] and "library" not in group.render("ok")
    # A group needs its own say-so.
    assert "group only" in phone_relationships.authorize_detail(env.user, "Rina", "running late", conversation="Band")
    assert RC.material_for(env.user, ref(BAND_KEY)).authorized == ["running late"]


# --------------------------------------------------------------------------- #
# Scenario 7: replay, concurrency, a failed lookup, deletion
# --------------------------------------------------------------------------- #

def test_a_replayed_notification_has_one_effect(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "you're funny")))
    args = dict(about=[msg("c_rina", "e_r1")], addressed="you", meaning="she enjoyed the joke",
                changed={"warmth": "warm"})
    assert "warmth warm" in phone_relationships.note_exchange(env.user, ref(DM_KEY), **args)
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "you're funny"), revision=2))  # the same snapshot again
    assert phone_relationships.note_exchange(env.user, ref(DM_KEY), **args).startswith("Already noted")
    assert count(env, PhoneExchange) == 1 and count(env, PhoneRelationshipEvidence) == 1
    with env.db() as db:
        assert db.get(PhoneAffinityShadow, person(env).id).version == 1


def test_concurrent_readings_of_different_fields_both_stand(env):
    report(env, rina_dm(*(message(f"e_r{i}", "Rina", "p_rina", f"line {i}") for i in range(2))))
    barrier, results = threading.Barrier(2), []

    def note(i: int, field: str, value: str):
        barrier.wait()
        results.append(phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", f"e_r{i}")],
                                                         addressed="you", meaning=f"reading {i}",
                                                         changed={field: value}))

    threads = [threading.Thread(target=note, args=(0, "warmth", "warm")),
               threading.Thread(target=note, args=(1, "initiative", "would_enjoy"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(r.startswith("Noted") for r in results), results
    with env.db() as db:
        shadow = db.get(PhoneAffinityShadow, person(env).id)
        assert (shadow.warmth, shadow.initiative, shadow.version) == ("warm", "would_enjoy", 2)


def test_a_failed_write_or_lookup_leaves_everything_as_it_was(env, monkeypatch):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "you're funny"), message("e_r2", "Rina", "p_rina", "lol")))
    phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], addressed="you",
                                      meaning="she enjoyed the joke", changed={"warmth": "warm"})
    rina = person(env)

    def broken(*args, **kwargs):
        raise OperationalError("UPDATE", {}, Exception("database is down"))

    with monkeypatch.context() as patched:
        patched.setattr(phone_relationships, "_recompute", broken)
        said = phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r2")],
                                                 addressed="you", meaning="more laughing", changed={"warmth": "cool"})
    assert said.startswith("Not recorded") and "Nothing changed" in said
    with env.db() as db:
        assert db.scalar(select(func.count()).select_from(PhoneExchange)) == 1
        assert db.get(PhoneAffinityShadow, rina.id).warmth == "warm"
        assert db.scalar(select(PhoneAffinity.warmth).where(PhoneAffinity.person_id == rina.id)) == "neutral"
        # Who wrote can't be looked up (their key is gone): nothing is created, no defaults.
        db.query(PhonePersonKey).filter(PhonePersonKey.person_id == rina.id).delete()
        db.commit()
    said = phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r2")], addressed="you",
                                             meaning="more laughing", changed={"warmth": "cool"})
    assert "couldn't look them up" in said
    assert count(env, PhonePerson) == 1 and count(env, PhoneAffinity) == 1 and count(env, PhoneExchange) == 1


def test_a_retracted_proposal_stops_counting(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "you're funny"), message("e_r2", "Rina", "p_rina", "ugh")))
    for event, value in (("e_r1", "warm"), ("e_r2", "cool")):
        phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", event)], addressed="you",
                                          meaning=f"reading {event}", changed={"warmth": value})
    rina = person(env)
    with env.db() as db:
        assert db.get(PhoneAffinityShadow, rina.id).warmth == "cool"
        later = db.scalar(select(PhoneRelationshipEvidence).where(PhoneRelationshipEvidence.value == "cool"))
        assert phone_relationships.retract(db, env.user, later.id)
        db.commit()
        assert db.get(PhoneAffinityShadow, rina.id).warmth == "warm"


def test_forgetting_a_person_removes_every_derived_record_and_keeps_her_sent_text(env):
    rina_asks_for_one_question(env)
    phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r2")], addressed="you",
                                      meaning="Rina asked me to ask one thing at a time",
                                      their_words="one question at a time please", changed={"warmth": "warm"},
                                      worth_learning=True)
    phone_relationships.note_request_for_space(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r2")],
                                               their_words="one question at a time please", everywhere=False)
    phone_relationships.authorize_detail(env.user, "Rina", "Vincent says hi")
    learn(env, Evaluator(stated("one question at a time please")), Builder(preference("one question at a time please")))
    rina = person(env)
    assert count(env, MemoryLesson) == 1 and count(env, LearningAssessment) == 1
    with env.db() as db:
        assert phone_people.delete_person(db, env.user, rina.id)
        db.commit()
    for model in (PhoneExchange, PhoneRelationshipEvidence, PhoneAffinityShadow, PhoneContactBoundary,
                  PhoneDisclosure, MemoryLesson, LearningAssessment):
        assert count(env, model) == 0, model.__tablename__
    with env.db() as db:
        assert [o.text for o in db.scalars(select(PhoneOutbound))] == [
            "Hi Rina! What do you play? And how long have you been in the band?"]


def test_codes_and_bank_details_never_reach_a_review_record(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "my code is 4821 9930, can you send it to Vincent")))
    said = phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], addressed="the_user",
                                             meaning="she shared code 4821 9930", their_words="my code is 4821 9930")
    assert "long number" in said
    phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], addressed="the_user",
                                      meaning="she shared code 4821 9930 for Vincent")
    with env.db() as db:
        [x] = db.scalars(select(PhoneExchange)).all()
        assert x.observation == "she shared code [number] for Vincent" and x.their_words is None


# --------------------------------------------------------------------------- #
# Scenario 8: the owner's question while a warmth change is only proposed
# --------------------------------------------------------------------------- #

def test_the_owner_hears_the_active_record_while_warmth_is_proposed_and_nothing_is_scheduled(env):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import deps
    from app.api.routes import memory
    from app.db.models import User

    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "you remembered my gig! that's sweet")))
    phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], addressed="you",
                                      meaning="she was touched I remembered", changed={"warmth": "fond"})
    answer = phone_people.read_person(env.user, "Rina")
    assert "warmth neutral" in answer and "fond" not in answer
    assert count(env, CoordinatorWork) == 0
    app = FastAPI()
    app.include_router(memory.router, prefix="/api")
    with env.db() as db:
        user = db.get(User, env.user)
    app.dependency_overrides[deps.require_owner] = lambda: user
    app.dependency_overrides[deps.get_current_user] = lambda: user
    rina = person(env)
    with TestClient(app) as client:
        review = client.get(f"/api/memory/people/{rina.id}/review").json()
        assert review["active"]["warmth"] == "neutral" and review["shadow"]["warmth"] == "fond"
        [evidence] = review["evidence"]
        assert evidence["observation"] == "she was touched I remembered" and evidence["audience"]["title"] == "Rina"
        assert client.post(f"/api/memory/people/{rina.id}/evidence/{evidence['id']}/retract").status_code == 204
        assert client.get(f"/api/memory/people/{rina.id}/review").json()["shadow"]["warmth"] is None
        assert client.get(f"/api/memory/people/{uuid.uuid4()}/review").status_code == 404


# --------------------------------------------------------------------------- #
# Which turns get which tools; the writer stays off the owner's paths
# --------------------------------------------------------------------------- #

def test_exchange_tools_only_on_turns_someone_elses_words_caused_and_authorization_only_on_the_owners(monkeypatch):
    import asyncio

    from app.ai import chat as ai_chat
    from app.ai.context import AgentRuntimeContext
    from app.ai.tools import phone as tools
    from app.core.config import get_settings

    settings = get_settings().model_copy(update={"redis_url": "redis://unused"})
    monkeypatch.setattr(ai_chat, "get_settings", lambda: settings)
    monkeypatch.setattr(tools, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_chat, "build_server_config", lambda connections: {})
    monkeypatch.setattr(ai_chat, "build_agent", lambda built, **kwargs: built)
    monkeypatch.setattr(ai_chat, "get_agent", lambda *args: [])

    def names(context) -> set[str]:
        async def run():
            async with ai_chat._orchestrator_for(context) as (built, _):
                return {t.name for t in built}
        return asyncio.run(run())

    mind = SimpleNamespace(conversation_id=None, model=None)
    reply = AgentRuntimeContext(uuid.uuid4(), conversation_id=uuid.uuid4(), source_message_id=uuid.uuid4(), mind=mind)
    theirs = replace(reply, source_message_id=None, wake="a notification", third_party=True)
    own = replace(theirs, third_party=False)
    exchange = {"read_phone_conversation", "note_exchange", "note_request_for_space"}
    assert {"read_person", "authorize_detail"} <= names(reply) and not exchange & names(reply)
    assert exchange <= names(theirs) and not {"read_person", "authorize_detail", "phone_action"} & names(theirs)
    assert not exchange & names(own)
    # The check is not a tool, and the owner's chat path doesn't use it (B3's send goes through it).
    assert "phone_reply_check" not in open(ai_chat.__file__).read()


# --------------------------------------------------------------------------- #
# Review fixes (PR 49)
# --------------------------------------------------------------------------- #

def test_a_lesson_learned_in_the_dm_is_never_read_or_revised_from_the_group(env):
    rina_asks_for_one_question(env)
    report(env, band(message("e_b1", "Rina", "p_rina", "rehearsal at 7")))
    rina, dm, group = person(env), conversation(env, "c_rina"), conversation(env, "c_band")
    with env.db() as db:
        c = L.Change("create", None, None, None, "preference", "standing", "Ask one question at a time",
                     "with Rina", [], ["I get lost otherwise"], ["phone:x"], None, "she said so")
        [lesson_id] = ML.apply(db, env.user, [c], ML.Source("phone:x", soon(), None, None,
                                                           phone=ML.PhoneScope(rina.id, dm.id))).lessons
        # A reading of the group sees none of the DM's lessons, and can't revise one.
        shown, _ = ML.shown_for(db, env.user, assessment_id=uuid.uuid4(), assessment_revision=1,
                                phone_person=rina.id, phone_conversation=group.id)
        assert shown == {}
        assert ML.for_learning(db, env.user, "question", phone_person=rina.id, phone_conversation=group.id) == []
        revised = ML.apply(db, env.user, [replace(c, op="revise", lesson_id=lesson_id, expected_revision=1,
                                                  statement="Keep it short in the band chat")],
                           ML.Source("phone:y", soon(), None, None, phone=ML.PhoneScope(rina.id, group.id)))
        assert revised.changed == [] and revised.skipped[0]["why"] == "a lesson of another audience"
        # A phone reading whose conversation is unknown sees nothing at all.
        assert ML.for_learning(db, env.user, "question", phone_person=rina.id) == []
        # Its own conversation's reading still does.
        shown, _ = ML.shown_for(db, env.user, assessment_id=uuid.uuid4(), assessment_revision=1,
                                phone_person=rina.id, phone_conversation=dm.id)
        assert [s.id for s in shown.values()] == [lesson_id]
        db.commit()


def test_an_authorization_for_another_member_of_the_group_isnt_this_replys(env):
    report(env, band(message("e_b1", "Budi", "p_budi", "is Vincent around?"),
                     message("e_b2", "Rina", "p_rina", "where's Vincent?")))
    assert "group only" in phone_relationships.authorize_detail(env.user, "Budi", "at the studio until six",
                                                                conversation="Band")
    answering_rina = RC.material_for(env.user, ref(BAND_KEY))
    assert answering_rina.recipient == "Rina" and answering_rina.authorized == []
    assert "studio" not in answering_rina.render("ok")


def test_the_check_fails_closed_and_never_cuts_a_draft(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "hey")))

    def broken(system, user):
        raise RuntimeError("model down")

    held = RC.check_reply(env.user, ref(DM_KEY), "hey!", model=broken)
    assert not held.send and "couldn't run" in held.why
    assert not RC.check_reply(env.user, ref(DM_KEY), "hey!", model=lambda system, user: {"nonsense": 1}).send
    unused = Judge()
    assert not RC.check_reply(env.user, ref(DM_KEY), "   ", model=unused).send
    long = RC.check_reply(env.user, ref(DM_KEY), "ha " * 1000, model=unused)
    assert not long.send and "longer than" in long.why and unused.seen == []
    assert RC.check_reply(env.user, ref(DM_KEY), "hey!", model=Judge({"send": True})).send


def test_no_content_rule_in_code_the_verdict_decides(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "my pin is 4471")),
           band(message("e_b1", "Rina", "p_rina", "hey all")))
    draft = "your pin is 4471 right?"
    assert RC.check_reply(env.user, ref(BAND_KEY), draft, model=Judge({"send": True})).send
    judge = Judge({"send": False, "reveals": ["a PIN"], "why": "a code from another conversation"})
    held = RC.check_reply(env.user, ref(BAND_KEY), draft, model=judge)
    assert not held.send and held.reveals == ["a PIN"]
    assert "4471" not in _before_draft(judge.seen[0][1])
    assert "PIN" in RC.POLICY and "leave alone" in RC.POLICY  # codes and requests for space: the policy's


def test_a_message_from_before_her_first_reply_there_is_not_an_exchange_she_took_part_in(env):
    early = datetime.now(timezone.utc) - timedelta(hours=2)
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "Jophiel, never send me voice notes", at=early)))
    send(env, "Hi Rina!")  # her first reply there, after Rina's message
    said = phone_relationships.note_exchange(
        env.user, ref(DM_KEY), about=[msg("c_rina", "e_r1")], addressed="you", meaning="no voice notes",
        their_words="never send me voice notes", worth_learning=True)
    assert "you haven't taken part in this conversation" in said
    assert count(env, LearningAssessment) == 0


def test_eligibility_and_the_learners_evidence_use_the_same_replies(env):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "hi")))
    send(env, "Hi Rina!")
    unknown = send(env, "Are you there?", status="unconfirmed")  # not a reply that counts as taking part
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "hi"),
                        message("e_r2", "Rina", "p_rina", "Jophiel, no voice notes please"), revision=2))
    phone_relationships.note_exchange(env.user, ref(DM_KEY), about=[msg("c_rina", "e_r2")], addressed="you",
                                      meaning="no voice notes", their_words="no voice notes please",
                                      worth_learning=True)
    evaluator = Evaluator(stated("no voice notes please"))
    learn(env, evaluator, Builder(preference("no voice notes please")))
    [text] = evaluator.texts
    assert "Hi Rina!" in text and "Are you there?" not in text
    assert "she has written in this conversation 1 time(s)" in text
    with env.db() as db:
        assert db.get(PhoneOutbound, unknown).status == "unknown"


# --------------------------------------------------------------------------- #
# Review fixes (PR 49, the check)
# --------------------------------------------------------------------------- #

def test_a_reply_after_the_user_spoke_in_the_group_answers_no_one_and_inherits_no_authorization(env):
    report(env, band(message("e_b1", "Budi", "p_budi", "is Vincent around?")))
    assert "group only" in phone_relationships.authorize_detail(env.user, "Budi", "at the studio until six",
                                                                conversation="Band")
    assert RC.material_for(env.user, ref(BAND_KEY)).authorized == ["at the studio until six"]
    # The user answers from the phone: the newest message is theirs, so she is answering no one in particular.
    report(env, band(message("e_b1", "Budi", "p_budi", "is Vincent around?"), owner("e_o1", "one sec"), revision=2))
    after = RC.material_for(env.user, ref(BAND_KEY))
    assert after.recipient is None and after.authorized == [] and "studio" not in after.render("ok")


def test_each_noted_quote_keeps_who_said_it(env):
    report(env, band(message("e_b1", "Rina", "p_rina", "I'm bringing snacks"),
                     message("e_b2", "Budi", "p_budi", "I'll drive us there")))
    for event, words in (("e_b1", "I'm bringing snacks"), ("e_b2", "I'll drive us there")):
        phone_relationships.note_exchange(env.user, ref(BAND_KEY), about=[msg("c_band", event)], addressed="everyone",
                                          meaning="a plan for saturday", their_words=words)
    seen = RC.material_for(env.user, ref(BAND_KEY))
    assert set(seen.their_words) == {("Rina", "I'm bringing snacks"), ("Budi", "I'll drive us there")}
    rendered = seen.render("ok")
    assert '- Rina: "I\'m bringing snacks"' in rendered and '- Budi: "I\'ll drive us there"' in rendered


def test_a_failure_reading_the_evidence_holds_the_reply(env, monkeypatch):
    report(env, rina_dm(message("e_r1", "Rina", "p_rina", "hey")))

    def down(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(RC, "_requests", down)
    judge = Judge({"send": True})
    held = RC.check_reply(env.user, ref(DM_KEY), "hey!", model=judge)
    assert not held.send and "couldn't be read" in held.why and judge.seen == []


def test_without_every_time_merged_notifications_answer_no_one(env):
    now = datetime.now(timezone.utc)
    report(env, band(message("e_b1", "Budi", "p_budi", "is Vincent around?", at=now - timedelta(minutes=5))))
    assert "group only" in phone_relationships.authorize_detail(env.user, "Budi", "at the studio until six",
                                                                conversation="Band")
    # A second notification of the same group, Rina's newer message without a time: Budi (timed, earlier) is never
    # taken for the one she's answering.
    report(env, chat(BAND_KEY + "_2", "c_band", "Band", [message("e_b9", "Rina", "p_rina", "where is he?")],
                     group=True))
    seen = RC.material_for(env.user, ref(BAND_KEY))
    assert len(seen.messages) == 2  # both notifications read as one conversation
    assert seen.recipient is None and seen.authorized == []


def test_one_notification_answers_its_last_message_timed_or_not(env):
    now = datetime.now(timezone.utc)
    report(env, band(message("e_b1", "Budi", "p_budi", "is Vincent around?", at=now - timedelta(minutes=5)),
                     message("e_b2", "Rina", "p_rina", "where is he?")))
    assert "group only" in phone_relationships.authorize_detail(env.user, "Budi", "at the studio until six",
                                                                conversation="Band")
    seen = RC.material_for(env.user, ref(BAND_KEY))
    assert seen.recipient == "Rina" and seen.authorized == []


def test_two_people_with_the_same_name_stay_two_people(env):
    report(env, band(message("e_b1", "Rina", "p_rina", "I'm bringing snacks"),
                     message("e_b2", "Rina", "p_rina2", "I'll drive us there")))
    for event, words in (("e_b1", "I'm bringing snacks"), ("e_b2", "I'll drive us there")):
        phone_relationships.note_exchange(env.user, ref(BAND_KEY), about=[msg("c_band", event)], addressed="everyone",
                                          meaning="a plan for saturday", their_words=words)
    seen = RC.material_for(env.user, ref(BAND_KEY))
    speakers = {who for who, _ in seen.their_words}
    assert len(speakers) == 2 and all(who.startswith("Rina (person:") for who in speakers)
    # The same label in the messages, the quotes and the one she's answering.
    assert seen.recipient in speakers and dict((w, who) for who, w in seen.their_words)["I'll drive us there"] == \
        seen.recipient
    assert any(line.endswith(': "I\'ll drive us there"') and seen.recipient in line for line in seen.messages)


def test_a_tie_between_speakers_across_notifications_answers_no_one(env):
    at = datetime.now(timezone.utc) - timedelta(minutes=1)
    report(env, band(message("e_b1", "Budi", "p_budi", "is Vincent around?", at=at)))
    assert "group only" in phone_relationships.authorize_detail(env.user, "Budi", "at the studio until six",
                                                                conversation="Band")
    report(env, chat(BAND_KEY + "_2", "c_band", "Band", [message("e_b9", "Rina", "p_rina", "where is he?", at=at)],
                     group=True))
    for key in (BAND_KEY, BAND_KEY + "_2"):  # whichever notification she names, the read order decides nothing
        seen = RC.material_for(env.user, ref(key))
        assert len(seen.messages) == 2 and seen.recipient is None and seen.authorized == []


def test_labels_use_the_name_this_conversation_shows(env):
    report(env, band(message("e_b1", "Rina", "p_rina", "I'm bringing snacks")))
    report(env, rina_dm(message("e_r1", "Rina Kusuma (work)", "p_rina", "hey")))  # the latest name, shown elsewhere
    assert person(env, "Rina Kusuma (work)") is not None and person(env) is None  # one person, renamed
    phone_relationships.note_exchange(env.user, ref(BAND_KEY), about=[msg("c_band", "e_b1")], addressed="everyone",
                                      meaning="snacks for saturday", their_words="I'm bringing snacks")
    seen = RC.material_for(env.user, ref(BAND_KEY))
    rendered = seen.render("ok")
    assert seen.recipient == "Rina" and seen.their_words == [("Rina", "I'm bringing snacks")]
    assert "Kusuma" not in rendered and "work" not in rendered
