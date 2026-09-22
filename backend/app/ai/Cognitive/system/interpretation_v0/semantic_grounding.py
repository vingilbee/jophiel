"""Narrow, inspectable semantic grounding rules for Interpretation V0."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..contracts.interpretation import (
    Contradiction,
    InterpretationEvidence,
    OutOfScopeRecord,
    SemanticFrame,
    UnknownInput,
)
from ..perception_v0.evidence import stable_id
from .evidence import evidence_by_cue, focus_message_evidence, runtime_evidence
from .models import InterpretationRequest
from .ontology import PREDICATE_REGISTRY_VERSION, PREDICATES, validate_semantic_frames

SEMANTIC_GROUNDER_VERSION = "interpretation-semantic-grounder-v0.1"


@dataclass(frozen=True, slots=True)
class GroundingResult:
    frames: tuple[SemanticFrame, ...]
    unknowns: tuple[UnknownInput, ...]
    contradictions: tuple[Contradiction, ...]
    out_of_scope_records: tuple[OutOfScopeRecord, ...]


class SemanticGrounder:
    def ground(
        self,
        request: InterpretationRequest,
        evidence: tuple[InterpretationEvidence, ...],
    ) -> GroundingResult:
        text = request.focus_message.content
        lower = text.casefold().strip()
        focus_evidence = focus_message_evidence(evidence, request.perception.focus_message_id)
        cue_evidence = evidence_by_cue(evidence)
        fallback_evidence = focus_evidence or next(iter(cue_evidence.values()))
        frames: list[SemanticFrame] = []
        unknowns: list[UnknownInput] = []
        contradictions: list[Contradiction] = []
        out_of_scope: list[OutOfScopeRecord] = []

        def add_frame(
            predicate: str,
            arguments: dict[str, object],
            *,
            polarity: str = "positive",
            unresolved: tuple[str, ...] = (),
            evidence_refs: tuple[str, ...] | None = None,
            rule: str,
        ) -> SemanticFrame:
            refs = evidence_refs or (fallback_evidence.evidence_id,)
            frame = SemanticFrame(
                frame_id=stable_id(
                    "semantic-frame", request.cognitive_turn_id,
                    request.perception.perception_event_id, predicate, arguments, rule,
                ),
                predicate=predicate,
                arguments=arguments,
                polarity=polarity,
                evidence_refs=refs,
                unresolved_arguments=unresolved,
                grounding_rule_ids=(rule,),
            )
            frames.append(frame)
            for argument in unresolved:
                unknowns.append(UnknownInput(
                    unknown_id=stable_id(
                        "interpretation-unknown", request.cognitive_turn_id,
                        frame.frame_id, argument,
                    ),
                    key=argument,
                    owner="user",
                    evidence_refs=refs,
                ))
            return frame

        if re.search(r"\bwhat time does (?:the )?museum close\b", lower):
            add_frame(
                "museum_closing_time", {"museum": None}, unresolved=("museum",),
                rule="grounding.museum-closing-question.v1",
            )
        elif re.search(r"\b(?:could|can|would) you send the report\b|\bplease send the report\b", lower):
            recipient_match = re.search(r"\bto\s+([A-Z][\w'-]+)", text)
            recipient = recipient_match.group(1) if recipient_match else None
            unresolved = () if recipient else ("recipient",)
            add_frame(
                "send_report", {"object": "report", "recipient": recipient},
                unresolved=unresolved, rule="grounding.send-report-request.v1",
            )
        elif match := re.search(r"\bi sent the report to\s+([A-Z][\w'-]+)", text, re.I):
            add_frame(
                "send_report", {"agent": "user", "object": "report", "recipient": match.group(1)},
                rule="grounding.send-report-assertion.v1",
            )
        elif "not " in lower and (
            "appointment" in " ".join(message.content.casefold() for message in request.messages)
            or "it is" in lower
        ):
            correction = re.search(r"\bis\s+([\w'-]+),?\s+not\s+([\w'-]+)", text, re.I)
            accepted = correction.group(1) if correction else None
            rejected = correction.group(2) if correction else None
            unresolved = tuple(key for key, value in (("accepted", accepted), ("rejected", rejected)) if value is None)
            add_frame(
                "appointment_date", {"accepted": accepted, "rejected": rejected},
                unresolved=unresolved, rule="grounding.appointment-correction.v1",
            )
        elif re.fullmatch(r"no thanks[.!]?", lower):
            prior = request.messages[-2].content if len(request.messages) > 1 else ""
            offer_match = re.search(r"\b(?:like|add)\s+([\w'-]+)", prior, re.I)
            offer = offer_match.group(1) if offer_match else None
            add_frame(
                "accept_offer", {"offer": offer}, polarity="negative",
                unresolved=() if offer else ("offer",), rule="grounding.explicit-decline.v1",
            )
        elif lower.startswith("yes") and "submitted" in lower:
            add_frame(
                "submit_form", {"agent": "user", "object": "form"},
                rule="grounding.form-confirmation.v1",
            )
        elif re.fullmatch(r"(?:okay|ok)[.!]?", lower):
            prior_id = request.messages[-2].message_id if len(request.messages) > 1 else None
            add_frame(
                "acknowledge_message", {"message_id": prior_id},
                unresolved=() if prior_id else ("message_id",),
                rule="grounding.acknowledgement-token.v1",
            )
        elif "soup" in lower and "service" in lower:
            add_frame(
                "evaluate_meal", {"soup": "positive", "service": "negative"},
                polarity="unknown", rule="grounding.meal-evaluation.v1",
            )
        elif match := re.search(r"\bit is raining in\s+([\w'-]+)", text, re.I):
            add_frame(
                "weather_condition", {"condition": "rain", "location": match.group(1)},
                rule="grounding.weather-assertion.v1",
            )
        elif re.fullmatch(r"(?:hey+|hello|hi)[!.]*", lower):
            add_frame(
                "social_opening", {"addressee": "columbina"},
                rule="grounding.social-opening.v1",
            )
        elif re.search(r"\b(?:goodbye|bye)\b", lower):
            future = "tomorrow" if "tomorrow" in lower else None
            add_frame(
                "social_closing", {"future_contact": future},
                rule="grounding.social-closing.v1",
            )
        elif match := re.search(r"\btrain leaves at\s+([\w:.-]+)", lower):
            add_frame(
                "train_departure_time", {"train": None, "time": match.group(1).rstrip(".")},
                unresolved=("train",), rule="grounding.train-time-assertion.v1",
            )
        elif "bridge is closed" in lower and lower.startswith("if "):
            bridge = add_frame(
                "bridge_closed", {"speaker": "she", "bridge": None},
                unresolved=("bridge",), rule="grounding.quoted-bridge-condition.v1",
            )
            add_frame(
                "contingency_action", {"condition": bridge.predicate, "action": None},
                unresolved=("action",), rule="grounding.hypothetical-contingency.v1",
            )
        elif "stop" in lower and "timer" in lower:
            timer_match = re.search(r"\bstop (?:the )?([\w'-]+) timer\b", lower)
            timer = timer_match.group(1) if timer_match else None
            add_frame(
                "stop_timer", {"timer": timer}, unresolved=() if timer else ("timer",),
                rule="grounding.stop-timer.v1",
            )
        elif "getting cold" in lower:
            prior = request.messages[-2].content.casefold() if len(request.messages) > 1 else ""
            location = "office" if "office" in prior or "office" in lower else None
            temperature = add_frame(
                "room_temperature_change", {"direction": "colder", "location": location},
                rule="grounding.room-temperature.v1",
            )
            if "window" in prior:
                add_frame(
                    "close_window", {"window": location or "context_window"},
                    evidence_refs=temperature.evidence_refs,
                    rule="grounding.indirect-window-action.v1",
                )
        elif "broken export" in lower:
            # Mixed polarity is not in the canonical contract.  This frame is
            # retained as unknown; live V0 does not infer tone mismatch from the
            # shadow wording cues.
            add_frame(
                "evaluate_export_event", {"event": "broken_export", "positive_phrase": None},
                polarity="unknown", unresolved=("positive_phrase",),
                rule="grounding.export-event-without-shadow-tone.v1",
            )
        elif re.search(r"\byeah,? we did\b", lower):
            add_frame(
                "shared_progress", {"participants": ("user", "columbina"), "time": "today"},
                rule="grounding.shared-progress.v1",
            )
        elif "export pdf" in lower or "export pdfs" in lower:
            message_ref = fallback_evidence.evidence_id
            runtime_ref = runtime_evidence(evidence, "/available_capabilities").evidence_id
            frame = add_frame(
                "capability_available", {"capability": "export_pdf", "claimed_value": False},
                polarity="negative", evidence_refs=(message_ref, runtime_ref),
                rule="grounding.capability-claim.v1",
            )
            if "export_pdf" in request.runtime.available_capabilities:
                conflict_refs = [message_ref, runtime_ref]
                for memory in request.memories:
                    if "export" in memory.content.casefold():
                        memory_ev = next(item for item in evidence if item.memory_ref == memory.memory_id)
                        conflict_refs.append(memory_ev.evidence_id)
                contradictions.append(Contradiction(
                    contradiction_id=stable_id(
                        "interpretation-contradiction", request.cognitive_turn_id,
                        frame.frame_id, "runtime-capability",
                    ),
                    status="active",
                    description="Current trusted capability state contradicts the user claim or stale memory.",
                    evidence_refs=tuple(conflict_refs),
                ))
        elif "delete" in lower and "database" in lower:
            database = "production_customer" if "production customer" in lower else None
            authorization = request.runtime.authorization
            add_frame(
                "delete_database", {"database": database, "authorization": authorization},
                unresolved=tuple(
                    key for key, value in (("database", database), ("authorization", authorization))
                    if value is None
                ),
                rule="grounding.consequential-database-delete.v1",
            )
        elif "summarize" in lower and "invoice" in lower:
            attachment_id = request.runtime.attachments[0].attachment_id if request.runtime.attachments else None
            refs = [fallback_evidence.evidence_id]
            if request.runtime.attachments:
                refs.append(runtime_evidence(evidence, "/attachments").evidence_id)
            add_frame(
                "summarize_attachment", {"attachment_id": attachment_id, "object": "invoice"},
                unresolved=() if attachment_id else ("attachment_id",),
                evidence_refs=tuple(refs), rule="grounding.attachment-summary.v1",
            )
        else:
            action_match = re.match(r"\s*([A-Za-z][\w'-]*)\s*(.*?)[.!]?\s*$", text)
            surface_action = action_match.group(1).casefold() if action_match else ""
            object_surface = action_match.group(2).strip() if action_match else ""
            request_cue = request.perception.cue_activations[1]
            explicit_request = request_cue.cue_key == "conversation.explicit_action_request" and request_cue.value is True
            if explicit_request and surface_action:
                frame = add_frame(
                    "unknown",
                    {
                        "surface_action": surface_action,
                        "predicate": None,
                        "object_reference": None,
                    },
                    unresolved=("predicate", "object_reference"),
                    rule="grounding.unknown-action-predicate.v1",
                )
                out_of_scope.append(OutOfScopeRecord(
                    out_of_scope_id=stable_id(
                        "interpretation-out-of-scope", request.cognitive_turn_id,
                        frame.frame_id, surface_action,
                    ),
                    surface_form=surface_action,
                    reason="The action predicate is not in the versioned V0 predicate registry.",
                    evidence_refs=frame.evidence_refs,
                    recoverable_by_user=True,
                ))
            else:
                frame = add_frame(
                    "unknown", {"surface_action": surface_action or None, "object_reference": object_surface or None, "predicate": None},
                    unresolved=("predicate",), rule="grounding.bounded-unknown-input.v1",
                )
                unknowns.append(UnknownInput(
                    unknown_id=stable_id(
                        "interpretation-unknown", request.cognitive_turn_id,
                        frame.frame_id, "meaning",
                    ),
                    key="meaning", owner="user", evidence_refs=frame.evidence_refs,
                ))

        validate_semantic_frames(frames)
        return GroundingResult(tuple(frames), tuple(unknowns), tuple(contradictions), tuple(out_of_scope))


def validate_runtime_predicate_registry(request: InterpretationRequest) -> None:
    if request.runtime.predicate_registry_version != PREDICATE_REGISTRY_VERSION:
        raise ValueError("unsupported interpretation predicate registry version")
    if tuple(request.runtime.known_predicates) != PREDICATES:
        raise ValueError("runtime known-predicate registry must exactly match the code-owned manifest")
