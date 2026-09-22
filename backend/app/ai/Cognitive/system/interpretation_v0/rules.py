"""Deterministic, multidimensional Interpretation V0 hypothesis rules."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..contracts.interpretation import (
    HypothesisDimension,
    HypothesisDisposition,
    HypothesisSupport,
    InterpretationEvidence,
    SemanticFrame,
    UnknownInput,
)
from .evidence import evidence_by_cue, focus_message_evidence
from .models import InterpretationRequest

HYPOTHESIS_RULES_VERSION = "interpretation-hypothesis-rules-v0.1"


@dataclass(frozen=True, slots=True)
class HypothesisCandidate:
    hypothesis_key: str
    dimension: HypothesisDimension
    base_support: HypothesisSupport
    support: HypothesisSupport
    disposition: HypothesisDisposition
    semantic_frame_refs: tuple[str, ...]
    supporting_evidence_refs: tuple[str, ...]
    contradicting_evidence_refs: tuple[str, ...] = ()
    unknown_refs: tuple[str, ...] = ()
    contradiction_refs: tuple[str, ...] = ()
    rule_ids: tuple[str, ...] = ()
    ambiguity_gate: float = 0.0
    bias_adjustments: tuple[object, ...] = ()


class HypothesisRuleEngine:
    def evaluate(
        self,
        request: InterpretationRequest,
        evidence: tuple[InterpretationEvidence, ...],
        frames: tuple[SemanticFrame, ...],
        unknowns: tuple[UnknownInput, ...],
        contradiction_ids: tuple[str, ...],
    ) -> tuple[HypothesisCandidate, ...]:
        cues = {item.cue_key: item for item in request.perception.cue_activations}
        cue_records = evidence_by_cue(evidence)
        focus = focus_message_evidence(evidence, request.perception.focus_message_id)
        frame_refs = tuple(item.frame_id for item in frames)
        frame_evidence = tuple(dict.fromkeys(ref for frame in frames for ref in frame.evidence_refs))
        predicates = {item.predicate for item in frames}
        lower = request.focus_message.content.casefold().strip()
        candidates: list[HypothesisCandidate] = []

        def cue_present(key: str) -> bool:
            return key in cues and cues[key].state.value == "present"

        def refs_for_cue(key: str) -> tuple[str, ...]:
            record = cue_records.get(key)
            return (record.evidence_id,) if record else ()

        def add(
            key: str,
            dimension: HypothesisDimension,
            support: HypothesisSupport,
            disposition: HypothesisDisposition,
            rule: str,
            *,
            supporting: tuple[str, ...] | None = None,
            contradicting: tuple[str, ...] = (),
            unknown_refs: tuple[str, ...] = (),
            semantic_refs: tuple[str, ...] | None = None,
            ambiguity_gate: float = 0.0,
        ) -> None:
            candidates.append(HypothesisCandidate(
                hypothesis_key=key,
                dimension=dimension,
                base_support=support,
                support=support,
                disposition=disposition,
                semantic_frame_refs=frame_refs if semantic_refs is None else semantic_refs,
                supporting_evidence_refs=(frame_evidence if supporting is None else supporting),
                contradicting_evidence_refs=contradicting,
                unknown_refs=unknown_refs,
                contradiction_refs=contradiction_ids,
                rule_ids=(rule,),
                ambiguity_gate=ambiguity_gate,
            ))

        action = cue_present("conversation.explicit_action_request")
        question = cue_present("conversation.question_form")
        correction = cue_present("conversation.explicit_correction")
        uncertainty = cue_present("linguistic.uncertainty_marker")

        # Communicative acts.
        if action:
            support = HypothesisSupport.MODERATE if "unknown" in predicates else HypothesisSupport.STRONG
            add(
                "interpretation.act.request_action", HypothesisDimension.COMMUNICATIVE_ACT,
                support, HypothesisDisposition.LEADING, "act.explicit-action-request.v1",
                supporting=refs_for_cue("conversation.explicit_action_request") + frame_evidence,
            )
            if question:
                add(
                    "interpretation.act.request_information", HypothesisDimension.COMMUNICATIVE_ACT,
                    HypothesisSupport.UNSUPPORTED, HypothesisDisposition.REJECTED,
                    "act.action-question-not-information.v1", supporting=(),
                    contradicting=refs_for_cue("conversation.explicit_action_request"),
                )
        elif correction:
            add(
                "interpretation.act.correct", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "act.explicit-correction.v1",
                supporting=refs_for_cue("conversation.explicit_correction") + frame_evidence,
            )
        elif lower.startswith("no thanks"):
            add(
                "interpretation.act.reject_or_decline", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "act.explicit-decline.v1",
            )
            add(
                "interpretation.act.correct", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.UNSUPPORTED, HypothesisDisposition.REJECTED,
                "act.decline-not-correction.v1", supporting=(),
            )
        elif lower.startswith("yes") and "submitted" in lower:
            add(
                "interpretation.act.confirm", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "act.explicit-confirmation.v1",
            )
            if question:
                add(
                    "interpretation.act.request_information", HypothesisDimension.COMMUNICATIVE_ACT,
                    HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                    "act.acknowledgement-request.v1",
                    supporting=refs_for_cue("conversation.question_form") + frame_evidence,
                )
        elif predicates == {"shared_progress"}:
            add(
                "interpretation.act.acknowledge", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                "act.shared-progress-acknowledgement.v1",
            )
            add(
                "interpretation.act.confirm", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                "act.shared-progress-confirmation.v1",
            )
        elif predicates == {"acknowledge_message"}:
            add(
                "interpretation.act.acknowledge", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "act.acknowledgement-token.v1",
            )
            add(
                "interpretation.act.confirm", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.WEAK, HypothesisDisposition.RETAINED_ALTERNATIVE,
                "act.acknowledgement-confirmation-alternative.v1", ambiguity_gate=1.0,
            )
        elif predicates & {"evaluate_meal", "evaluate_export_event"}:
            add(
                "interpretation.act.evaluate", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "act.explicit-evaluation.v1",
            )
        elif "social_opening" in predicates:
            add(
                "interpretation.act.greet", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "act.conventional-greeting.v1",
            )
        elif "social_closing" in predicates:
            add(
                "interpretation.act.close", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "act.conventional-closing.v1",
            )
        elif question:
            add(
                "interpretation.act.request_information", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "act.information-question.v1",
                supporting=refs_for_cue("conversation.question_form") + frame_evidence,
            )
        elif "unknown" not in predicates:
            add(
                "interpretation.act.inform", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.MODERATE if uncertainty else HypothesisSupport.STRONG,
                HypothesisDisposition.LEADING, "act.assertive-information.v1",
            )

        if predicates == {"send_report"} and not action:
            add(
                "interpretation.act.request_action", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.UNSUPPORTED, HypothesisDisposition.REJECTED,
                "act.assertion-not-action-request.v1", supporting=(),
                contradicting=refs_for_cue("conversation.explicit_action_request"),
            )
        if predicates == {"weather_condition"}:
            add(
                "interpretation.act.evaluate", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.UNSUPPORTED, HypothesisDisposition.REJECTED,
                "act.neutral-weather-not-evaluation.v1", supporting=(),
            )

        if "close_window" in predicates and not action:
            add(
                "interpretation.act.request_action", HypothesisDimension.COMMUNICATIVE_ACT,
                HypothesisSupport.MODERATE, HypothesisDisposition.RETAINED_ALTERNATIVE,
                "act.contextual-indirect-action.v1", ambiguity_gate=1.0,
            )

        # Speaker goals.
        act_keys = {item.hypothesis_key for item in candidates if item.dimension == HypothesisDimension.COMMUNICATIVE_ACT and item.support != HypothesisSupport.UNSUPPORTED}
        if "interpretation.act.request_information" in act_keys:
            add("interpretation.goal.obtain_information", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "goal.obtain-information.v1")
        if "interpretation.act.request_action" in act_keys:
            if predicates & {"stop_timer", "accept_offer"}:
                goal_key = "interpretation.goal.prevent_or_stop_action"
            else:
                goal_key = "interpretation.goal.cause_action"
            support = HypothesisSupport.MODERATE if not action or "unknown" in predicates else HypothesisSupport.STRONG
            disposition = HypothesisDisposition.RETAINED_ALTERNATIVE if not action else HypothesisDisposition.LEADING
            add(goal_key, HypothesisDimension.SPEAKER_GOAL, support, disposition,
                "goal.action-outcome.v1", ambiguity_gate=1.0 if not action else 0.0)
        if "interpretation.act.correct" in act_keys:
            add("interpretation.goal.repair_shared_understanding", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "goal.repair-understanding.v1")
        if "interpretation.act.reject_or_decline" in act_keys:
            add("interpretation.goal.prevent_or_stop_action", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "goal.prevent-offer-action.v1")
        if "interpretation.act.evaluate" in act_keys:
            add("interpretation.goal.express_stance", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "goal.express-stance.v1")
        if "interpretation.act.confirm" in act_keys and question:
            add("interpretation.goal.seek_acknowledgement", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                "goal.seek-acknowledgement.v1")
        if act_keys & {"interpretation.act.greet", "interpretation.act.close"}:
            add("interpretation.goal.maintain_social_connection", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                "goal.social-connection.v1")
        elif "interpretation.act.acknowledge" in act_keys:
            add("interpretation.goal.maintain_social_connection", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.WEAK, HypothesisDisposition.RETAINED_ALTERNATIVE,
                "goal.acknowledgement-bonding-alternative.v1", ambiguity_gate=1.0)
        if "interpretation.act.inform" in act_keys:
            shared_progress = predicates == {"shared_progress"}
            capability_conflict = predicates == {"capability_available"} and bool(contradiction_ids)
            add(
                "interpretation.goal.transfer_information", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.WEAK if shared_progress or capability_conflict else (
                    HypothesisSupport.MODERATE if uncertainty else HypothesisSupport.STRONG
                ),
                HypothesisDisposition.RETAINED_ALTERNATIVE if shared_progress or capability_conflict
                else HypothesisDisposition.LEADING,
                "goal.transfer-information.v1",
                ambiguity_gate=1.0 if shared_progress or capability_conflict else 0.0,
            )
        if predicates == {"shared_progress"}:
            add(
                "interpretation.goal.transfer_information", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.WEAK, HypothesisDisposition.RETAINED_ALTERNATIVE,
                "goal.shared-progress-information-alternative.v1", ambiguity_gate=1.0,
            )
            add(
                "interpretation.goal.maintain_social_connection", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.WEAK, HypothesisDisposition.RETAINED_ALTERNATIVE,
                "goal.shared-progress-bonding-alternative.v1", ambiguity_gate=1.0,
            )
        if predicates == {"weather_condition"}:
            add(
                "interpretation.goal.maintain_social_connection", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.UNSUPPORTED, HypothesisDisposition.REJECTED,
                "goal.isolated-weather-not-social.v1", supporting=(),
            )
        if "unknown" in predicates or (predicates == {"capability_available"} and contradiction_ids):
            unknown_refs = tuple(item.unknown_id for item in unknowns)
            add("interpretation.goal.unknown", HypothesisDimension.SPEAKER_GOAL,
                HypothesisSupport.STRONG if "unknown" in predicates else HypothesisSupport.MODERATE,
                HypothesisDisposition.LEADING,
                "goal.unknown-for-out-of-scope.v1", unknown_refs=unknown_refs)

        # Modalities.
        if action:
            add("interpretation.modality.requested", HypothesisDimension.MODALITY,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "modality.explicit-request.v1",
                supporting=refs_for_cue("conversation.explicit_action_request") + frame_evidence)
        elif uncertainty:
            add("interpretation.modality.tentative", HypothesisDimension.MODALITY,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "modality.speaker-uncertainty.v1",
                supporting=refs_for_cue("linguistic.uncertainty_marker") + frame_evidence)
        elif (lower.startswith("yes") and "submitted" in lower) or correction or (
            not question and predicates not in ({"acknowledge_message"}, {"social_opening"}, {"unknown"})
        ):
            support = HypothesisSupport.STRONG
            disposition = HypothesisDisposition.LEADING
            if lower.startswith("no thanks") or predicates == {"shared_progress"}:
                support = HypothesisSupport.MODERATE
            elif predicates == {"social_closing"}:
                support = HypothesisSupport.WEAK
                disposition = HypothesisDisposition.RETAINED_ALTERNATIVE
            add("interpretation.modality.asserted", HypothesisDimension.MODALITY,
                support, disposition, "modality.asserted-content.v1",
                ambiguity_gate=1.0 if disposition == HypothesisDisposition.RETAINED_ALTERNATIVE else 0.0)
        if predicates == {"acknowledge_message"}:
            add(
                "interpretation.modality.asserted", HypothesisDimension.MODALITY,
                HypothesisSupport.UNSUPPORTED, HypothesisDisposition.REJECTED,
                "modality.acknowledgement-not-assertion.v1", supporting=(),
                contradicting=frame_evidence,
            )
        if question and not action:
            add("interpretation.modality.questioned", HypothesisDimension.MODALITY,
                HypothesisSupport.STRONG if not lower.startswith("yes") else HypothesisSupport.MODERATE,
                HypothesisDisposition.LEADING, "modality.question-form.v1",
                supporting=refs_for_cue("conversation.question_form") + frame_evidence)
        if lower.startswith("if "):
            add("interpretation.modality.hypothetical", HypothesisDimension.MODALITY,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "modality.explicit-hypothetical.v1")
        if '"' in request.focus_message.content or "“" in request.focus_message.content:
            add("interpretation.modality.quoted", HypothesisDimension.MODALITY,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "modality.explicit-quotation.v1")

        # Pragmatic meaning. Shadow positive/negative cues are intentionally not visible here.
        if "unknown" in predicates:
            add("interpretation.pragmatic.unknown", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "pragmatic.unknown-content.v1", unknown_refs=tuple(item.unknown_id for item in unknowns))
        elif action and question:
            add("interpretation.pragmatic.indirect", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                "pragmatic.interrogative-action-request.v1", ambiguity_gate=0.0)
            add("interpretation.pragmatic.literal", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.WEAK, HypothesisDisposition.RETAINED_ALTERNATIVE,
                "pragmatic.literal-question-form-alternative.v1", ambiguity_gate=1.0)
        elif "close_window" in predicates and not action:
            add("interpretation.pragmatic.literal", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "pragmatic.literal-temperature-report.v1")
            add("interpretation.pragmatic.indirect", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.MODERATE, HypothesisDisposition.RETAINED_ALTERNATIVE,
                "pragmatic.indirect-window-request.v1", ambiguity_gate=1.0)
        elif predicates == {"acknowledge_message"} or predicates == {"shared_progress"}:
            add("interpretation.pragmatic.literal", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                "pragmatic.literal-acknowledgement.v1")
            add("interpretation.pragmatic.unknown", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.WEAK, HypothesisDisposition.RETAINED_ALTERNATIVE,
                "pragmatic.acknowledgement-alternative.v1",
                unknown_refs=tuple(item.unknown_id for item in unknowns), ambiguity_gate=1.0)
        elif predicates == {"capability_available"} and contradiction_ids:
            add("interpretation.pragmatic.literal", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                "pragmatic.literal-conflicted-capability-claim.v1")
        elif lower.startswith("yes") and "submitted" in lower:
            add("interpretation.pragmatic.literal", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.MODERATE, HypothesisDisposition.LEADING,
                "pragmatic.literal-confirmation-with-tag.v1")
        else:
            add("interpretation.pragmatic.literal", HypothesisDimension.PRAGMATIC_MEANING,
                HypothesisSupport.STRONG, HypothesisDisposition.LEADING,
                "pragmatic.literal-grounded-content.v1")

        # Current trusted runtime contradictions remain explicit and cannot be bias-adjusted.
        if contradiction_ids:
            candidates = [replace(item, contradiction_refs=contradiction_ids) for item in candidates]
        return tuple(candidates)
