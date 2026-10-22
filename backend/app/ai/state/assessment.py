"""One Jev assessment per user message: recall need and thinking need (docs/SHARED_STATE_STEP4_SPEC.md, "Jev
contract"). Pure: the request body and the parse. The HTTP call is in ``app/services/jev_assessment.py``.

Jev answers choice questions, not free text: it never writes a recall query, a thought prompt or a reason, and
none is invented here. Message and memory text go in as data (``state``); the questions and their criteria are
fixed here, so nothing in a message can change the policy. Choosing a processing need is never permission to
act.

Capability (verified 2026-10-21 against TypeSafe ``/systemone``, model ``jev-1.14.0``): one request carries
both questions and returns a choice and probabilities per question, ~60 ms. Decision quality is not
established: it needs the labelled evaluation (spec, "Acceptance tests and evaluation").
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = "conversation-assessment-v0"
# Bump when the questions or criteria change: decisions are only comparable under one policy.
POLICY_VERSION = "conversation-assessment-policy-v1"  # v1: recall/thinking only (no clarification/tool choices)

RECALL = ("not_required", "required", "uncertain")
THINKING = ("not_required", "required", "uncertain")

Status = Literal["ok", "error", "invalid", "disabled"]

# Jev decides two processing needs only. Whether she knows the answer, whether to ask what they meant, and
# whether to use a tool are cognition's and the mouth's (docs/SHARED_STATE_STEP4_SPEC.md, "Authority").
_RECALL_QUESTION = ("Before Columbina replies, does she need to look up her memory of this user: their shared "
                    "or personal history that isn't already in the recent conversation or the memories supplied?")
_RECALL_CRITERIA = {
    "not_required": "No memory lookup is needed first: the message is about the present conversation, general "
                    "knowledge, an opinion, a request, or something already in what she has. An unclear 'it' or "
                    "'her', or a need for outside information or a tool, is not a reason to look up memory.",
    "required": "It asks about or depends on something from your history with this user (something they told "
                "her before, something you did together, an earlier conversation) that is not in what she has.",
    "uncertain": "You can't tell.",
}
_THINKING_QUESTION = ("Does answering need deliberate, careful thinking beyond a normal conversational reply?")
_THINKING_CRITERIA = {
    "not_required": "A normal conversational reply is enough: chat, reactions, simple questions, recalling or "
                    "restating something.",
    "required": "It needs working something out before answering: weighing options for a real decision, a "
                "plan with several steps, a hard problem, or the user explicitly asked her to think it over.",
    "uncertain": "You can't tell.",
}

# Bounds on what goes to Jev: it sees the shape of the conversation, not all of it.
RECENT_MESSAGES = 6
MESSAGE_CHARS = 600
EVIDENCE_LINES = 8
EVIDENCE_CHARS = 1500  # per memory; content and a compact reference/applicability prefix


@dataclass(frozen=True)
class Assessment:
    assessment_id: str
    trigger_ref: str
    input_versions: dict[str, str]
    status: Status
    recall: str | None = None  # one of RECALL; None when not assessed
    thinking: str | None = None  # one of THINKING; None when not assessed
    recall_origin: str = "none"  # jev | none
    thinking_origin: str = "none"  # jev | explicit_user_instruction | none
    probabilities: dict[str, dict[str, float]] = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None  # the model that answered (Jev reports its version)
    latency_ms: float | None = None
    error: str | None = None
    schema_version: str = SCHEMA_VERSION
    policy_version: str = POLICY_VERSION

    @classmethod
    def disabled(cls, trigger_ref: str, reason: str) -> Assessment:
        return cls(str(uuid.uuid4()), trigger_ref, {}, "disabled", error=reason)


def state(*, message: str, recent: list[dict[str, Any]], interpretation: dict[str, Any] | None,
          evidence_lines: list[str], capabilities: list[str]) -> dict[str, Any]:
    """What Jev reads, bounded. The latest message is always whole (up to the char cap)."""
    def clip(text: str, n: int) -> str:
        text = (text or "").strip()
        return text if len(text) <= n else text[: n - 1] + "…"

    return {
        "latest_user_message": clip(message, MESSAGE_CHARS * 2),
        "recent_conversation": [{"from": "user" if m.get("role") == "user" else "columbina",
                                 "text": clip(m.get("content", ""), MESSAGE_CHARS)}
                                for m in recent[-RECENT_MESSAGES:] if m.get("content")],
        "quick_reading": interpretation or "unavailable",
        "memories_supplied": [clip(line, EVIDENCE_CHARS) for line in evidence_lines[:EVIDENCE_LINES]] or "none",
        "capabilities": capabilities,
    }


def body(model: str, conversation_state: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": model,
        "state": conversation_state,
        "questions": {
            "recall": {"type": "choice", "instructions": _RECALL_QUESTION, "criteria": _RECALL_CRITERIA},
            "thinking": {"type": "choice", "instructions": _THINKING_QUESTION, "criteria": _THINKING_CRITERIA},
        },
    }


def parse(data: dict[str, Any], *, trigger_ref: str, input_versions: dict[str, str], provider: str,
          latency_ms: float) -> Assessment:
    """A response to an ``Assessment``. A missing, unknown or refused answer for a question leaves that question
    unassessed (``None``) and the whole result ``invalid``: never a guessed negative."""
    answers = data.get("answers") or {}
    recall = (answers.get("recall") or {}).get("choice")
    thinking = (answers.get("thinking") or {}).get("choice")
    probabilities = {name: dict((answers.get(name) or {}).get("probabilities") or {})
                     for name in ("recall", "thinking") if (answers.get(name) or {}).get("probabilities")}
    ok_r, ok_t = recall in RECALL, thinking in THINKING
    problems = [f"{name}: {value!r}" for name, value, ok in (("recall", recall, ok_r),
                                                              ("thinking", thinking, ok_t)) if not ok]
    return Assessment(
        str(uuid.uuid4()), trigger_ref, input_versions, "ok" if not problems else "invalid",
        recall=recall if ok_r else None, thinking=thinking if ok_t else None,
        recall_origin="jev" if ok_r else "none", thinking_origin="jev" if ok_t else "none",
        probabilities=probabilities, provider=provider, model=data.get("model"), latency_ms=latency_ms,
        error=("unusable answer: " + "; ".join(problems)) if problems else None,
    )
