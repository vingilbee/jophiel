"""Closed Goal V0 formation manifest: which validated acts form which goals.

Formation used to test broad hypothesis-key sets over a whole scenario and then
merge arguments from every frame the scenario carried, so in a compound
contribution one act's frames could leak into another act's goal.  Each entry
here names the communicative act and speaker goal that form one definition,
which frames of that act may bind it, and exactly how each requirement target
and parameter is derived.  A candidate is formed per eligible act-local frame;
arguments are copied only from that frame and are never invented.

Four definitions do not come from a user act; they are declared here too (with
their formation source) so the manifest is closed over the ontology:

- ``response_obligation`` - the trusted runtime obligation of a response turn;
- ``blocking_argument`` - an exact unresolved argument of a formed parent;
- ``contradiction`` - an active contradiction a retained scenario cites;
- ``runtime_tool_event`` - a failed or unavailable trusted tool event.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field, model_validator

from ..contracts.goals import GOAL_FORMATION_POLICY_VERSION, GoalOwner, GoalPriorityBand
from ..contracts.interpretation import HYPOTHESIS_DIMENSION_BY_KEY, FrozenModel, HypothesisDimension
from ..interpretation_v0.ontology import ActFamily
from .ontology import all_definitions
from .rules import PROPOSAL_RULE_IDS

# No predicate is excluded from formation: a candidate bound to an
# ``unknown`` frame is formed and then rejected by its adoption policy as
# ungrounded, so the blocked request stays visible to Action as a typed
# planning condition instead of silently disappearing.
EXCLUDED_PREDICATES: tuple[str, ...] = ()


class GoalTargetBinding(FrozenModel):
    """How one requirement target role is bound."""

    role: str = Field(min_length=1)
    # ``frame_argument``: the named argument of the candidate's own frame, or
    # the frame itself when the predicate carries no such argument.
    source: Literal["frame_argument", "conversation_ref", "task_ref"]
    argument: str | None = None

    @model_validator(mode="after")
    def valid(self) -> GoalTargetBinding:
        if (self.source == "frame_argument") != (self.argument is not None):
            raise ValueError("only a frame-argument binding names an argument")
        return self


class GoalParameterBinding(FrozenModel):
    """How one requirement parameter is bound.

    An absent argument becomes a stable identifier derived from the
    interpretation (or frame) id - a reference placeholder, never content.
    ``unresolved_sentinel_argument``: when that frame argument is unresolved,
    the parameter is the literal ``unresolved_effect`` marker instead.
    """

    name: str = Field(min_length=1)
    argument: str = Field(min_length=1)
    placeholder_base: Literal["interpretation", "frame"]
    unresolved_sentinel_argument: str | None = None


class GoalFormationEntry(FrozenModel):
    definition_key: str = Field(min_length=1)
    proposal_rule_id: str = Field(min_length=1)
    formation_source: Literal[
        "semantic_act", "response_obligation", "blocking_argument", "contradiction", "runtime_tool_event",
    ]
    owner: GoalOwner
    # ``task_or_turn``: the trusted task, or this turn when none is supplied.
    # ``contact_scope_argument``: the frame's ``contact_scope`` (conversation
    # or turn), defaulting to turn.
    scope: Literal["turn", "conversation", "task", "task_or_turn", "contact_scope_argument"]
    act_hypothesis_key: str | None = None
    speaker_goal_hypothesis_key: str | None = None
    # A registered predicate binds only when its declared act family is listed;
    # an unregistered (reviewed design-case) predicate is bound through the act
    # hypothesis that cites it.
    frame_act_families: tuple[ActFamily, ...] = ()
    # Formation v0.5: bind only frames whose registered act family is listed.
    # A frame without a family (``unknown`` or a reviewed design-case
    # predicate) never forms such a goal, so a content deliverable or an
    # inquiry answer exists only for its own validated predicate.
    strict_frame_families: bool = False
    excluded_predicates: tuple[str, ...] = EXCLUDED_PREDICATES
    # Negative frames beside a positive request scope that request; they do
    # not form a second goal of the same definition.
    scoping_polarity: str | None = None
    target_bindings: tuple[GoalTargetBinding, ...] = ()
    parameter_bindings: tuple[GoalParameterBinding, ...] = ()
    # A frame argument whose value (an existing goal id) is cited as a source.
    source_argument: str | None = None
    requires_task_ref: bool = False
    # Forms only when no trusted task binding exists: with a trusted task the
    # committing task goal represents the request instead (formation v0.4).
    requires_absent_task_ref: bool = False
    # Priority when this frame argument is unresolved (the goal cannot yet be
    # pursued at its default band).
    unresolved_priority_argument: str | None = None
    unresolved_priority_band: GoalPriorityBand | None = None
    priority_band: GoalPriorityBand | None = None

    @model_validator(mode="after")
    def valid(self) -> GoalFormationEntry:
        if self.proposal_rule_id not in PROPOSAL_RULE_IDS:
            raise ValueError("formation entry cites an unregistered proposal rule")
        semantic = self.formation_source == "semantic_act"
        if semantic != (self.act_hypothesis_key is not None):
            raise ValueError("exactly the semantic-act entries name an act hypothesis")
        if semantic and (not self.speaker_goal_hypothesis_key or not self.frame_act_families):
            raise ValueError("a semantic-act entry names its speaker goal and frame act families")
        if self.act_hypothesis_key is not None and (
            HYPOTHESIS_DIMENSION_BY_KEY.get(self.act_hypothesis_key) != HypothesisDimension.COMMUNICATIVE_ACT
        ):
            raise ValueError("act hypothesis key is not a communicative act")
        if self.speaker_goal_hypothesis_key is not None and (
            HYPOTHESIS_DIMENSION_BY_KEY.get(self.speaker_goal_hypothesis_key) != HypothesisDimension.SPEAKER_GOAL
        ):
            raise ValueError("speaker goal hypothesis key is not a speaker goal")
        if (self.unresolved_priority_argument is None) != (self.unresolved_priority_band is None):
            raise ValueError("an unresolved-priority argument and band are declared together")
        if self.scope in {"task"} and not self.requires_task_ref:
            raise ValueError("a task-scoped entry requires the trusted task reference")
        if self.requires_task_ref and self.requires_absent_task_ref:
            raise ValueError("an entry cannot both require and exclude a trusted task")
        if self.requires_absent_task_ref and any(item.source == "task_ref" for item in self.target_bindings):
            raise ValueError("an entry formed without a trusted task cannot bind one")
        return self


def _frame(role: str, argument: str) -> GoalTargetBinding:
    return GoalTargetBinding(role=role, source="frame_argument", argument=argument)


_ENTRIES = (
    GoalFormationEntry(
        definition_key="interaction.discharge_response_obligation",
        proposal_rule_id="goal-propose.required-response-obligation.v1",
        formation_source="response_obligation", owner=GoalOwner.SYSTEM, scope="turn",
    ),
    GoalFormationEntry(
        definition_key="interaction.answer_information_request",
        proposal_rule_id="goal-propose.explicit-information-request.v1",
        formation_source="semantic_act", owner=GoalOwner.USER, scope="turn",
        act_hypothesis_key="interpretation.act.request_information",
        speaker_goal_hypothesis_key="interpretation.goal.obtain_information",
        frame_act_families=(ActFamily.REQUEST_INFORMATION,),
        target_bindings=(_frame("topic_ref", "topic_ref"),),
        parameter_bindings=(GoalParameterBinding(name="answer_scope", argument="answer_scope", placeholder_base="interpretation"),),
        source_argument="prior_goal_ref",
    ),
    GoalFormationEntry(
        definition_key="task.complete_authorized_request",
        proposal_rule_id="goal-propose.explicit-authorized-action-request.v1",
        formation_source="semantic_act", owner=GoalOwner.USER, scope="task",
        act_hypothesis_key="interpretation.act.request_action",
        speaker_goal_hypothesis_key="interpretation.goal.cause_action",
        frame_act_families=(ActFamily.REQUEST_ACTION,),
        scoping_polarity="negative",
        target_bindings=(GoalTargetBinding(role="task_ref", source="task_ref"),),
        parameter_bindings=(GoalParameterBinding(
            name="requested_effect", argument="requested_effect", placeholder_base="frame",
            unresolved_sentinel_argument="subject_ref",
        ),),
        requires_task_ref=True,
        unresolved_priority_argument="subject_ref", unresolved_priority_band=GoalPriorityBand.MODERATE,
    ),
    # Formation v0.4: the non-committing handler of an explicit request.  It
    # binds the request frame itself, keeps the requested effect, and never
    # requires a trusted task -- feasibility is resolved downstream.
    GoalFormationEntry(
        definition_key="interaction.handle_action_request",
        proposal_rule_id="goal-propose.explicit-action-request-handling.v1",
        formation_source="semantic_act", owner=GoalOwner.USER, scope="turn",
        act_hypothesis_key="interpretation.act.request_action",
        speaker_goal_hypothesis_key="interpretation.goal.cause_action",
        frame_act_families=(ActFamily.REQUEST_ACTION,),
        scoping_polarity="negative",
        target_bindings=(_frame("request_ref", "request_ref"),),
        parameter_bindings=(GoalParameterBinding(
            name="requested_effect", argument="requested_effect", placeholder_base="frame",
        ),),
        requires_absent_task_ref=True,
    ),
    # Formation v0.5: content requested inside the conversation.  It binds
    # the request frame itself plus the frame's closed operation and artifact
    # type; the artifact target is resolved by the conversational workspace.
    GoalFormationEntry(
        definition_key="interaction.fulfill_conversational_deliverable",
        proposal_rule_id="goal-propose.explicit-conversational-deliverable.v1",
        formation_source="semantic_act", owner=GoalOwner.USER, scope="turn",
        act_hypothesis_key="interpretation.act.request_action",
        speaker_goal_hypothesis_key="interpretation.goal.cause_action",
        frame_act_families=(ActFamily.REQUEST_CONTENT,), strict_frame_families=True,
        scoping_polarity="negative",
        target_bindings=(_frame("request_ref", "request_ref"),),
        parameter_bindings=(
            GoalParameterBinding(name="operation", argument="operation", placeholder_base="frame"),
            GoalParameterBinding(name="artifact_type", argument="artifact_type", placeholder_base="frame"),
        ),
    ),
    # Formation v0.5: an answer linked to an exact open inquiry.
    GoalFormationEntry(
        definition_key="interaction.incorporate_inquiry_answer",
        proposal_rule_id="goal-propose.inquiry-answer-incorporation.v1",
        formation_source="semantic_act", owner=GoalOwner.USER, scope="turn",
        act_hypothesis_key="interpretation.act.inform",
        speaker_goal_hypothesis_key="interpretation.goal.transfer_information",
        frame_act_families=(ActFamily.ANSWER_INQUIRY,), strict_frame_families=True,
        target_bindings=(_frame("answer_ref", "answer_ref"),),
        parameter_bindings=(
            GoalParameterBinding(name="inquiry_ref", argument="inquiry_ref", placeholder_base="frame"),
        ),
    ),
    GoalFormationEntry(
        definition_key="interaction.acknowledge_relevant_information",
        proposal_rule_id="goal-propose.relevant-information-acknowledgement.v1",
        formation_source="semantic_act", owner=GoalOwner.USER, scope="conversation",
        act_hypothesis_key="interpretation.act.inform",
        speaker_goal_hypothesis_key="interpretation.goal.seek_acknowledgement",
        frame_act_families=(ActFamily.INFORM,),
        target_bindings=(_frame("proposition_ref", "proposition_ref"),),
    ),
    GoalFormationEntry(
        definition_key="interaction.repair_shared_understanding",
        proposal_rule_id="goal-propose.explicit-repair-request.v1",
        formation_source="semantic_act", owner=GoalOwner.SHARED, scope="task_or_turn",
        act_hypothesis_key="interpretation.act.correct",
        speaker_goal_hypothesis_key="interpretation.goal.repair_shared_understanding",
        frame_act_families=(ActFamily.CORRECT,),
        target_bindings=(_frame("proposition_ref", "proposition_ref"),),
    ),
    GoalFormationEntry(
        definition_key="interaction.respond_to_social_contact",
        proposal_rule_id="goal-propose.explicit-social-contact.v1",
        formation_source="semantic_act", owner=GoalOwner.SHARED, scope="contact_scope_argument",
        act_hypothesis_key="interpretation.act.greet",
        speaker_goal_hypothesis_key="interpretation.goal.maintain_social_connection",
        frame_act_families=(ActFamily.GREET, ActFamily.SOCIAL_QUESTION),
        target_bindings=(GoalTargetBinding(role="dialogue_scope_ref", source="conversation_ref"),),
        parameter_bindings=(GoalParameterBinding(name="contact_kind", argument="contact_kind", placeholder_base="interpretation"),),
    ),
    GoalFormationEntry(
        definition_key="task.prevent_or_stop_requested_action",
        proposal_rule_id="goal-propose.explicit-prevent-or-stop-request.v1",
        formation_source="semantic_act", owner=GoalOwner.USER, scope="task",
        act_hypothesis_key="interpretation.act.request_action",
        speaker_goal_hypothesis_key="interpretation.goal.prevent_or_stop_action",
        frame_act_families=(ActFamily.REQUEST_ACTION,),
        target_bindings=(GoalTargetBinding(role="task_ref", source="task_ref"),),
        parameter_bindings=(GoalParameterBinding(name="prevented_effect", argument="prevented_effect", placeholder_base="interpretation"),),
        requires_task_ref=True,
    ),
    GoalFormationEntry(
        definition_key="epistemic.resolve_required_unknown",
        proposal_rule_id="goal-propose.blocking-required-unknown.v1",
        formation_source="blocking_argument", owner=GoalOwner.COLUMBINA, scope="task_or_turn",
        priority_band=GoalPriorityBand.HIGH,
    ),
    GoalFormationEntry(
        definition_key="epistemic.resolve_material_contradiction",
        proposal_rule_id="goal-propose.material-contradiction.v1",
        formation_source="contradiction", owner=GoalOwner.COLUMBINA, scope="task_or_turn",
    ),
    GoalFormationEntry(
        definition_key="interaction.explain_unavailable_or_failed_operation",
        proposal_rule_id="goal-propose.known-operation-failure.v1",
        formation_source="runtime_tool_event", owner=GoalOwner.SHARED, scope="turn",
    ),
)


def frame_target_refs(
    entry: GoalFormationEntry, frame, *, conversation_ref: str, task_ref: str | None,
) -> tuple[tuple[str, str | None], ...]:
    """The ``(role, ref)`` requirement targets ``entry`` binds from ``frame``.

    The frame itself is the referent when the predicate carries no such
    argument; an unresolved value is gated at adoption.  Shared by formation
    and by requirement resolution, which must recover exactly the frame a
    goal was formed from.
    """

    roles = []
    for binding in entry.target_bindings:
        if binding.source == "conversation_ref":
            roles.append((binding.role, conversation_ref))
        elif binding.source == "task_ref":
            roles.append((binding.role, task_ref))
        else:
            value = frame.arguments.get(binding.argument)
            roles.append((binding.role, str(value) if value is not None else frame.frame_id))
    return tuple(roles)


def validate_formation_manifest(entries: tuple[GoalFormationEntry, ...] = _ENTRIES) -> tuple[GoalFormationEntry, ...]:
    definitions = {item.definition_key: item for item in all_definitions()}
    keys = tuple(item.definition_key for item in entries)
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate goal formation entry")
    if set(keys) != set(definitions):
        raise ValueError("formation manifest must cover exactly the installed goal definitions")
    for entry in entries:
        definition = definitions[entry.definition_key]
        if entry.owner not in definition.allowed_owners:
            raise ValueError("formation entry owner is not allowed by its definition")
        template = definition.requirement_templates[0]
        bound_roles = tuple(item.role for item in entry.target_bindings)
        if entry.formation_source == "semantic_act" and sorted(bound_roles) != sorted(template.required_target_roles):
            raise ValueError("a semantic-act entry binds exactly its definition's target roles")
        if not {item.name for item in entry.parameter_bindings} <= set(template.allowed_parameter_keys):
            raise ValueError("formation entry binds an unregistered parameter")
    return entries


FORMATION_ENTRIES: tuple[GoalFormationEntry, ...] = validate_formation_manifest()
FORMATION_BY_DEFINITION: Mapping[str, GoalFormationEntry] = {item.definition_key: item for item in FORMATION_ENTRIES}
SEMANTIC_ENTRIES: tuple[GoalFormationEntry, ...] = tuple(
    item for item in FORMATION_ENTRIES if item.formation_source == "semantic_act"
)
FORMATION_MANIFEST_VERSION = GOAL_FORMATION_POLICY_VERSION


def frame_eligible(entry: GoalFormationEntry, frame) -> bool:
    """Whether ``frame`` may bind ``entry`` (shared by formation and requirement resolution)."""

    from ..interpretation_v0.ontology import act_family

    if frame.predicate in entry.excluded_predicates:
        return False
    family = act_family(frame.predicate)
    if family is None:
        return not entry.strict_frame_families
    return family in entry.frame_act_families


__all__ = [
    "EXCLUDED_PREDICATES", "frame_eligible", "FORMATION_BY_DEFINITION", "FORMATION_ENTRIES", "FORMATION_MANIFEST_VERSION",
    "GoalFormationEntry", "GoalParameterBinding", "GoalTargetBinding", "SEMANTIC_ENTRIES",
    "frame_target_refs", "validate_formation_manifest",
]
