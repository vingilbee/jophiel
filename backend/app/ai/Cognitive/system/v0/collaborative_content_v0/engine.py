"""Collaborative Content Production V0 (``collaborative-content-engine-v0.1``).

For each conversational-deliverable (or inquiry-answer) goal of the turn that
requirement resolution found ready, build the bounded payload, make exactly
one structured provider call, and compile the candidate deterministically.
A blocked goal is skipped without a call.  A provider or compiler failure is
a typed ``CollaborationGoalOutcome`` failure: there is no retry here, no
fallback to another model, and never a fallback to Realization reasoning.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from ..contracts.actions import ActionPersonalityProfile
from ..contracts.collaboration import (
    CollaborationFailure, CollaborationFailureCode as F, CollaborationGoalOutcome, CollaborationSkipReason,
    InquiryAnswerBinding, WorkspaceResolution, WorkspaceTargetBinding, WorkspaceTargetStatus as T,
)
from ..contracts.interpretation import InterpretationEnvelope, SemanticFrame
from ..contracts.workspace import ArtifactOperation, OpenInquiryRecord
from ..interpretation_v0.models import InterpretationDialogueContext
from ..perception_v0.models import PerceptionMessage
from .compiler import CollaborationCompileError, CollaborationCompilerV0, CompileContext
from .model_protocol import (
    CollaborationArtifactView, CollaborationCandidateModel, CollaborationModelPayload, CollaborationProviderError,
    vocabulary_for,
)
from .sources import build_sources, context_messages, element_labels

ENGINE_VERSION = "collaborative-content-engine-v0.1"
READY_TARGETS = frozenset({T.NOT_REQUIRED, T.RESOLVED_ARTIFACT, T.RESOLVED_DIALOGUE})


@dataclass(frozen=True)
class CollaborationGoalInput:
    goal_ref: str
    frame: SemanticFrame
    target: WorkspaceTargetBinding
    blocked: bool
    answer: InquiryAnswerBinding | None = None
    inquiry: OpenInquiryRecord | None = None


def curiosity_band(value: float | None) -> str:
    if value is None:
        return "moderate"
    return "low" if value < 0.35 else "high" if value > 0.65 else "moderate"


class CollaborativeContentEngine:
    """One model, one call per ready goal, deterministic compilation."""

    version = ENGINE_VERSION

    def __init__(self, model: CollaborationCandidateModel | None = None, *,
                 unavailable_code: F = F.PROVIDER_NOT_CONFIGURED) -> None:
        self.model = model
        self.unavailable_code = unavailable_code
        self.compiler = CollaborationCompilerV0()

    def produce(
        self, *, cognitive_turn_id: str, conversation_ref: str, goals: tuple[CollaborationGoalInput, ...],
        workspace: WorkspaceResolution, interpretation: InterpretationEnvelope,
        dialogue_context: InterpretationDialogueContext | None, messages: tuple[PerceptionMessage, ...],
        personality: ActionPersonalityProfile,
    ) -> tuple[CollaborationGoalOutcome, ...]:
        out = []
        for goal in sorted(goals, key=lambda item: item.goal_ref):
            out.append(self._one(goal, cognitive_turn_id=cognitive_turn_id, conversation_ref=conversation_ref,
                                 workspace=workspace, interpretation=interpretation,
                                 dialogue_context=dialogue_context, messages=messages, personality=personality))
        return tuple(out)

    def _one(self, goal, *, cognitive_turn_id, conversation_ref, workspace, interpretation, dialogue_context, messages,
             personality) -> CollaborationGoalOutcome:
        if goal.blocked or goal.target.status not in READY_TARGETS:
            return CollaborationGoalOutcome(goal_ref=goal.goal_ref, status="skipped",
                                            skip_reason=CollaborationSkipReason.GOAL_BLOCKED)
        if goal.answer is not None and goal.answer.answer_kind == "declined":
            return CollaborationGoalOutcome(goal_ref=goal.goal_ref, status="skipped",
                                            skip_reason=CollaborationSkipReason.ANSWER_DECLINED)
        if self.model is None:
            return self._failed(goal, "provider", self.unavailable_code)
        target = goal.target
        snapshot = workspace.snapshot
        base = snapshot.revision(target.base_revision_ref) if target.base_revision_ref else None
        sources = build_sources(frame=goal.frame, interpretation=interpretation, target=target, base=base,
                                dialogue_context=dialogue_context, answer=goal.answer, inquiry=goal.inquiry)
        curiosity = personality.curiosity if personality.status.value == "known" else None
        try:
            payload = CollaborationModelPayload(
                operation=target.operation, artifact_type=target.artifact_type,
                requested_form=goal.frame.arguments.get("requested_form") if isinstance(
                    goal.frame.arguments.get("requested_form"), str) else None,
                target_kind="revision" if base is not None else (
                    "dialogue" if target.status == T.RESOLVED_DIALOGUE else "new"
                ),
                artifact=CollaborationArtifactView(
                    artifact_ref=base.artifact_id, artifact_type=base.artifact_type, revision_ref=base.revision_id,
                    revision_number=base.revision_number, title=base.title,
                    payload=base.payload.model_dump(mode="json"), element_ids=tuple(sorted(element_labels(base))),
                ) if base is not None else None,
                sources=sources.sources, messages=context_messages(messages),
                vocabulary=vocabulary_for(target.operation, target.artifact_type),
                curiosity=curiosity_band(curiosity),
            )
        except (ValidationError, ValueError):
            return self._failed(goal, "compiler", F.CONTENT_INVALID, "payload")
        provenance = getattr(self.model, "provenance", None)
        try:
            output = self.model.generate(payload)
        except CollaborationProviderError as exc:
            return self._failed(goal, "provider", exc.code, provenance=provenance)
        except (ValidationError, ValueError, TypeError):
            return self._failed(goal, "provider", F.PROVIDER_OUTPUT_INVALID, provenance=provenance)
        candidate = output.model_dump(mode="json")
        context = CompileContext(
            cognitive_turn_id=cognitive_turn_id, conversation_ref=conversation_ref, goal_ref=goal.goal_ref,
            frame_ref=goal.frame.frame_id, operation=target.operation, artifact_type=target.artifact_type,
            target=target, base=base, sources=sources, inquiry_history=snapshot.inquiries, curiosity=curiosity,
            max_inquiries=payload.max_inquiries,
        )
        try:
            from ..contracts.collaboration import COLLABORATION_COMPILER_VERSION_V0_2
            tolerant = getattr(provenance, "compiler_version", None) == COLLABORATION_COMPILER_VERSION_V0_2
            proposal = self.compiler.compile(context, output, candidate, tolerant=tolerant)
        except CollaborationCompileError as exc:
            return self._failed(goal, "compiler", exc.code, exc.subject, candidate=candidate, provenance=provenance)
        return CollaborationGoalOutcome(goal_ref=goal.goal_ref, status="proposed", proposal=proposal,
                                        candidate=candidate, provenance=provenance)

    @staticmethod
    def _failed(goal, stage, code: F, subject: str = "", *, candidate=None, provenance=None) -> CollaborationGoalOutcome:
        from ..contracts.collaboration import RETRYABLE_COLLABORATION_FAILURES

        return CollaborationGoalOutcome(
            goal_ref=goal.goal_ref, status="failed",
            failure=CollaborationFailure(goal_ref=goal.goal_ref, stage=stage, code=code,
                                         retryable=code in RETRYABLE_COLLABORATION_FAILURES, subject=subject),
            candidate=candidate, provenance=provenance,
        )


ModelFactory = Callable[[], CollaborationCandidateModel | None]

__all__ = ["CollaborationGoalInput", "CollaborativeContentEngine", "ENGINE_VERSION", "ModelFactory",
           "READY_TARGETS", "curiosity_band"]
