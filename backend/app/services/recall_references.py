"""A reply's recall (docs/COLUMBINA_MEMORY_SESSION3_RECALL_SPEC.md §10, §11): what it was given, what its
answer is about, and how the next turn follows up.

1. **Before the reply** (``for_reply``): the references from the answer before this one in this
   conversation are re-read, then one shared-recall search runs. The result rides on the turn as a
   ``TurnRecall``; the read tools add to it.
2. **With the reply** (``save_receipt``, in the transaction that commits it): a receipt and one
   ``supplied`` row per memory she was given. No memory text is copied.
3. **After it** (``start_association``, its own thread): one small call reads the delivered answer and
   the memories supplied to it, and says which memories which words are about. Only supplied ids and
   exact quotes are kept (``referenced`` rows with their span). This is a hypothesis about what the
   words refer to, never evidence that they're true. Pending, ambiguous or failed stays so.
4. **Next turn** (``prior_references``): the answer before's ``referenced`` memories whose words were
   actually heard, else everything it was supplied (marked uncertain). Only that one exchange, only
   this conversation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.recall.contracts import (
    KINDS, PRIOR_REFS, TOOL_CALLS_PER_REPLY, MemoryRecord, MemoryRef, RecallInvalid, RecallRequest, RecallResult,
)
from app.core.config import get_settings
from app.db.models import ChatMessage, MemoryManagerState, RecallReceipt, RecallReference
from app.db.session import SessionLocal
from app.services import heard, memory_forget, recall

log = logging.getLogger(__name__)

SCHEMA_VERSION = "recall-receipt-v1"
BUNDLE_VERSION = "evidence-bundle-v1"  # the reply's sectioned evidence (recall.render_bundle)
_TARGET = {"fact": "fact_id", "episode": "episode_id", "event": "event_id", "item": "item_id", "message": "message_id"}


@dataclass
class TurnRecall:
    """One reply's recall: the automatic result, what the tools found, and the tool budget."""

    user_id: uuid.UUID
    conversation_id: uuid.UUID | None
    zone: str
    now: datetime
    focus_context_id: uuid.UUID | None
    result: RecallResult
    rendered: str
    legacy: str | None  # the old fact/episode block, for the cognitive layer that reads text
    prior_note: str | None = None
    supplied: dict[MemoryRef, MemoryRecord] = field(default_factory=dict)
    tool_calls: int = 0
    tool_budget: int = TOOL_CALLS_PER_REPLY
    tool_statuses: list[str] = field(default_factory=list)
    histories: list = field(default_factory=list)  # recall_history.HistoryRun, kept with the receipt
    # The evidence bundle this reply's consumers read: one id per search, and what each consumer was given
    # (refs and a hash of its rendering), so a receipt says exactly what Student 1, Student 2 and the mouth saw.
    bundle_id: uuid.UUID = field(default_factory=uuid.uuid4)
    consumers: dict[str, dict] = field(default_factory=dict)
    user_message_id: uuid.UUID | None = None  # the message being answered: never a transcript search hit

    def supply(self, records: list[MemoryRecord]) -> None:
        for record in records:
            self.supplied.setdefault(record.ref, record)

    def consumed(self, consumer: str, refs, rendering: str | None, *, note: str | None = None) -> None:
        """What one consumer of this bundle was given. Refs and a hash only: never memory text."""
        entry = {"refs": [str(r) for r in refs],
                 "rendering_sha": hashlib.sha256(rendering.encode()).hexdigest()[:16] if rendering else None}
        self.consumers[consumer] = entry | ({"note": note} if note else {})


# --------------------------------------------------------------------------- #
# Before the reply
# --------------------------------------------------------------------------- #


def _focus(db: Session, user_id: uuid.UUID) -> uuid.UUID | None:
    state = db.get(MemoryManagerState, user_id)
    return state.focus_context_id if state is not None else None


def prior_references(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID,
                     before_message_id: uuid.UUID | None) -> tuple[list[MemoryRef], str | None]:
    """The memories the answer before this message referred to, and how sure that is."""
    refs, note, _ = _prior(db, user_id, conversation_id, before_message_id)
    return refs, note


def _prior(db: Session, user_id: uuid.UUID, conversation_id: uuid.UUID,
           before_message_id: uuid.UUID | None) -> tuple[list[MemoryRef], str | None, int]:
    """``prior_references`` plus how many memories that answer was given have since been deleted (their
    reference rows went with them, so only the receipt's count still knows)."""
    query = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id, ChatMessage.role == "assistant")
    if before_message_id is not None:
        before = db.get(ChatMessage, before_message_id)
        if before is not None and before.conversation_sequence is not None:
            query = query.where(ChatMessage.conversation_sequence < before.conversation_sequence)
    answer = db.scalar(query.order_by(ChatMessage.conversation_sequence.desc()).limit(1))
    if answer is None:
        return [], None, 0
    receipt = db.scalar(select(RecallReceipt).where(RecallReceipt.assistant_message_id == answer.id,
                                                    RecallReceipt.user_id == user_id))
    if receipt is None:
        return [], None, 0
    rows = list(db.scalars(select(RecallReference).where(RecallReference.receipt_id == receipt.id)
                           .order_by(RecallReference.id)))
    heard_upto = heard.heard_cut(answer.content or "", answer.heard_chars)
    referenced = [r for r in rows if r.relation == "referenced" and r.span_end is not None and r.span_end <= heard_upto]
    unheard = [r for r in rows if r.relation == "referenced" and (r.span_end or 0) > heard_upto]
    supplied = [r for r in rows if r.relation == "supplied"]
    gone = (receipt.counts or {}).get("supplied", 0) - len(supplied)
    if referenced:
        refs, note = _unique(referenced), "matched to the words of your last answer"
    elif receipt.association_status == "done" and not unheard:
        refs, note = [], None  # her answer used none of what it was given
    elif supplied:
        # What the answer mentioned only in words the user never heard isn't something they can follow up on.
        cut_off = set(_unique(unheard))
        refs = [ref for ref in _unique(supplied) if ref not in cut_off]
        note = "given to your last answer; which of them it was about is unclear" if refs else None
    else:
        refs, note = [], None
    if gone > 0:
        note = (note + "; " if note else "") + f"{gone} memory used then has since been removed"
    return refs[:PRIOR_REFS], note, max(gone, 0)


def _unique(rows: list[RecallReference]) -> list[MemoryRef]:
    out: list[MemoryRef] = []
    for row in rows:
        ref = _ref_of(row)
        if ref not in out:
            out.append(ref)
    return out


def _ref_of(row: RecallReference) -> MemoryRef:
    for kind, column in _TARGET.items():
        value = getattr(row, column)
        if value is not None:
            return MemoryRef(kind, str(value))  # type: ignore[arg-type]
    raise ValueError("recall reference without a target")


def for_reply(user_id: uuid.UUID, conversation_id: uuid.UUID | None, user_message_id: uuid.UUID | None,
              query: str, zone: str | None, *, skip_focus_items: bool = True) -> TurnRecall:
    """One reply's automatic recall. Never raises: a failure is a ``failed`` result she's told about."""
    now = datetime.now(timezone.utc)
    zone = zone or "UTC"
    try:
        with SessionLocal() as db:
            focus = _focus(db, user_id)
            refs, note = (prior_references(db, user_id, conversation_id, user_message_id)
                          if conversation_id else ([], None))
            req = RecallRequest(user_id=user_id, query=(query or "")[:2000], purpose="reply", now=now, zone=zone,
                                conversation_id=conversation_id, focus_context_id=focus, prior_refs=tuple(refs),
                                kinds=KINDS, limit=get_settings().recall_auto_limit,
                                skip_open_items_of=focus if skip_focus_items else None)
            result = recall.search(db, req)
            forgetting = memory_forget.status_note(db, user_id)
            db.commit()
    except RecallInvalid as exc:
        log.warning("recall: reply request invalid: %s", exc)
        result, note, forgetting = RecallResult.failed("invalid_request"), None, None
    except Exception:  # noqa: BLE001 - a broken recall is said as such, never as "nothing recorded"
        log.exception("recall: reply recall failed")
        result, note, forgetting = RecallResult.failed("search_failed"), None, None
    rendered = recall.render_bundle(result, now=now, zone=zone)
    if note:
        rendered += f"\n(About \"from your last answer\": {note}.)"
    if forgetting:
        rendered += f"\n{forgetting}"
    turn = TurnRecall(user_id, conversation_id, zone, now, None, result, rendered,
                      recall.render_legacy(result), prior_note=note,
                      tool_budget=get_settings().recall_tool_calls_per_reply, user_message_id=user_message_id)
    turn.supply(result.records)
    return turn


def cached_for_reply(user_id: uuid.UUID, conversation_id: uuid.UUID | None, user_message_id: uuid.UUID | None,
                     zone: str | None) -> TurnRecall:
    """A reply's memory without a search (the shared-state pipeline, docs/SHARED_STATE_REFACTOR_SPEC.md): only
    the answer before's references, re-read now, so a correction or deletion since is seen, never an old copy.
    The turn carries the same tool budget, and its receipt supplies them again, so follow-ups keep working.
    Never raises: a failed reread is ``failed``, not an empty cache."""
    now = datetime.now(timezone.utc)
    zone = zone or "UTC"
    note, deleted = None, 0
    try:
        with SessionLocal() as db:
            refs, note, deleted = (_prior(db, user_id, conversation_id, user_message_id)
                                   if conversation_id else ([], None, 0))
            result = recall.reread(db, user_id, refs)
        if deleted:
            # Deleted since: no reference row is left to reread, so the count is all the cache can say.
            result.coverage["references_deleted"] = deleted
    except Exception:  # noqa: BLE001
        log.exception("recall: reading the cached references failed")
        result, note = RecallResult.failed("read_failed"), None
    if result.status == "failed":
        lines = ["Memories from your last answer: couldn't be read just now. Don't say nothing is recorded."]
    elif not result.records:
        lines = ["Memories from your last answer: none."]
    else:
        lines = ["Memories from your last answer, re-read now:"]
        lines += recall.render_records(result.records, now=now, zone=zone)
    if result.coverage.get("references_set_aside"):
        lines.append("(Conversation history your last answer told them was about something they asked you to "
                     "forget: it isn't carried on as something you remember.)")
    if (result.coverage.get("references_gone") or deleted) and not note:
        lines.append("(Something your last answer used has since been removed or changed.)")
    if note:
        lines.append(f"(About these: {note}.)")
    turn = TurnRecall(user_id, conversation_id, zone, now, None, result, "\n".join(lines),
                      recall.render_legacy(result), prior_note=note,
                      tool_budget=get_settings().recall_tool_calls_per_reply, user_message_id=user_message_id)
    turn.supply(result.records)
    recall._log({"op": "cache_reread", "status": result.status, "refs": [str(r) for r in result.refs()],
                 "gone": len(result.coverage.get("references_gone", [])) + deleted})
    return turn


def _activity_context(db: Session, source: str, activity: uuid.UUID) -> uuid.UUID | None:
    """The context a hard thought's activity belongs to: a game session's world. Never the focus now: the
    problem may have been queued before attention moved."""
    if source == "minecraft":
        from app.db.models import DesktopSession
        from app.services.observation_adapters import game_context_now

        session = db.get(DesktopSession, activity)
        return game_context_now(db, session) if session is not None else None
    return None


def for_thought(db: Session, *, user_id: uuid.UUID, source: str, activity: uuid.UUID, query: str,
                refs: list[str] | None = None) -> str | None:
    """A hard thought's recall, rendered for its input; the same service and statuses as a reply. ``refs``:
    gets the supplied memories' references (what she read, for her thought's handoff to memory)."""
    now = datetime.now(timezone.utc)
    try:
        context_id = _activity_context(db, source, activity)
        result = recall.search(db, RecallRequest(
            user_id=user_id, query=(query or "")[:2000], purpose="hard_thought", now=now,
            # Its own activity counts as named (a pull, not a filter: only tool searches filter).
            focus_context_id=context_id, context_ids=(context_id,) if context_id else (),
            kinds=KINDS, limit=get_settings().recall_auto_limit))
    except Exception:  # noqa: BLE001
        log.exception("recall: hard-thought recall failed")
        result = RecallResult.failed("search_failed")
    recall._log({"op": "hard_thought_supplied", "source": source, "activity": str(activity),
                 "status": result.status, "refs": [str(r) for r in result.refs()]})
    if refs is not None:
        refs.extend(str(r) for r in result.refs())
    return recall.render(result, now=now, zone="UTC")


# --------------------------------------------------------------------------- #
# With the reply
# --------------------------------------------------------------------------- #


def save_receipt(db: Session, turn: TurnRecall | None, *, assistant_message_id: uuid.UUID,
                 conversation_id: uuid.UUID, reply: str) -> int | None:
    """In the caller's transaction, in a savepoint: never fails the turn. Returns the receipt id when its
    association is to run (``start_association`` after the commit), else None."""
    if turn is None:
        return None
    try:
        with db.begin_nested():
            supplied = list(turn.supplied.values())
            association = ("none" if not supplied else
                           "pending" if reply.strip() else "skipped")
            by_kind: dict[str, int] = {}
            for record in supplied:
                by_kind[record.ref.kind] = by_kind.get(record.ref.kind, 0) + 1
            receipt = RecallReceipt(
                user_id=turn.user_id, assistant_message_id=assistant_message_id, conversation_id=conversation_id,
                schema_version=SCHEMA_VERSION, purpose="reply", status=turn.result.status,
                coverage={k: v for k, v in turn.result.coverage.items() if k != "ms"} | (
                    {"errors": turn.result.errors} if turn.result.errors else {}),
                counts={"supplied": len(supplied), "by_kind": by_kind, "tool_calls": turn.tool_calls,
                        "tool_statuses": turn.tool_statuses,
                        "bundle": {"id": str(turn.bundle_id), "version": BUNDLE_VERSION},
                        "consumers": turn.consumers | {"mouth": _mouth(turn)}}
                | ({"histories": len(turn.histories)} if turn.histories else {}),
                association_status=association)
            db.add(receipt)
            db.flush()
            for record in supplied:
                db.add(_reference(receipt, record, relation="supplied", method="structured"))
            db.flush()
            _save_histories(db, receipt, turn.histories)
            return receipt.id if association == "pending" else None
    except Exception:  # noqa: BLE001
        log.exception("recall: saving the receipt for %s failed", assistant_message_id)
        return None


def _mouth(turn: TurnRecall) -> dict:
    """The mouth's share of a receipt: everything it was supplied (the bundle plus what its tools found)."""
    return {"refs": [str(r) for r in turn.supplied],
            "rendering_sha": hashlib.sha256(turn.rendered.encode()).hexdigest()[:16] if turn.rendered else None}


def _save_histories(db: Session, receipt: RecallReceipt, runs: list) -> None:
    """Each history rebuilt for this reply, in its own savepoint: a source deleted meanwhile loses that
    history's provenance (counted on the receipt), never the receipt."""
    from app.services import recall_history

    lost = 0
    for run in runs:
        try:
            with db.begin_nested():
                recall_history.save(db, receipt_id=receipt.id, user_id=receipt.user_id, run=run)
        except Exception:  # noqa: BLE001
            log.exception("recall: saving a rebuilt history for receipt %s failed", receipt.id)
            lost += 1
    if lost:
        receipt.counts = {**(receipt.counts or {}), "histories_unsaved": lost}


def _reference(receipt: RecallReceipt, record: MemoryRecord, *, relation: str, method: str,
               span: tuple[int, int] | None = None) -> RecallReference:
    target = int(record.ref.id) if record.ref.kind == "event" else uuid.UUID(record.ref.id)
    return RecallReference(receipt_id=receipt.id, user_id=receipt.user_id, relation=relation,
                           fingerprint=record.fingerprint, target_recorded_at=record.recorded_at,
                           span_start=span[0] if span else None, span_end=span[1] if span else None,
                           method=method, **{_TARGET[record.ref.kind]: target})


# --------------------------------------------------------------------------- #
# After it: which memories the answer's words are about
# --------------------------------------------------------------------------- #

_ASSOCIATION_PROMPT = """You are given a reply Columbina just gave, and the memories she was given while \
writing it, each with an id. Say which memories the reply's words are about.

For each memory the reply actually talks about, return its id and a short quote: the exact words of the \
reply (copied character for character, 3 to 12 words) that are about it. Leave out memories the reply \
doesn't use. Use only ids from the list. Return JSON: {"references": [{"id": "...", "quote": "..."}]}"""


def _ask(reply: str, memories: list[MemoryRecord]) -> list[dict]:
    from app.services.long_term_memory import _client

    settings = get_settings()
    listed = "\n".join(f"- {record.ref}: {record.content}" for record in memories)
    response = _client().chat.completions.create(
        model=settings.recall_association_model or settings.subagent_model or "gpt-4.1-mini",
        messages=[{"role": "system", "content": _ASSOCIATION_PROMPT},
                  {"role": "user", "content": f"REPLY:\n{reply}\n\nMEMORIES:\n{listed}"}],
        response_format={"type": "json_object"}, temperature=0)
    data = json.loads(response.choices[0].message.content or "{}")
    found = data.get("references") if isinstance(data, dict) else None
    return found if isinstance(found, list) else []


def associate(receipt_id: int, *, ask=None) -> str:
    """Run the association for one receipt. Returns its new status. ``ask`` replaces the model call."""
    ask = ask or _ask
    with SessionLocal() as db:
        receipt = db.get(RecallReceipt, receipt_id)
        if receipt is None or receipt.association_status != "pending":
            return receipt.association_status if receipt else "gone"
        message = db.get(ChatMessage, receipt.assistant_message_id)
        rows = list(db.scalars(select(RecallReference).where(RecallReference.receipt_id == receipt.id,
                                                              RecallReference.relation == "supplied")))
        records = {}
        for row in rows:
            ref = _ref_of(row)
            fresh = recall.read(db, receipt.user_id, ref)
            if fresh.records:
                records[str(ref)] = fresh.records[0]
        reply = (message.content if message else "") or ""
        db.commit()
    status, note, spans = "done", None, []
    try:
        proposals = ask(reply, list(records.values())) if records and reply else []
        bad = 0
        for proposal in proposals[:12]:
            ref, quote = str(proposal.get("id", "")).strip("[] "), str(proposal.get("quote", "")).strip()
            start = reply.find(quote) if quote else -1
            if ref not in records or start < 0:
                bad += 1
                continue
            spans.append((records[ref], (start, start + len(quote))))
        if bad and not spans:
            status, note = "ambiguous", f"{bad} proposed reference(s) didn't match the memories or the words"
        elif bad:
            note = f"{bad} proposed reference(s) dropped"
    except Exception as exc:  # noqa: BLE001 - kept as failed; the next turn treats what was given as candidates
        log.warning("recall: association for receipt %s failed: %s", receipt_id, exc)
        status, note = "failed", type(exc).__name__
    with SessionLocal() as db:
        receipt = db.get(RecallReceipt, receipt_id)
        if receipt is None or receipt.association_status != "pending":
            return receipt.association_status if receipt else "gone"
        for record, span in spans:
            db.add(_reference(receipt, record, relation="referenced", method="post_reply", span=span))
        receipt.association_status, receipt.association_note = status, note
        db.commit()
    recall._log({"op": "associate", "receipt": receipt_id, "status": status, "referenced": len(spans)})
    return status


def start_association(receipt_id: int | None) -> None:
    """After the reply is committed: its own thread, off the reply path."""
    if receipt_id is None:
        return

    def run() -> None:
        try:
            associate(receipt_id)
        except Exception:  # noqa: BLE001
            log.exception("recall: association thread for receipt %s failed", receipt_id)

    threading.Thread(target=run, name=f"recall-associate-{receipt_id}", daemon=True).start()


def inspect(db: Session, user_id: uuid.UUID, limit: int = 10) -> dict:
    """Embedding progress and the latest receipts with their references, for GET /api/memory/recall."""
    from app.services import recall_embeddings

    receipts = list(db.scalars(select(RecallReceipt).where(RecallReceipt.user_id == user_id)
                               .order_by(RecallReceipt.created_at.desc()).limit(limit)))
    out = []
    for receipt in receipts:
        rows = list(db.scalars(select(RecallReference).where(RecallReference.receipt_id == receipt.id)))
        out.append((receipt, {"supplied": [str(_ref_of(r)) for r in rows if r.relation == "supplied"],
                              "referenced": [str(_ref_of(r)) for r in rows if r.relation == "referenced"],
                              "histories": _histories(db, receipt.id)}))
    return {"embeddings": recall_embeddings.progress(db, user_id), "receipts": out}


def _histories(db: Session, receipt_id: int) -> list[dict]:
    from app.db.models import RecallReconstruction, RecallReconstructionSource

    out = []
    for row in db.scalars(select(RecallReconstruction).where(RecallReconstruction.receipt_id == receipt_id)
                          .order_by(RecallReconstruction.id)):
        sources = []
        for source in db.scalars(select(RecallReconstructionSource).where(
                RecallReconstructionSource.reconstruction_id == row.id).order_by(RecallReconstructionSource.id)):
            target = next(f"{column.removesuffix('_id')}:{getattr(source, column)}" for column in (
                "fact_id", "episode_id", "event_id", "item_id", "message_id", "observation_id", "experience_id",
                "session_event_id", "thought_id") if getattr(source, column) is not None)
            sources.append({"label": source.label, "source": target, "speaker": source.speaker,
                            "cited": source.cited, "in_scope": source.in_scope, "depth": source.depth,
                            "fingerprint": source.fingerprint})
        out.append({"id": row.id, "status": row.status, "question": row.question, "about": row.about,
                    "scope": row.scope, "model": row.model, "input_digest": row.input_digest, "account": row.account,
                    "coverage": row.coverage, "sources": sources})
    return out
