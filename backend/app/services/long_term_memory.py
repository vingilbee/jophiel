"""Semantic-memory extraction, persistence, retrieval, and job scheduling.

The public function names remain stable while the existing long-term fact store
is migrated to the more precise ``semantic_memories`` table.
"""
from __future__ import annotations

import logging
import math
import re
import uuid
from datetime import datetime, timedelta, timezone
import secrets

import redis
from openai import OpenAI
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import ChatJob, ChatMessage, Conversation, EpisodicMemory, MemoryJob, SemanticMemory
from app.db.session import SessionLocal
from app.schemas.long_term_memory import MemoryCandidate, MemoryExtraction
from app.services import memory_time
from app.services.heard import heard_text

logger = logging.getLogger(__name__)
_WORD_RE = re.compile(r"[a-z0-9]+")

_EXTRACTION_PROMPT = """You maintain long-term memory for a personal companion and general-purpose assistant.
Extract only durable, future-useful information explicitly stated or corrected by the USER.
Good memories include stable preferences, goals, constraints, explicit personal facts, important
episodes, relationships, ongoing projects, routines, and interaction preferences. Ignore greetings,
one-off questions, assistant suggestions, guesses, transient moods, and facts already represented by
an authoritative application record.
Games: the assistant plays games with the user. Never store the game's state: what the assistant or the
user carries, has, or is doing in the game, where things are in a world, the time of day there. It changes
by the minute and the game records it. One-off game tasks ("collect five oak logs", "build a house", "stop
playing", "follow me") aren't the user's goals: skip them; so are tips on how to do a game action ("you can
break blocks"): the game learns those itself. But do store what lasts about playing together: games the user
plays with the assistant and likes, and standing wishes about how the assistant should be with them while
playing, especially said more than once ("tell me when you find something", "warn me about mobs",
"talk more") as `preference:` items.
The USER text is often a speech transcript and can be misheard: never store a fact from a phrase that doesn't
make sense in context (an odd brand or name in the middle of a game, a fragment), and when unsure store nothing.
EARLIER messages, when given, are context for understanding the latest exchange only: extract from the latest
USER message, and use earlier ones only to see what it refers to or that it repeats an earlier wish.
KNOWN memories, when given, are what is already stored: for the same thing, reuse its normalized_key exactly
(updated wording is fine) instead of making a new key; don't repeat one that adds nothing new.
Use kind `constraint` for what must always be respected whatever the topic: allergies, dietary or medical
restrictions, accessibility needs, hard boundaries the user set; never `fact` for those.
Never infer diagnoses, sensitive traits, relationships, or
emotional conditions. A model interpretation is not a user fact. Use `forget` only when the user
explicitly retracts something or asks for it to be forgotten. Give related facts a stable lowercase
namespaced normalized_key such as `preference:communication_style`, `relationship:sister_name`,
`project:nutrition_app`, `routine:morning_workout`, or `goal:learn_japanese`. A statement overrides
an older one when the user says or plainly means that it changed or was wrong; one that only disagrees with
it is a disagreement (below), not an update. Return no more than 12 items and return an empty list when nothing
deserves durable memory."""


def _client() -> OpenAI:
    settings = get_settings()
    return OpenAI(api_key=settings.openai_api_key) if settings.openai_api_key else OpenAI()


def _embedding(text: str) -> list[float] | None:
    try:
        result = _client().embeddings.create(
            model=get_settings().memory_embedding_model,
            input=text,
        )
        return list(result.data[0].embedding)
    except Exception:
        logger.warning("Memory embedding failed; continuing with lexical retrieval", exc_info=True)
        return None


def schedule_consolidation(
    db: Session, *, user_id: uuid.UUID, conversation_id: uuid.UUID,
    assistant_message_id: uuid.UUID, commit: bool = True,
) -> MemoryJob | None:
    """Create an idempotent durable job and best-effort wake the worker."""
    existing = db.scalar(
        select(MemoryJob).where(MemoryJob.assistant_message_id == assistant_message_id)
    )
    if existing is not None:
        return existing
    job = MemoryJob(
        user_id=user_id,
        conversation_id=conversation_id,
        assistant_message_id=assistant_message_id,
    )
    db.add(job)
    if not commit:
        db.flush()
        return job
    db.commit()
    db.refresh(job)
    settings = get_settings()
    if settings.redis_url:
        try:
            redis.from_url(settings.redis_url).rpush(settings.memory_queue_name, str(job.id))
        except Exception:
            # Postgres is the durable queue; polling will still discover it.
            logger.warning("Could not wake memory worker through Redis", exc_info=True)
    return job


def _turn_for_job(db: Session, job: MemoryJob) -> tuple[str, str, uuid.UUID] | None:
    """Use the durable reply linkage, never a timestamp guess during overlapping turns.

    A chat job without a user source is autonomous and supplies no user facts. Older
    replies without a chat job can still use their shared turn ID; unlinked records
    must fail visibly rather than learn from an unrelated message.
    """
    assistant = db.get(ChatMessage, job.assistant_message_id)
    if (assistant is None or assistant.role != "assistant"
            or assistant.conversation_id != job.conversation_id or assistant.turn_id is None):
        raise ValueError("Memory source reply is missing a valid turn link")
    chat_job = db.scalar(select(ChatJob).where(
        ChatJob.turn_id == assistant.turn_id,
        ChatJob.conversation_id == job.conversation_id,
        ChatJob.user_id == job.user_id,
    ))
    if chat_job is not None:
        if chat_job.user_message_id is None:
            return None
        user_message = db.get(ChatMessage, chat_job.user_message_id)
    else:
        user_message = db.scalar(select(ChatMessage).where(
            ChatMessage.conversation_id == job.conversation_id,
            ChatMessage.role == "user",
            ChatMessage.turn_id == assistant.turn_id,
        ))
    if (user_message is None or user_message.role != "user"
            or user_message.conversation_id != job.conversation_id):
        raise ValueError("Memory source user message is missing or invalid")
    # Learn only from what the user heard: unheard text was never said to them.
    return (user_message.content, heard_text(assistant.content, assistant.heard_chars),
            user_message.id)


EARLIER_MESSAGES = 6  # before the latest exchange, so "yes, always" or a repeated wish reads right


def _earlier_messages(db: Session, conversation_id: uuid.UUID, before_message_id: uuid.UUID) -> list[str]:
    """The few messages before the extracted exchange, oldest first, as "USER: ..." lines. A message something
    forgotten came from is left out (services/memory_forget.py): memory never reads it again."""
    from app.services import memory_forget

    before = db.get(ChatMessage, before_message_id)
    if before is None:
        return []
    rows = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id, ChatMessage.created_at < before.created_at)
        .order_by(ChatMessage.created_at.desc())
        .limit(EARLIER_MESSAGES)
    ).all()
    conversation = db.get(Conversation, conversation_id)
    forgotten = memory_forget.forgotten_messages(db, conversation.user_id, [m.id for m in rows]) if conversation else set()
    lines = []
    for message in reversed(rows):
        if message.id in forgotten:
            continue
        text = heard_text(message.content, message.heard_chars) if message.role == "assistant" else message.content
        text = " ".join(text.split())
        if text:
            lines.append(f"{message.role.upper()}: {text}")
    return lines


KNOWN_SHOWN = 60  # stored memories shown to the extractor, so the same thing keeps its key
# ...plus older ones the message is about (its words, or an entity it mentions), so an old fact outside the
# recent window is still found and matched instead of stored again.
KNOWN_MATCHED = 20
KNOWN_PLANS = 12


def _known_facts(db: Session, user_id: uuid.UUID, text: str = "",
                 entity_ids: set[uuid.UUID] = frozenset()) -> list[SemanticMemory]:
    from app.services import memory_entities

    rows = list(db.scalars(
        select(SemanticMemory)
        .where(SemanticMemory.user_id == user_id, SemanticMemory.status == "active")
        .order_by(SemanticMemory.last_confirmed_at.desc())
        .limit(2000)
    ).all())
    recent, older = rows[:KNOWN_SHOWN], rows[KNOWN_SHOWN:]
    words = memory_entities.bases(text)
    if not (words or entity_ids) or not older:
        return recent
    matched = [f for f in older if f.entity_id in entity_ids or (
        words & memory_entities.bases(f"{f.content} {f.value or ''}"))]
    return recent + matched[:KNOWN_MATCHED]


def _claim_tag(fact: SemanticMemory, entity_refs: dict[uuid.UUID, str]) -> str:
    """How a claim is shown to the extractor: who it's about, which detail, in what respect, and when. Older
    notes (no attribute) have no tag."""
    if not fact.attribute:
        return ""
    parts = [entity_refs.get(fact.entity_id, "another entity") if fact.entity_id else "the user", fact.attribute]
    if fact.qualifier:
        parts.append(fact.qualifier)
    if fact.historical:
        parts.append("past")
    if fact.time_words:
        parts.append(f'when: "{fact.time_words}"')
    return " [" + " · ".join(parts) + "]"


def _known(db: Session, user_id: uuid.UUID, facts: list[SemanticMemory] | None = None,
           entity_refs: dict[uuid.UUID, str] | None = None) -> list[str]:
    """The stored facts shown to the extractor, each with its claim tag and its unresolved disagreements, if
    any."""
    from app.services import memory_conflicts

    facts = _known_facts(db, user_id) if facts is None else facts
    unresolved = memory_conflicts.open_for(db, user_id, [m.id for m in facts])
    return [f"{m.normalized_key}{_claim_tag(m, entity_refs or {})}: {' '.join(m.content.split())}"
            f"{memory_conflicts.known_suffix(unresolved.get(m.id, []))}" for m in facts]


def plan_key(entry) -> str:
    return f"plan:{entry.id.hex[:8]}"


def _known_plans(db: Session, user_id: uuid.UUID) -> list:
    from app.db.models import WorkingItem

    return list(db.scalars(select(WorkingItem).where(
        WorkingItem.user_id == user_id, WorkingItem.kind == "plan", WorkingItem.closed_at.is_(None))
        .order_by(WorkingItem.updated_at.desc()).limit(KNOWN_PLANS)))


def _plan_lines(plans: list) -> list[str]:
    out = []
    for entry in plans:
        when = ""
        if entry.time_words:
            bounds = (f" = {entry.time_start.date()} to {(entry.time_end - timedelta(days=1)).date()}"
                      if entry.time_start and entry.time_end else "")
            when = f'; when: "{entry.time_words}"{bounds}'
        out.append(f"{plan_key(entry)} [{entry.status}{when}]: {' '.join(entry.text.split())}")
    return out


def _moment(db: Session, job: MemoryJob, message_id: uuid.UUID) -> tuple[datetime, str | None]:
    """When the user's message was said, and the time zone their client reported for that turn (None when it
    didn't: never the server's own)."""
    message = db.get(ChatMessage, message_id)
    said = message.created_at if message is not None else datetime.now(timezone.utc)
    said = said if said.tzinfo else said.replace(tzinfo=timezone.utc)
    zone = None
    if message is not None and message.turn_id is not None:
        chat_job = db.scalar(select(ChatJob).where(ChatJob.turn_id == message.turn_id,
                                                   ChatJob.user_id == job.user_id))
        zone = (chat_job.payload or {}).get("zone") if chat_job is not None else None
    return said, zone if isinstance(zone, str) and zone else None


_CORRECTIONS_PROMPT = """
Corrections: also list in `corrections` each place where the latest USER message explicitly corrects or
updates something said or recorded earlier, by the user, by the assistant, or in KNOWN ("actually it's X,
not Y", "I changed X to Y", "no, I said X"). Not a fact stated for the first time, not a question, not the
assistant correcting itself, and not a newer statement that only adds to an older one. subject: what it's
about, in the user's words; attribute: which detail of it, if said; previous_value: what was said or
recorded before, only when the USER message, EARLIER or KNOWN show it, else null (never guess);
new_value: what holds now; user_words: the words of the correction, copied exactly from the latest USER
message. kind: "changed" when the user says the thing itself changed (the old value was true before);
"mistaken" when the user says the earlier statement or record was wrong (misspoke, misheard, got it
wrong); "unspecified" when they don't say which. Store the corrected fact in `memories` as usual too,
reusing the KNOWN key for the same thing."""

_CONFLICTS_PROMPT = """
Disagreements: check every latest USER message against KNOWN, even a passing remark with nothing worth
storing. Each person, pet, place, name or preference it mentions: does what it says, or takes for granted,
disagree with what KNOWN states about the same thing? For example, KNOWN says their uncle lives in Denver and
the message says "my uncle up in Seattle"; KNOWN says the parrot is called Kiwi and the message calls it
Mango; KNOWN says Priya can't stand olives and the message says she ate a whole bowl of them. The user hasn't said it
changed or that the memory was wrong. It may be a slip, a mishearing, a visit, a change nobody mentioned, or
a new name: you can't tell, so don't decide, and don't explain it away. Words that pick someone out by a
place, a name or a time ("my cousin in Rome", "her flat in Kyoto", "my husband Raj", "my 7am run") say that
about them, even in passing; when that disagrees with KNOWN it counts, even if a visit or a nickname could
explain it: explaining it is someone else's job. List it in `conflicts`: known_key: the KNOWN key, exactly;
stated: what the message says about it, in a few words; user_words: the words, copied exactly from the latest
USER message. Then leave that memory out of `memories` (never reword it) and out of `corrections`.
Not a disagreement: a detail KNOWN doesn't mention, something that can be true alongside it (a trip to
another city), or a change the user states or explains ("she moved", "actually it's X", "not anymore"),
which is a correction or an update as above.
A KNOWN line can end with [unresolved: ...]: an earlier disagreement. When the latest USER message settles it
(says which holds, or explains it), list that key in `settled`. If the memory holds as it was, leave it out
of `memories` and `corrections`; only if the new version holds, store it in `memories` (and `corrections`)
as usual."""


_FORGET_PROMPT = """
Forgetting: when the latest USER message asks the assistant to forget, delete or not keep something they told
it ("forget what I told you about my knee", "please don't remember that"), list it in `forget`: about: what to
forget, in a few words of theirs; user_words: the request, copied exactly from the latest USER message. A wish
about the future ("don't keep health stuff like that") is also a preference to store as usual, but `forget`
covers only what they asked to forget now. Not a correction (that's above), and not "I forgot" about something
in their own life."""


_CLAIMS_PROMPT = """
Claims: each memory is one claim: one detail about one person, pet, place or thing. "My cat Pixel hides from the
vacuum" is two claims: the cat's name (attribute `name`, value `Pixel`) and what it fears (attribute `fears`,
value `the vacuum`). For each give `entity`, who or what it's about (null when it's about the user themselves):
ref is its KNOWN ENTITIES id only when the message makes clear it's that one (it names it, says what it is to the
user, or plainly goes on talking about it); otherwise ref null, with kind, name and relation (what it is to the
user, without "user's" or "my": "sister", "cat", "childhood dog", "grandpa's cat") for a new one. The same name
alone isn't enough ("my coworker Ana" is not "sister Ana"). `attribute`: which detail, short and lowercase
("name", "lives_in", "fears", "schedule", "diet"); `value`: its value in a few words; `qualifier`: in what respect,
only if said ("as a child", "at work"); `historical`: true when it tells how things were, not how they are ("she
lived in Leeds for ten years", "I had a hamster as a kid"): it stays true when things change. Give each claim its
own key (`pet:cat:name`, `pet:cat:fears_vacuum`); two values of one detail (two siblings, two fears) are two claims
with two keys.
A KNOWN line tagged [...] is a claim (its entity, attribute, and when). A KNOWN line with no tag is an older note
and may hold several claims: when the latest USER message changes one detail of it, keep the note's key for the
claim that changed, and store every other claim it holds as its own claim, content unchanged, with a new key and
`split_from` set to the note's key. Nothing a note said is lost unless the user changed it.
Claims stay with who or what they're about. When a correction says a name was wrong ("my cat is Pixel, not Bolt;
Bolt was my grandpa's cat"), the note was about the same one under its right name: every other claim it made
(hiding from the vacuum) stays with that one, now called Pixel, and the old name goes to the other one only with
what the message itself says about it (Bolt was the grandpa's cat, nothing more)."""

_TIME_PROMPT = """
Time: SAID is when the latest USER message was said, on the user's calendar. When a memory says when something
happened, will happen, or became true ("last month", "in December", "on Friday", "Thursdays at 7pm"), give
`time`: words: the time words copied exactly from the latest USER message; start and end: the first and last day
they mean (YYYY-MM-DD), counted from SAID, never from today ("last month" said in October 2026 is 2026-09-01 to
2026-09-30); precision: day, days (a few days: "this weekend"; or around an approximate day, "two weeks ago",
"a few days back": the few days it could be), week, month, season or year; year_inferred: true
when the year wasn't said and you chose it ("in December": the next December). A schedule that repeats is
precision `repeating` with repeats, weekdays and local_time (24h), and no dates. When the words can't be placed
("soon", "a while back", "someday") or more than one reading is plausible, precision `unknown` and no dates:
never invent a date. No time words: no `time`."""

_PLANS_PROMPT = """
Plans: something the user means to do ("I'm visiting Nora in Lisbon in December", "we're going camping next
weekend") is kind `plan`, not an episode or a fact: content: the plan in one sentence; user_words: the words of the
plan, copied exactly from the latest USER message; time as above; entity: who it's mainly with or about;
participants: the other people and places it involves. KNOWN PLANS are plans already stored: for the same plan
reuse its key exactly, with the new content and time when they changed, and set plan_status `happened` or
`cancelled` when the user says it happened or is off. What already happened is an episode (with its time)."""


def _prompt() -> str:
    return (_EXTRACTION_PROMPT + _CLAIMS_PROMPT + _TIME_PROMPT + _PLANS_PROMPT + _CORRECTIONS_PROMPT
            + _CONFLICTS_PROMPT + _FORGET_PROMPT)


def _extract(user_text: str, assistant_text: str, earlier: list[str] = (), known: list[str] = (), *,
             entities: list[str] = (), plans: list[str] = (), said: str = "") -> MemoryExtraction:
    context = (said + "\n\n") if said else ""
    context += ("KNOWN ENTITIES:\n" + "\n".join(entities) + "\n\n") if entities else ""
    context += ("KNOWN:\n" + "\n".join(known) + "\n\n") if known else ""
    context += ("KNOWN PLANS:\n" + "\n".join(plans) + "\n\n") if plans else ""
    context += ("EARLIER (context only):\n" + "\n".join(earlier) + "\n\n") if earlier else ""
    response = _client().responses.parse(
        model=get_settings().memory_model,
        input=[
            {"role": "system", "content": _prompt()},
            {
                "role": "user",
                "content": f"{context}USER:\n{user_text}\n\nASSISTANT:\n{assistant_text}",
            },
        ],
        text_format=MemoryExtraction,
    )
    parsed = response.output_parsed
    if response.status != "completed" or parsed is None:
        # The existing job retry/failure path records this, without logging private
        # response content. A valid parsed empty extraction still succeeds.
        raise ValueError("Memory extraction did not return a completed structured result")
    return parsed


def _said_after(db: Session, first: uuid.UUID, second: uuid.UUID) -> bool:
    """Whether message ``first`` was said after message ``second`` (False when either is gone)."""
    rows = {m.id: m for m in db.scalars(select(ChatMessage).where(ChatMessage.id.in_((first, second))))}
    a, b = rows.get(first), rows.get(second)
    if a is None or b is None:
        return False
    if a.conversation_id == b.conversation_id and a.conversation_sequence and b.conversation_sequence:
        return a.conversation_sequence > b.conversation_sequence
    return a.created_at > b.created_at


def _corrects(extraction: MemoryExtraction, before: str, after: str) -> bool:
    """Whether this new wording of a fact is one of the turn's corrections: the old value goes, the new
    one comes."""
    old_words, new_words = set(_WORD_RE.findall(before.lower())), set(_WORD_RE.findall(after.lower()))
    for c in extraction.corrections:
        previous, new = set(_WORD_RE.findall((c.previous_value or "").lower())), set(_WORD_RE.findall(c.new_value.lower()))
        if previous and new and previous <= old_words and new <= new_words and not new <= old_words:
            return True
    return False


HISTORY_KEPT = 10  # earlier wordings kept on a fact


def _keep_history(fact: SemanticMemory, *, by_message_id: uuid.UUID | None, reason: str,
                  correction_id: uuid.UUID | None = None) -> None:
    """Keep the wording a fact is about to lose, with where it came from (never just overwrite it)."""
    entry = {"content": fact.content, "revision": fact.revision,
             "source_message_id": str(fact.source_message_id) if fact.source_message_id else None,
             "replaced_at": datetime.now(timezone.utc).isoformat(), "replaced_by_message_id":
             str(by_message_id) if by_message_id else None, "reason": reason}
    if correction_id is not None:
        entry["correction_id"] = str(correction_id)
    meta = dict(fact.metadata_ or {})
    meta["history"] = ([*meta.get("history", []), entry])[-HISTORY_KEPT:]
    fact.metadata_ = meta  # reassign: JSONB doesn't track in place


def _named_in(content: str, user_text: str) -> bool:
    """The user's message shares a content word with this memory: it can be what they asked to forget."""
    from app.services import memory_forget

    return bool(memory_forget._bases(content) & memory_forget._bases(user_text))


def _plan_words(candidate: MemoryCandidate, user_text: str) -> str:
    """A plan's own words: the ones the model copied, when they really are in the message; else the message."""
    words = " ".join((candidate.user_words or "").split())
    if words and memory_time._said(words, user_text):
        return words[:500]
    return " ".join(user_text.split())[:500]


def _keep_plan(db: Session, job: MemoryJob, candidate: MemoryCandidate, key: str, entity, resolver, known_plans: list,
               user_text: str, message_id: uuid.UUID, said_at: datetime, zone: str | None) -> None:
    """A plan is one working item (its canonical form): the same plan again (its key, or the same wording) is
    revised, and a new one is added with its words, time and who it involves. Happened or off closes it."""
    from app.services import memory_entities, memory_manager

    source = memory_manager.Source("message", message_id)
    when = memory_time.ground(candidate.time, user_text=user_text, said_at=said_at, zone=zone, role="future")
    text_ = " ".join(candidate.content.split())
    same = next((p for p in known_plans if plan_key(p) == key), None) or next(
        (p for p in known_plans if " ".join(p.text.lower().split()) == text_.lower()), None)
    try:
        if same is not None:
            if candidate.plan_status in ("happened", "cancelled"):
                memory_manager.resolve(db, job.user_id, same.id, status=candidate.plan_status,
                                       reason="the user said so", source=source)
                return
            memory_manager.revise_plan(db, job.user_id, same, text_=text_, user_words=_plan_words(candidate, user_text),
                                       time_values=when, source=source, role="restated")
            entry = same
        elif candidate.plan_status != "planned":
            return  # about a plan never stored: nothing to close
        else:
            entry = memory_manager.add_plan(db, job.user_id, text_, user_words=_plan_words(candidate, user_text),
                                            time_values=when, source=source)
            known_plans.append(entry)
    except memory_manager.MemoryError as exc:
        logger.info("Memory job %s: plan %s not kept: %s", job.id, key, exc.message)
        return
    for mention, role in [(candidate.entity, "about"), *((m, "participant") for m in candidate.participants)]:
        linked = entity if mention is candidate.entity else resolver.resolve(mention)
        if linked is not None:
            memory_entities.link(db, job.user_id, linked, item_id=entry.id,
                                 role="place" if linked.kind == "place" else role)


def process_job(db: Session, job_id: uuid.UUID) -> bool:
    """Atomically claim and process one job, reclaiming expired leases safely."""
    now = datetime.now(timezone.utc)
    claim_token = secrets.token_urlsafe(32)
    job = db.scalar(
        select(MemoryJob).where(
            MemoryJob.id == job_id,
            or_(
                MemoryJob.status == "pending",
                (MemoryJob.status == "processing")
                & (MemoryJob.lease_expires_at.is_(None) | (MemoryJob.lease_expires_at <= now)),
            ),
        ).with_for_update(skip_locked=True)
    )
    if job is None:
        return False
    job.status = "processing"
    job.attempts += 1
    job.claim_token = claim_token
    job.lease_expires_at = now + timedelta(minutes=15)
    db.commit()
    try:
        turn = _turn_for_job(db, job)
        from app.services import memory_conflicts, memory_forget

        if turn is not None and memory_forget.forgotten_messages(db, job.user_id, [turn[2]]):
            # Something from this message was forgotten (a late or retried job): it is never read again.
            logger.info("Memory job %s: its message was forgotten; nothing extracted", job_id)
            turn = None
        earlier = _earlier_messages(db, job.conversation_id, turn[2]) if turn else []

        from app.services import memory_entities

        context_text = " ".join([turn[0], *earlier]) if turn else ""
        entities = memory_entities.known(db, job.user_id, context_text) if turn else []
        by_ref = memory_entities.refs(entities)
        entity_refs = {e.id: ref for ref, e in by_ref.items()}
        shown = _known_facts(db, job.user_id, turn[0] if turn else "",
                             {e.id for e in entities if memory_entities.mentioned(e, context_text)})
        known = _known(db, job.user_id, shown, entity_refs)
        plans = _known_plans(db, job.user_id) if turn else []
        said_at, zone = _moment(db, job, turn[2]) if turn else (datetime.now(timezone.utc), None)
        extraction = _extract(turn[0], turn[1], earlier, known, entities=memory_entities.lines(by_ref),
                              plans=_plan_lines(plans), said=memory_time.anchor(said_at, zone)) if turn else MemoryExtraction()
        candidates = extraction.memories
        source_message_id = turn[2] if turn else None
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        shown_by_key = {m.normalized_key: m for m in shown}
        resolver = memory_entities.Resolver(db, job.user_id, by_ref, context_text, source_message_id)
        # A fact this message only disagrees with is kept as it was: the disagreement is recorded, not applied.
        disputed = memory_conflicts.record(
            db, job=job, message_id=source_message_id, user_text=turn[0], shown=shown_by_key,
            conflicts=extraction.conflicts) if extraction.conflicts and turn is not None else set()
        revised: list[tuple[SemanticMemory, str, int]] = []  # (fact, its old wording, its old revision)
        forget_keys: list[str] = []
        updated_keys = {c.normalized_key.strip().lower() for c in candidates if c.operation == "upsert"}
        split_notes: dict[str, list[uuid.UUID]] = {}
        # Older notes (no attribute) as they were before this turn changed anything: a claim split out of one
        # keeps the note's own source and time, even when the note itself is updated earlier in the loop.
        notes = {m.normalized_key: {"id": m.id, "source": m.source_message_id, "created_at": m.created_at,
                                    "content": m.content, "revision": m.revision}
                 for m in shown if m.attribute is None and m.status == "active"}
        for candidate in candidates:
            key = candidate.normalized_key.strip().lower()
            existing = db.scalar(
                select(SemanticMemory).where(
                    SemanticMemory.user_id == job.user_id,
                    SemanticMemory.normalized_key == key,
                )
            )
            if candidate.operation == "forget":
                if existing is not None and turn is not None and _named_in(existing.content, turn[0]):
                    # Hidden now; removed with the request (services/memory_forget.py). Only a fact the message
                    # names: a broad wish never takes a fact it doesn't mention.
                    existing.status = "deleted"
                    existing.last_confirmed_at = now
                    existing.revision += 1
                    memory_conflicts.settle(db, existing, "forgotten", message_id=source_message_id)
                    forget_keys.append(key)
                continue
            if key in disputed:
                logger.info("Memory job %s: %s is disputed by this message; left as it was", job_id, key)
                continue
            if turn is None:
                continue
            entity = resolver.resolve(candidate.entity)
            if candidate.kind == "plan":
                _keep_plan(db, job, candidate, key, entity, resolver, plans, turn[0], source_message_id, said_at, zone)
                continue
            vector = _embedding(candidate.content)
            if candidate.kind == "episode":
                when = memory_time.ground(candidate.time, user_text=turn[0], said_at=said_at, zone=zone, role="past")
                episode = EpisodicMemory(
                    id=uuid.uuid4(),
                    user_id=job.user_id,
                    conversation_id=job.conversation_id,
                    event_at=when.get("time_start") or said_at,
                    summary=candidate.content.strip(),
                    source_message_ids=[str(source_message_id)] if source_message_id else [],
                    embedding=vector,
                    importance=candidate.importance,
                    confidence=candidate.confidence,
                    context={"normalized_key": key},
                    **when,
                )
                db.add(episode)
                db.flush()
                for mention, role in [(candidate.entity, "about"), *((m, "participant") for m in candidate.participants)]:
                    linked = entity if mention is candidate.entity else resolver.resolve(mention)
                    if linked is not None:
                        memory_entities.link(db, job.user_id, linked, episode_id=episode.id,
                                             role="place" if linked.kind == "place" else role)
                continue
            if (existing is not None and source_message_id is not None and existing.source_message_id
                    and _said_after(db, existing.source_message_id, source_message_id)):
                # A job that ran late never overwrites what the user said after it.
                logger.info("Memory job %s: %s already holds a later statement", job_id, key)
                continue
            when = memory_time.ground(candidate.time, user_text=turn[0], said_at=said_at, zone=zone, role="any")
            note_key = (candidate.split_from or "").strip().lower()
            note = notes.get(note_key)
            split = existing is None and note is not None and note_key != key
            claim = {"entity_id": entity.id if entity is not None else None,
                     "attribute": (candidate.attribute or "").strip().lower() or None,
                     "value": (candidate.value or "").strip() or None,
                     "qualifier": (candidate.qualifier or "").strip() or None,
                     # Only ever set by the model saying so: leaving it out never makes a past claim current.
                     "historical": True if candidate.historical else None}
            if existing is None:
                existing = SemanticMemory(id=uuid.uuid4(), user_id=job.user_id, normalized_key=key)
                db.add(existing)
                if split:
                    # Taken out of an older note, unchanged: it was said when the note was, and says so.
                    existing.created_at = note["created_at"]
                    existing.metadata_ = {"split_from": {"fact_id": str(note["id"]), "key": note_key,
                                                         "content": note["content"], "revision": note["revision"],
                                                         "by_message_id": str(source_message_id)}}
                    split_notes.setdefault(note_key, []).append(existing.id)
                origin = note["source"] if split else source_message_id
                role = "origin"
            else:
                origin = source_message_id
                # What it says changed: its wording, or a detail it already had (value, entity, time). Filling in
                # what an older note didn't have (its entity, attribute, a time) says nothing new about it.
                changed = (" ".join(existing.content.split()) != " ".join(candidate.content.split())
                           or any(getattr(existing, k) is not None and getattr(existing, k) != v
                                  for k, v in claim.items() if v is not None and k != "historical")
                           or (claim["historical"] and not existing.historical)
                           or (when and existing.time_words is not None
                               and any(getattr(existing, k) != v for k, v in when.items() if k != "time_said_at")))
                if changed:
                    corrects = _corrects(extraction, existing.content, candidate.content)
                    _keep_history(existing, by_message_id=source_message_id,
                                  reason="correction" if corrects else "restated")
                    revised.append((existing, existing.content, existing.revision))
                    # The content version moves only when what it says changes (a restatement is no new premise).
                    existing.revision += 1
                    role = "correction" if corrects else "restated"
                else:
                    role = "support"
                existing.evidence_count += 1
            existing.kind = candidate.kind
            existing.content = candidate.content.strip()
            existing.embedding = vector
            existing.importance = candidate.importance
            existing.confidence = candidate.confidence
            existing.status = "active"
            existing.source_conversation_id = job.conversation_id
            existing.source_message_id = origin
            existing.last_confirmed_at = now
            for field, value in claim.items():
                # What this message doesn't say about it is kept, never cleared.
                if value is not None:
                    setattr(existing, field, value)
            for field, value in when.items():
                setattr(existing, field, value)
            db.flush()
            if origin is not None:
                memory_entities.evidence(db, job.user_id, role=role, message_id=origin, fact_id=existing.id)
            if claim["attribute"] == "name" and entity is not None and claim["value"]:
                memory_entities.rename(resolver, entity, claim["value"])
        for note_key, claims in split_notes.items():
            note = shown_by_key[note_key]
            if note_key not in updated_keys and note.status == "active":
                # Split into claims and not kept as one of them: the note is replaced by them.
                _keep_history(note, by_message_id=source_message_id, reason="split")
                meta = dict(note.metadata_ or {})
                meta["split_into"] = [str(c) for c in claims]
                note.metadata_ = meta
                note.status = "superseded"
                note.revision += 1
                memory_conflicts.settle(db, note, "fact_changed", message_id=source_message_id)
        if resolver.refused:
            logger.info("Memory job %s: entity refs not attached: %s", job_id, "; ".join(resolver.refused))
        for fact, _old_content, _old_revision in revised:
            # The revision is the answer to any disagreement with the version it replaced.
            memory_conflicts.settle(db, fact, "fact_changed", message_id=source_message_id)
        changed = {fact.id for fact, _content, _revision in revised}
        for key in (k.strip().lower() for k in extraction.settled):
            fact = shown_by_key.get(key)
            if fact is not None and fact.id not in changed and fact.status == "active" and key not in disputed:
                memory_conflicts.settle(db, fact, "known_confirmed", message_id=source_message_id)
        if (extraction.forget or forget_keys) and turn is not None:
            memory_forget.queue(db, job=job, message_id=source_message_id, user_text=turn[0],
                                requests=extraction.forget, keys=forget_keys)
        if extraction.corrections and turn is not None:
            # Same transaction as the facts: a consolidated correction is never left without its handoff
            # to the memory manager (services/memory_corrections.py applies it).
            from app.services import memory_corrections

            memory_corrections.record(db, job=job, message_id=source_message_id, user_text=turn[0],
                                      shown=[*earlier, *known], corrections=extraction.corrections)
        if revised and source_message_id is not None:
            # Whatever reworded a fact, its working items are reconciled with it (services/memory_reconcile.py),
            # in this same transaction: a revised fact is never left without that handoff.
            from app.services import memory_reconcile

            for fact, old_content, old_revision in revised:
                memory_reconcile.queue(db, fact=fact, old_content=old_content, old_revision=old_revision,
                                       message_id=source_message_id, job_id=job.id)
        # Completion is accepted only from the worker that still owns this
        # lease. Use a conditional UPDATE so a concurrent reclaim cannot be
        # defeated by a stale ORM object in this session.
        completion = db.execute(
            update(MemoryJob)
            .where(MemoryJob.id == job_id, MemoryJob.claim_token == claim_token)
            .values(
                status="completed", claim_token=None, lease_expires_at=None,
                processed_at=now, last_error=None,
            )
        )
        if completion.rowcount != 1:
            db.rollback()
            return False
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        job = db.scalar(select(MemoryJob).where(
            MemoryJob.id == job_id, MemoryJob.claim_token == claim_token
        ))
        if job is not None:
            job.status = "pending" if job.attempts < 3 else "failed"
            job.claim_token = None
            job.lease_expires_at = None
            job.last_error = str(exc)[:2000]
            db.commit()
        logger.exception("Memory job %s failed", job_id)
        return False


def next_pending_job_id(db: Session) -> uuid.UUID | None:
    now = datetime.now(timezone.utc)
    return db.scalar(
        select(MemoryJob.id)
        .where(
            or_(
                MemoryJob.status == "pending",
                (MemoryJob.status == "processing")
                & (MemoryJob.lease_expires_at.is_(None) | (MemoryJob.lease_expires_at <= now)),
            )
        )
        .order_by(MemoryJob.created_at)
        .limit(1)
    )


def _cosine(left: list[float] | None, right: list[float] | None) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left) * sum(b * b for b in right))
    return dot / norm if norm else 0.0


# What counts as relevant, measured on real messages (text-embedding-3-small): a message about
# a memory scores 0.3+ ("remember when you died in the cave?" 0.44); small talk scores up to
# ~0.24 against any game recap ("how was your day?", "I'm so tired from work today").
MIN_SIMILARITY = 0.3
# ...or most of the message's own words appear in it (embeddings off, or a name the model blurs).
MIN_WORD_OVERLAP = 0.5
MAX_EPISODES = 3  # of memory_retrieval_limit: recaps alone never fill the block
RECENCY_DAYS = 3.0  # an episode's recency bonus halves every this many days
# Only these may come along without matching the message, and only when this important.
ALWAYS_KINDS = frozenset({"preference", "constraint"})
ALWAYS_IMPORTANCE = 0.85
_STOPWORDS = frozenset("""a an and are as at be but by can did do does for from had has have he her him his how i
if in is it its just let me my no not of oh ok okay on or our she so that the their them then there they this to
up us was we were what when where which who why will with would yeah yes you your""".split())


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def retrieve_for_prompt(user_id: uuid.UUID, query: str) -> str | None:
    """The old compact block of facts and episodes, for callers that read text. Ranked by the shared
    recall service (``app.services.recall``: the same scoring, one implementation); a failure or no match
    is None, as before. New callers use the service and its explicit statuses instead."""
    settings = get_settings()
    from app.ai.recall.contracts import RecallRequest
    from app.services import recall

    try:
        with SessionLocal() as db:
            result = recall.search(db, RecallRequest(
                user_id=user_id, query=query[:2000], purpose="reply", now=datetime.now(timezone.utc),
                kinds=("fact", "episode"), limit=settings.memory_retrieval_limit))
            db.commit()
        return recall.render_legacy(result) if result.status in ("matched", "partial") else None
    except Exception:
        logger.exception("Long-term memory retrieval failed; continuing without it")
        return None
