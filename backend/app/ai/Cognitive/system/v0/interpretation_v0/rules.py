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
from .ontology import ActFamily, act_family, embedding_of
from .semantic_grounding import contribution_content

# v0.2 composes conventional phatic acts (greeting, acknowledgement) with the
# act of the rest of the contribution instead of letting one exclude the other;
# each act hypothesis cites only its own frames (SH-EVAL-003).  Contributions
# with a single kind of act evaluate exactly as in v0.1.
# v0.3 makes validated semantic frames the primary act evidence (SH-EVAL-012):
# when every frame is model-grounded and belongs to a predicate with a
# declared act family (or is an explicit ``unknown``), acts,
# goals, and modalities come from the frames, and a Perception cue that
# disagrees only adds a weak retained alternative (or a rejection) citing the
# cue.  Quoted, reported, and hypothetical frames never become the speaker's
# own request.  Legacy design-case predicates keep the v0.2 cue-driven rules.
# v0.4 (registry v0.4): a collaborative-content frame grounds the same
# request act, cause-action goal, and requested modality as an action
# request (its requested outcome, not its act, tells them apart downstream),
# cited with its own rule ids; an inquiry-answer frame grounds an inform act
# and a transfer-information goal.  Contributions without these predicates
# evaluate exactly as in v0.3.
HYPOTHESIS_RULES_VERSION = "interpretation-hypothesis-rules-v0.4"
PHATIC_PREDICATES = frozenset({"social_opening", "acknowledge_message"})


_MODEL_COMPILER_PREFIX = "interpretation-semantic-model-compiler-"


def _model_grounded(frame: SemanticFrame) -> bool:
    """Frames from the offline lexical grounder are derived from the same
    surface form as the Perception cues, so they are not independent act
    evidence and keep the cue-driven rules."""

    return any(rule.startswith(_MODEL_COMPILER_PREFIX) for rule in frame.grounding_rule_ids)


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
        if frames and all(_model_grounded(item) for item in frames) and any(
            act_family(item.predicate) for item in frames
        ) and all(act_family(item.predicate) or item.predicate == "unknown" for item in frames):
            return self._evaluate_frames(request, evidence, frames, unknowns, contradiction_ids)
        phatic = tuple(item for item in frames if item.predicate in PHATIC_PREDICATES)
        core = tuple(item for item in frames if item.predicate not in PHATIC_PREDICATES)
        if not phatic or not core:
            return self._evaluate(request, evidence, frames, unknowns, contradiction_ids)
        # Compound contribution: the non-phatic act is evaluated on its own
        # frames, and each conventional phatic act is added beside it.
        candidates = list(self._evaluate(request, evidence, core, unknowns, contradiction_ids))
        candidates.extend(self._phatic(phatic, contradiction_ids))
        return tuple(candidates)

    @staticmethod
    def _phatic(frames: tuple[SemanticFrame, ...], contradiction_ids: tuple[str, ...]) -> list[HypothesisCandidate]:
        out: list[HypothesisCandidate] = []
        for predicate, act, act_rule, goal in (
            ("social_opening", "interpretation.act.greet", "act.conventional-greeting.v1",
             ("interpretation.goal.maintain_social_connection", HypothesisSupport.MODERATE, "goal.social-connection.v1")),
            ("acknowledge_message", "interpretation.act.acknowledge", "act.acknowledgement-token.v1", None),
        ):
            owned = tuple(item for item in frames if item.predicate == predicate)
            if not owned:
                continue
            refs = tuple(item.frame_id for item in owned)
            evidence = tuple(dict.fromkeys(ref for item in owned for ref in item.evidence_refs))
            out.append(HypothesisCandidate(
                hypothesis_key=act, dimension=HypothesisDimension.COMMUNICATIVE_ACT,
                base_support=HypothesisSupport.STRONG, support=HypothesisSupport.STRONG,
                disposition=HypothesisDisposition.LEADING, semantic_frame_refs=refs,
                supporting_evidence_refs=evidence, contradiction_refs=contradiction_ids, rule_ids=(act_rule,),
            ))
            if goal is not None:
                key, support, rule = goal
                out.append(HypothesisCandidate(
                    hypothesis_key=key, dimension=HypothesisDimension.SPEAKER_GOAL, base_support=support,
                    support=support, disposition=HypothesisDisposition.LEADING, semantic_frame_refs=refs,
                    supporting_evidence_refs=evidence, contradiction_refs=contradiction_ids, rule_ids=(rule,),
                ))
        return out

    def _evaluate_frames(
        self,
        request: InterpretationRequest,
        evidence: tuple[InterpretationEvidence, ...],
        frames: tuple[SemanticFrame, ...],
        unknowns: tuple[UnknownInput, ...],
        contradiction_ids: tuple[str, ...],
    ) -> tuple[HypothesisCandidate, ...]:
        """Frame-dominant rules: each validated frame grounds its own act.

        Precedence: a direct frame's act family decides the act.  A present
        Perception cue that agrees is cited as supporting evidence; one that
        disagrees adds the cue's act as a weak retained alternative (or, for an
        interrogative action request, the documented rejection), so a better
        parser can repair a missed shallow cue without losing the cue's
        reading.  Embedded (quoted, reported, hypothetical) frames are content
        the speaker relays, never the speaker's own request.
        """

        cues = {item.cue_key: item for item in request.perception.cue_activations}
        cue_records = evidence_by_cue(evidence)
        candidates: list[HypothesisCandidate] = []
        D, S, P = HypothesisDimension, HypothesisSupport, HypothesisDisposition

        def cue_present(key: str) -> bool:
            return key in cues and cues[key].state.value == "present"

        def cue_refs(key: str) -> tuple[str, ...]:
            record = cue_records.get(key)
            return (record.evidence_id,) if record and cue_present(key) else ()

        def refs_of(owned: tuple[SemanticFrame, ...]) -> tuple[str, ...]:
            return tuple(dict.fromkeys(ref for item in owned for ref in item.evidence_refs))

        def add(
            key: str, dimension: HypothesisDimension, support: HypothesisSupport,
            disposition: HypothesisDisposition, rule: str, owned: tuple[SemanticFrame, ...], *,
            supporting: tuple[str, ...] | None = None, contradicting: tuple[str, ...] = (),
            unknown_refs: tuple[str, ...] = (), ambiguity_gate: float = 0.0,
        ) -> None:
            candidates.append(HypothesisCandidate(
                hypothesis_key=key, dimension=dimension, base_support=support, support=support,
                disposition=disposition, semantic_frame_refs=tuple(item.frame_id for item in owned),
                supporting_evidence_refs=refs_of(owned) if supporting is None else supporting,
                contradicting_evidence_refs=contradicting, unknown_refs=unknown_refs,
                contradiction_refs=contradiction_ids, rule_ids=(rule,), ambiguity_gate=ambiguity_gate,
            ))

        direct = tuple(item for item in frames if act_family(item.predicate) and embedding_of(item) == "direct")
        embedded = tuple(item for item in frames if act_family(item.predicate) and embedding_of(item) != "direct")
        unknown_frames = tuple(item for item in frames if item.predicate == "unknown")
        grouped: dict[ActFamily, tuple[SemanticFrame, ...]] = {}
        for item in direct:
            family = act_family(item.predicate)
            assert family is not None
            grouped[family] = grouped.get(family, ()) + (item,)

        action_cue = cue_present("conversation.explicit_action_request")
        question_cue = cue_present("conversation.question_form")
        correction_cue = cue_present("conversation.explicit_correction")
        uncertainty = cue_present("linguistic.uncertainty_marker")
        unknown_ids = tuple(item.unknown_id for item in unknowns)

        information = grouped.get(ActFamily.REQUEST_INFORMATION, ())
        social_question = grouped.get(ActFamily.SOCIAL_QUESTION, ())
        external = grouped.get(ActFamily.REQUEST_ACTION, ())
        content_requests = grouped.get(ActFamily.REQUEST_CONTENT, ())
        # Both kinds are directives the speaker wants carried out; one request
        # act cites every such frame.
        action = external + content_requests
        correction = grouped.get(ActFamily.CORRECT, ())
        answers = grouped.get(ActFamily.ANSWER_INQUIRY, ())
        inform = grouped.get(ActFamily.INFORM, ()) + answers
        greet = grouped.get(ActFamily.GREET, ())
        close = grouped.get(ActFamily.CLOSE, ())
        acknowledge = grouped.get(ActFamily.ACKNOWLEDGE, ())
        thank = grouped.get(ActFamily.THANK, ())

        # Communicative acts and the goals each act grounds.
        asking = information + social_question
        if asking:
            add("interpretation.act.request_information", D.COMMUNICATIVE_ACT, S.STRONG, P.LEADING,
                "act.information-question.v1", asking,
                supporting=refs_of(asking) + cue_refs("conversation.question_form"))
        if information:
            add("interpretation.goal.obtain_information", D.SPEAKER_GOAL, S.STRONG, P.LEADING,
                "goal.obtain-information.v1", information)
        if social_question:
            add("interpretation.act.greet", D.COMMUNICATIVE_ACT, S.MODERATE, P.LEADING,
                "act.phatic-social-question.v1", social_question)
            add("interpretation.goal.maintain_social_connection", D.SPEAKER_GOAL, S.STRONG, P.LEADING,
                "goal.social-question-connection.v1", social_question)
            if not information:
                add("interpretation.goal.obtain_information", D.SPEAKER_GOAL, S.MODERATE, P.LEADING,
                    "goal.social-question-information.v1", social_question)
        if action:
            negative = tuple(item for item in action if item.polarity == "negative")
            positive = tuple(item for item in action if item.polarity != "negative")
            weakened = any(
                item.unresolved_arguments or item.predicate == "unsafe_request" for item in action
            ) or bool(unknown_frames)
            act_rule = "act.frame-content-request.v1" if not external else "act.frame-action-request.v1"
            add("interpretation.act.request_action", D.COMMUNICATIVE_ACT,
                S.MODERATE if weakened else S.STRONG, P.LEADING, act_rule, action,
                supporting=refs_of(action) + cue_refs("conversation.explicit_action_request"))
            if positive:
                # A negated request beside a positive one ("send X, but don't
                # include Y") scopes that request; it is not a competing
                # prevent-action goal, which the ontology marks as conflicting.
                add("interpretation.goal.cause_action", D.SPEAKER_GOAL,
                    S.MODERATE if weakened else S.STRONG, P.LEADING,
                    "goal.negative-scope-of-positive-request.v1" if negative else (
                        "goal.content-outcome.v1" if not external else "goal.action-outcome.v1"
                    ),
                    positive + negative)
            else:
                add("interpretation.goal.prevent_or_stop_action", D.SPEAKER_GOAL, S.STRONG, P.LEADING,
                    "goal.negative-action-request.v1", negative)
            add("interpretation.modality.requested", D.MODALITY, S.STRONG, P.LEADING,
                "modality.explicit-request.v1", action,
                supporting=refs_of(action) + cue_refs("conversation.explicit_action_request"))
            if question_cue and not asking:
                add("interpretation.act.request_information", D.COMMUNICATIVE_ACT, S.UNSUPPORTED, P.REJECTED,
                    "act.action-question-not-information.v1", action, supporting=(),
                    contradicting=refs_of(action))
        if correction:
            add("interpretation.act.correct", D.COMMUNICATIVE_ACT, S.STRONG, P.LEADING,
                "act.explicit-correction.v1", correction,
                supporting=refs_of(correction) + cue_refs("conversation.explicit_correction"))
            add("interpretation.goal.repair_shared_understanding", D.SPEAKER_GOAL, S.STRONG, P.LEADING,
                "goal.repair-understanding.v1", correction)
        if inform:
            level = S.MODERATE if uncertainty else S.STRONG
            add("interpretation.act.inform", D.COMMUNICATIVE_ACT, level, P.LEADING,
                "act.inquiry-answer.v1" if answers and len(answers) == len(inform) else "act.assertive-information.v1",
                inform)
            add("interpretation.goal.transfer_information", D.SPEAKER_GOAL, level, P.LEADING,
                "goal.transfer-information.v1", inform)
        if greet:
            add("interpretation.act.greet", D.COMMUNICATIVE_ACT, S.STRONG, P.LEADING,
                "act.conventional-greeting.v1", greet)
        if close:
            add("interpretation.act.close", D.COMMUNICATIVE_ACT, S.STRONG, P.LEADING,
                "act.conventional-closing.v1", close)
        if greet or close:
            add("interpretation.goal.maintain_social_connection", D.SPEAKER_GOAL, S.MODERATE, P.LEADING,
                "goal.social-connection.v1", greet + close)
        if acknowledge:
            add("interpretation.act.acknowledge", D.COMMUNICATIVE_ACT, S.STRONG, P.LEADING,
                "act.acknowledgement-token.v1", acknowledge)
            if set(grouped) == {ActFamily.ACKNOWLEDGE}:
                add("interpretation.act.confirm", D.COMMUNICATIVE_ACT, S.WEAK, P.RETAINED_ALTERNATIVE,
                    "act.acknowledgement-confirmation-alternative.v1", acknowledge, ambiguity_gate=1.0)
                add("interpretation.goal.maintain_social_connection", D.SPEAKER_GOAL, S.WEAK,
                    P.RETAINED_ALTERNATIVE, "goal.acknowledgement-bonding-alternative.v1", acknowledge,
                    ambiguity_gate=1.0)
        if thank:
            add("interpretation.act.acknowledge", D.COMMUNICATIVE_ACT, S.STRONG, P.LEADING,
                "act.expressed-gratitude.v1", thank)
            add("interpretation.goal.maintain_social_connection", D.SPEAKER_GOAL, S.MODERATE, P.LEADING,
                "goal.gratitude-connection.v1", thank)

        # Embedded content is never the speaker's request.  Alone it is relayed
        # information; beside a direct act it is that act's content.
        relayed_requests = tuple(
            item for item in embedded
            if act_family(item.predicate) in {ActFamily.REQUEST_ACTION, ActFamily.REQUEST_CONTENT}
        )
        if embedded:
            if not direct:
                add("interpretation.act.inform", D.COMMUNICATIVE_ACT, S.MODERATE, P.LEADING,
                    "act.relayed-embedded-content.v1", embedded)
                add("interpretation.goal.transfer_information", D.SPEAKER_GOAL, S.MODERATE, P.LEADING,
                    "goal.transfer-information.v1", embedded)
            if relayed_requests and not action:
                add("interpretation.act.request_action", D.COMMUNICATIVE_ACT, S.UNSUPPORTED, P.REJECTED,
                    "act.embedded-request-not-speaker-request.v1", relayed_requests, supporting=(),
                    contradicting=refs_of(relayed_requests))
            quoted = tuple(item for item in embedded if embedding_of(item) in {"quoted", "reported"})
            imagined = tuple(item for item in embedded if embedding_of(item) == "hypothetical")
            if quoted:
                add("interpretation.modality.quoted", D.MODALITY, S.STRONG, P.LEADING,
                    "modality.explicit-quotation.v1", quoted)
            if imagined:
                add("interpretation.modality.hypothetical", D.MODALITY, S.STRONG, P.LEADING,
                    "modality.explicit-hypothetical.v1", imagined)

        # Cue disagreement: the frame wins; the cue's reading is retained, weak.
        # A cue explained by an embedded request frame is not retained.
        if action_cue and not action and not relayed_requests:
            cue = cue_refs("conversation.explicit_action_request")
            owned = asking or direct or frames
            add("interpretation.act.request_action", D.COMMUNICATIVE_ACT, S.WEAK, P.RETAINED_ALTERNATIVE,
                "act.cue-action-alternative-to-frame.v1", owned, supporting=cue + refs_of(owned),
                ambiguity_gate=1.0)
        if correction_cue and not correction:
            cue = cue_refs("conversation.explicit_correction")
            owned = direct or frames
            add("interpretation.act.correct", D.COMMUNICATIVE_ACT, S.WEAK, P.RETAINED_ALTERNATIVE,
                "act.cue-correction-alternative-to-frame.v1", owned, supporting=cue + refs_of(owned),
                ambiguity_gate=1.0)

        # Unknown content stays unknown beside whatever was grounded.
        if unknown_frames:
            add("interpretation.goal.unknown", D.SPEAKER_GOAL, S.STRONG, P.LEADING,
                "goal.unknown-for-out-of-scope.v1", unknown_frames, unknown_refs=unknown_ids)

        # Modalities.
        if asking:
            add("interpretation.modality.questioned", D.MODALITY, S.STRONG, P.LEADING,
                "modality.question-form.v1", asking,
                supporting=refs_of(asking) + cue_refs("conversation.question_form"))
        only_acknowledgement = set(grouped) == {ActFamily.ACKNOWLEDGE} and not embedded and not unknown_frames
        if only_acknowledgement:
            add("interpretation.modality.asserted", D.MODALITY, S.UNSUPPORTED, P.REJECTED,
                "modality.acknowledgement-not-assertion.v1", acknowledge, supporting=(),
                contradicting=refs_of(acknowledge))
        if close and not (correction or inform):
            add("interpretation.modality.asserted", D.MODALITY, S.WEAK, P.RETAINED_ALTERNATIVE,
                "modality.asserted-content.v1", close, ambiguity_gate=1.0)
        asserting = correction + inform
        if asserting:
            if uncertainty:
                add("interpretation.modality.tentative", D.MODALITY, S.STRONG, P.LEADING,
                    "modality.speaker-uncertainty.v1", asserting,
                    supporting=refs_of(asserting) + cue_refs("linguistic.uncertainty_marker"))
            else:
                add("interpretation.modality.asserted", D.MODALITY, S.STRONG, P.LEADING,
                    "modality.asserted-content.v1", asserting)
        content = request.focus_message.content
        if ('"' in content or "\u201c" in content) and not any(
            embedding_of(item) in {"quoted", "reported"} for item in embedded
        ):
            add("interpretation.modality.quoted", D.MODALITY, S.MODERATE, P.RETAINED_ALTERNATIVE,
                "modality.surface-quotation-alternative.v1", frames, ambiguity_gate=1.0)

        # Pragmatic meaning: exactly one leading reading among conflicting keys.
        grounded = direct + embedded
        if not grounded:
            add("interpretation.pragmatic.unknown", D.PRAGMATIC_MEANING, S.STRONG, P.LEADING,
                "pragmatic.unknown-content.v1", unknown_frames, unknown_refs=unknown_ids)
        elif action and question_cue:
            add("interpretation.pragmatic.indirect", D.PRAGMATIC_MEANING, S.MODERATE, P.LEADING,
                "pragmatic.interrogative-action-request.v1", action)
            add("interpretation.pragmatic.literal", D.PRAGMATIC_MEANING, S.WEAK, P.RETAINED_ALTERNATIVE,
                "pragmatic.literal-question-form-alternative.v1", action, ambiguity_gate=1.0)
        elif only_acknowledgement:
            add("interpretation.pragmatic.literal", D.PRAGMATIC_MEANING, S.MODERATE, P.LEADING,
                "pragmatic.literal-acknowledgement.v1", acknowledge)
            add("interpretation.pragmatic.unknown", D.PRAGMATIC_MEANING, S.WEAK, P.RETAINED_ALTERNATIVE,
                "pragmatic.acknowledgement-alternative.v1", acknowledge, unknown_refs=unknown_ids,
                ambiguity_gate=1.0)
        else:
            add("interpretation.pragmatic.literal", D.PRAGMATIC_MEANING, S.STRONG, P.LEADING,
                "pragmatic.literal-grounded-content.v1", grounded)
            if unknown_frames:
                add("interpretation.pragmatic.unknown", D.PRAGMATIC_MEANING, S.WEAK, P.RETAINED_ALTERNATIVE,
                    "pragmatic.partial-unknown-content.v1", unknown_frames, unknown_refs=unknown_ids,
                    ambiguity_gate=1.0)
        return tuple(candidates)

    def _evaluate(
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
        # A leading greeting, addressee, or discourse marker never changes
        # which act the rest of the contribution performs.
        lower = contribution_content(request.focus_message.content).casefold().strip()
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
