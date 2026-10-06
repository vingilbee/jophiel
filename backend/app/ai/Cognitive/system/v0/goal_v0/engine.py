"""Deterministic Goal V0 formation, lifecycle, and focus engine."""
from __future__ import annotations
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from ..contracts.goals import *
from ..contracts.goals import RESPONSE_OBLIGATION_DEFINITION_KEY
from ..contracts.interpretation import HypothesisDisposition, InterpretationHypothesis
from .models import GoalUpdateRequest, GoalUpdateResult
from .ontology import definition, definition_by_key, validate_requirement
from .lifecycle import next_status
from .errors import GoalExecutionError, GoalFailurePhase
from .adoption_policy import AdoptionContext, UnresolvedArgument, adoption_policy, evaluate_adoption
from .evidence_producer import AVOIDANCE_MONITORING_POLICY, SATISFYING_EVIDENCE_KINDS
from .formation_policy import FORMATION_BY_DEFINITION, SEMANTIC_ENTRIES, frame_eligible, frame_target_refs

_RETAINED = (HypothesisDisposition.LEADING, HypothesisDisposition.RETAINED_ALTERNATIVE)
# Satisfaction evidence classes are the reviewed authority table shared with
# the evidence producer (satisfaction policy v0.2).
_SATISFACTION_EVIDENCE = SATISFYING_EVIDENCE_KINDS
SOCIAL_CONTACT_DEFINITION_KEY = "interaction.respond_to_social_contact"
REQUIRED_UNKNOWN_DEFINITION_KEY = "epistemic.resolve_required_unknown"
PRECEDENCE_RULE = "goal-priority.current-explicit-request-over-compatible-history.v1"


@dataclass(frozen=True)
class _CandidateBasis:
    """Engine-internal: what one candidate was formed from (adoption input only)."""

    act_hypothesis: InterpretationHypothesis | None = None
    linked_hypotheses: tuple[InterpretationHypothesis, ...] = ()
    frame_refs: tuple[str, ...] = ()
    frame_predicates: tuple[str, ...] = ()
    unresolved: tuple[UnresolvedArgument, ...] = ()
    parent_goal_ref: str | None = None


def _eligible(entry, frame) -> bool:
    """A frame binds an entry unless its registered act family says otherwise."""

    return frame_eligible(entry, frame)


def _exact_unknown(interpretation, scenario, frame, argument_name) -> str | None:
    """The one typed unknown for ``argument_name`` of ``frame``, else ``None``.

    Only unknowns the scenario carries qualify; several are narrowed to the
    ones sharing evidence with the frame.  Ambiguity is never guessed away.
    """

    listed = set(scenario.unknown_refs)
    matches = [item for item in interpretation.unknowns if item.key == argument_name and item.unknown_id in listed]
    if len(matches) > 1:
        matches = [item for item in matches if set(item.evidence_refs) & set(frame.evidence_refs)]
    return matches[0].unknown_id if len(matches) == 1 else None


def _canonical_evidence(evidence):
    """Evidence order never changes an outcome (lifecycle policy v0.3)."""

    return tuple(sorted(evidence, key=lambda item: item.evidence_id))

def _id(kind: str, *parts: str) -> str:
    return f"{kind}-{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:20]}"

class GoalEngine:
    def __init__(self, *, identity_factory: Callable[[str, tuple[str, ...]], str] | None = None):
        self.identity_factory = identity_factory or (lambda kind, parts: _id(kind, *parts))

    def update(self, request: GoalUpdateRequest) -> GoalUpdateResult:
        phase = GoalFailurePhase.VALIDATION
        try:
            self._validate(request)
            # Reconcile evidence in one canonical order so the outcome cannot
            # depend on the order a producer happened to emit it in.
            request = request.model_copy(update={"new_goal_evidence": _canonical_evidence(request.new_goal_evidence)})
            previous = request.previous_goal_snapshot
            records = list(previous.records if previous else ())
            prior_status = {x.goal_id: x.status for x in records}
            assessments = list(previous.requirement_assessments if previous else ())
            current_targets = {(item.target_goal_ref, item.target_requirement_ref) for item in request.new_goal_evidence}
            records_by_id = {item.goal_id: item for item in records}
            next_revision = previous.revision + 1 if previous else 1
            refreshed_assessments = []
            for assessment in assessments:
                record = records_by_id.get(assessment.goal_ref)
                refresh_open_task = (
                    request.trigger == GoalUpdateTrigger.REALIZATION_OR_OUTCOME
                    and assessment.status == GoalRequirementStatus.OPEN
                    and (assessment.goal_ref, assessment.requirement_ref) not in current_targets
                    and record is not None
                    and record.domain == GoalDomain.TASK
                )
                if refresh_open_task:
                    refreshed_assessments.append(assessment.model_copy(update={
                        "assessment_id": self.identity_factory("assessment", (
                            assessment.goal_ref, assessment.requirement_ref,
                            f"revision:{next_revision}",
                        )),
                        "evidence_refs": (),
                        "conflict_refs": (),
                        "previous_assessment_ref": (
                            assessment.assessment_id
                            if assessment.previous_assessment_ref is not None else None
                        ),
                    }))
                else:
                    refreshed_assessments.append(assessment)
            assessments = refreshed_assessments
            conflicts = list(previous.evidence_conflicts if previous else ())
            transitions: list[GoalTransition] = []
            base_sequence = previous.transitions[-1].sequence if previous and previous.transitions else 0
            proposals: list[GoalProposal] = []
            priority_changes: list[GoalPriorityChange] = []
            phase = GoalFailurePhase.LIFECYCLE_UPDATE
            # Reconcile the existing records before lifecycle rules run.  In
            # particular, verified-impossibility and adequate-monitoring
            # closure need an existing typed assessment to update; otherwise
            # the lifecycle transition would be emitted without its required
            # terminal assessment.
            self._ensure_assessments(records, assessments, transitions)
            phase = GoalFailurePhase.EVIDENCE_RECONCILIATION
            if records and all(item.target_goal_ref in {record.goal_id for record in records} for item in request.new_goal_evidence):
                self._reconcile(request, records, assessments, conflicts)
            satisfied_transitioned: set[str] = set()
            for record in tuple(records):
                if prior_status.get(record.goal_id) in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED} and record.status == GoalStatus.SATISFIED:
                    transition = self._transition(record, GoalTransitionKind.SATISFY, request, base_sequence + len(transitions)+1, from_status=prior_status[record.goal_id], rule_id="goal-lifecycle.satisfy-all-requirements.v1")
                    transitions.append(transition)
                    records[records.index(record)] = record.model_copy(update={"last_transition_ref": transition.transition_id})
                    satisfied_transitioned.add(record.goal_id)
            self._lifecycle(request, records, assessments, transitions, base_sequence)
            # Turn rollover runs after typed evidence so a prior turn goal that
            # evidence legitimately satisfied keeps that terminal state; every
            # remaining one expires before the new turn's goals form or rank.
            self._rollover(request, records, transitions, base_sequence)
            phase = GoalFailurePhase.CANDIDATE_FORMATION
            bases: dict[str, _CandidateBasis] = {}
            candidates = self._candidates(request, records, bases)
            phase = GoalFailurePhase.ADOPTION
            self._adopt(request, candidates, records, proposals, transitions, base_sequence, bases=bases)
            if any(item.provenance.replacement_goal_ref for item in request.new_goal_evidence):
                self._lifecycle(request, records, assessments, transitions, base_sequence)
            # Evidence for a newly proposed goal can only be reconciled after
            # adoption has materialized its canonical record.
            if request.new_goal_evidence and any(item.target_goal_ref not in prior_status for item in request.new_goal_evidence):
                self._reconcile(request, records, assessments, conflicts)
            for record in records:
                if record.goal_id not in satisfied_transitioned and prior_status.get(record.goal_id) in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED} and record.status == GoalStatus.SATISFIED:
                    transition = self._transition(record, GoalTransitionKind.SATISFY, request, base_sequence + len(transitions)+1, from_status=prior_status[record.goal_id], rule_id="goal-lifecycle.satisfy-all-requirements.v1")
                    transitions.append(transition)
                    records[records.index(record)] = record.model_copy(update={"last_transition_ref": transition.transition_id})
            self._ensure_assessments(records, assessments, transitions)
            phase = GoalFailurePhase.PRIORITY_ASSIGNMENT
            self._priorities(request, records, priority_changes)
            phase = GoalFailurePhase.CONFLICT_RESOLUTION
            goal_conflicts, route = self._conflicts(request, records)
            if (request.runtime.last_tool_event or {}).get("status") == "disputed" or any(item.status == GoalRequirementStatus.DISPUTED for item in assessments):
                route = GoalFocusRoute.SLOW
            phase = GoalFailurePhase.FOCUS_SELECTION
            if request.trigger == GoalUpdateTrigger.RESPONSE_TURN and any(
                record.scope == GoalScope.TURN and record.scope_ref != request.cognitive_turn_id
                and record.status in {GoalStatus.CANDIDATE, GoalStatus.ACTIVE, GoalStatus.SUSPENDED}
                for record in records
            ):
                raise ValueError("a response turn cannot rank another turn's non-terminal goals")
            focus = self._focus(request, records, goal_conflicts, route)
            snapshot_id = self.identity_factory("goal-snapshot", (request.cognitive_turn_id, str((previous.revision + 1) if previous else 1), request.interpretation.interpretation_trace_id))
            # Rebind transition snapshot refs now that the immutable snapshot id exists.
            transitions = [t.model_copy(update={"resulting_snapshot_ref": snapshot_id, "previous_snapshot_ref": previous.goal_snapshot_id if previous else None}) for t in transitions]
            snapshot = GoalSnapshot(
                goal_snapshot_id=snapshot_id, revision=(previous.revision + 1 if previous else 1),
                supersedes_goal_snapshot_id=previous.goal_snapshot_id if previous else None,
                cognitive_turn_id=request.cognitive_turn_id, conversation_ref=request.conversation_ref,
                runtime_snapshot_ref=request.runtime.runtime_snapshot_ref,
                interpretation_trace_ref=request.interpretation.interpretation_trace_id,
                previous_dynamic_state_ref=request.previous_dynamic_state.state_snapshot_id,
                input_outcome_refs=tuple(
                    x.outcome_id for x in request.new_goal_evidence
                    if x.outcome_id
                ),
                records=tuple(records), requirement_assessments=tuple(assessments), evidence_conflicts=tuple(conflicts),
                transitions=tuple((previous.transitions if previous else ()) + tuple(transitions)), priority_changes=tuple(priority_changes), conflicts=tuple(goal_conflicts),
                focus_decision=focus, active_goal_refs=tuple(x.goal_id for x in records if x.status == GoalStatus.ACTIVE),
                focal_goal_refs=(focus.primary_goal_ref, *focus.supporting_goal_refs),
            )
            phase = GoalFailurePhase.SNAPSHOT_VALIDATION
            validated = GoalSnapshot.model_validate(snapshot.model_dump(mode="python"))
            views = tuple(project_active_goal(next(x for x in validated.records if x.goal_id == ref)) for ref in validated.focal_goal_refs)
            return GoalUpdateResult(proposals=tuple(proposals), transitions=tuple(transitions), requirement_assessments=tuple(assessments), evidence_conflicts=tuple(conflicts), priority_changes=tuple(priority_changes), conflicts=tuple(goal_conflicts), focus_decision=validated.focus_decision, snapshot=validated, active_goal_views=views)
        except GoalExecutionError: raise
        except Exception as exc:
            category = {
                GoalFailurePhase.VALIDATION: "invalid_goal_input",
                GoalFailurePhase.EVIDENCE_RECONCILIATION: "invalid_goal_evidence",
                GoalFailurePhase.LIFECYCLE_UPDATE: "invalid_goal_transition",
                GoalFailurePhase.CANDIDATE_FORMATION: "unknown_goal_definition",
                GoalFailurePhase.ADOPTION: "invalid_goal_adoption",
                GoalFailurePhase.PRIORITY_ASSIGNMENT: "invalid_goal_priority",
                GoalFailurePhase.CONFLICT_RESOLUTION: "invalid_goal_conflict",
                GoalFailurePhase.FOCUS_SELECTION: "invalid_goal_focus",
                GoalFailurePhase.SNAPSHOT_VALIDATION: "invalid_goal_snapshot",
            }[phase]
            raise GoalExecutionError(
                request.cognitive_turn_id,
                phase,
                category,
                "Goal processing could not complete safely.",
            ) from exc

    @staticmethod
    def _validate(request):
        if request.trigger == GoalUpdateTrigger.RESPONSE_TURN and request.runtime.conversation_status != "open": raise ValueError("closed conversation cannot create response goal")
        if request.previous_goal_snapshot and request.previous_goal_snapshot.conversation_ref != request.conversation_ref: raise ValueError("predecessor conversation mismatch")

    def _reconcile(self, request, records, assessments, conflicts):
        by_id={x.goal_id:x for x in records}; by_req={(x.goal_ref,x.requirement_ref):x for x in assessments}
        known_evidence = {item.evidence_id for item in request.new_goal_evidence}

        def requirement_of(ev):
            goal = by_id.get(ev.target_goal_ref)
            return next((x for x in goal.requirements if x.requirement_id == ev.target_requirement_ref), None) if goal else None

        # Observations first, then satisfying evidence, each by evidence id:
        # an observation can never reopen a requirement that evidence in the
        # same batch satisfied, whatever order the batch arrived in.
        ordered = sorted(request.new_goal_evidence, key=lambda ev: (
            requirement_of(ev) is not None and self._satisfies_policy(ev, requirement_of(ev)), ev.evidence_id,
        ))
        for ev in ordered:
            goal=by_id.get(ev.target_goal_ref)
            if goal is None: raise ValueError("goal evidence targets an unknown goal")
            req=next((x for x in goal.requirements if x.requirement_id == ev.target_requirement_ref),None)
            if req is None: raise ValueError("goal evidence targets an unknown requirement")
            key=(goal.goal_id,req.requirement_id); old=by_req.get(key)
            if any(
                other.provenance.contradicts_evidence_ref == ev.evidence_id
                for other in request.new_goal_evidence
            ):
                # Reconcile the explicitly contradictory pair atomically.  Do
                # not publish a transient satisfied assessment for the first
                # item in the same evidence batch.
                continue
            if any((
                ev.provenance.withdraws_goal_ref,
                ev.provenance.replacement_goal_ref,
                ev.provenance.suspend_goal_ref,
                ev.provenance.resume_goal_ref,
                ev.provenance.verified_impossible,
            )):
                continue
            matches_policy = self._satisfies_policy(ev, req)
            if not matches_policy:
                track_open = ev.evidence_kind in {"finalized_tool_result", "realization_validation", "reviewed_outcome"} or bool(ev.provenance.alternate_route_available)
                if req.satisfaction_policy_key == "goal-satisfaction.authorized-task-result.v1" and ev.evidence_kind == "explicit_user_confirmation":
                    track_open = True
                if ev.provenance.monitoring_adequate is False:
                    track_open = True
                if track_open:
                    prior_was_focal = request.previous_goal_snapshot is None or goal.goal_id in request.previous_goal_snapshot.focal_goal_refs
                    assessment = GoalRequirementAssessment(assessment_id=self.identity_factory("assessment", (goal.goal_id, req.requirement_id, ev.evidence_id)), goal_ref=goal.goal_id, requirement_ref=req.requirement_id, status=GoalRequirementStatus.OPEN, policy_key=req.satisfaction_policy_key, evidence_refs=(ev.evidence_id,), previous_assessment_ref=old.assessment_id if old and (old.previous_assessment_ref is not None or prior_was_focal) else None, rationale="typed evidence does not establish the registered terminal condition")
                    if old: assessments[:] = [x for x in assessments if x is not old]
                    assessments.append(assessment); by_req[key] = assessment
                continue
            status = GoalRequirementStatus.SATISFIED if ev.finality == "final" and matches_policy else GoalRequirementStatus.OPEN
            if ev.provenance.outcome_status == "failed" and ev.provenance.contradicts_evidence_ref:
                status = GoalRequirementStatus.DISPUTED
            # Evidence classes are not contradictory merely because they differ.
            # A conflict requires explicit typed provenance naming the evidence
            # it contradicts; identifier text is never interpreted.
            contradicted_ref = ev.provenance.contradicts_evidence_ref
            if old is not None and isinstance(contradicted_ref, str) and (
                contradicted_ref in old.evidence_refs or contradicted_ref in known_evidence
            ):
                conflict = GoalEvidenceConflict(conflict_id=self.identity_factory("evidence-conflict", (goal.goal_id, ev.evidence_id)), status=GoalEvidenceConflictStatus.ACTIVE, left_evidence_ref=contradicted_ref, right_evidence_ref=ev.evidence_id, rationale="explicit typed provenance declares contradiction")
                conflicts.append(conflict)
                status = GoalRequirementStatus.DISPUTED
            if status == GoalRequirementStatus.DISPUTED and isinstance(contradicted_ref, str):
                evidence_refs = tuple(dict.fromkeys((*old.evidence_refs, contradicted_ref, ev.evidence_id))) if old else (contradicted_ref, ev.evidence_id)
            else:
                evidence_refs = (ev.evidence_id,)
            assessment=GoalRequirementAssessment(assessment_id=self.identity_factory("assessment", (goal.goal_id,req.requirement_id,ev.evidence_id)),goal_ref=goal.goal_id,requirement_ref=req.requirement_id,status=status,policy_key=req.satisfaction_policy_key,evidence_refs=evidence_refs,conflict_refs=tuple(x.conflict_id for x in conflicts if x.left_evidence_ref in (old.evidence_refs if old else ()) or x.right_evidence_ref == ev.evidence_id),previous_assessment_ref=old.assessment_id if old else None,rationale="typed evidence")
            if old: assessments[:] = [x for x in assessments if x is not old]
            assessments.append(assessment); by_req[key]=assessment
            if status == GoalRequirementStatus.SATISFIED and goal.status in {GoalStatus.ACTIVE,GoalStatus.SUSPENDED}:
                if all(x.status == GoalRequirementStatus.SATISFIED for x in assessments if x.goal_ref == goal.goal_id):
                    records[:] = [x.model_copy(update={"status": GoalStatus.SATISFIED}) if x.goal_id==goal.goal_id else x for x in records]

    @staticmethod
    def _satisfies_policy(ev, req) -> bool:
        """Whether ``ev`` is of a class that can satisfy ``req`` (finality aside)."""

        provenance = ev.provenance
        if provenance.monitoring_adequate is not None:
            # An avoid-mode goal is met when trusted runtime monitoring shows
            # the action stayed prevented through its closed scope.
            return (
                provenance.monitoring_adequate is True
                and ev.evidence_kind == "trusted_runtime_fact"
                and req.satisfaction_policy_key == AVOIDANCE_MONITORING_POLICY
            )
        if ev.evidence_kind not in _SATISFACTION_EVIDENCE.get(req.satisfaction_policy_key, frozenset()):
            return False
        # A failed or unavailable outcome is never success.  A failure that
        # names the evidence it contradicts is reconciled as a dispute instead.
        if provenance.outcome_status in {"failed", "unavailable"} and not provenance.contradicts_evidence_ref:
            return False
        return not (provenance.suspend_goal_ref or provenance.non_satisfying_observation)

    def _lifecycle(self, request, records, assessments, transitions, base_sequence=0):
        for record in records:
            typed_facts = [item.provenance for item in request.new_goal_evidence if item.target_goal_ref == record.goal_id]
            withdrawal = any(fact.withdraws_goal_ref == record.goal_id for fact in typed_facts)
            impossible = any(fact.verified_impossible is True for fact in typed_facts)
            defer = any(fact.suspend_goal_ref == record.goal_id for fact in typed_facts)
            suspend_rule = next((fact.suspend_rule_id for fact in typed_facts if fact.suspend_goal_ref == record.goal_id and fact.suspend_rule_id), "goal-lifecycle.suspend-explicit-deferral.v1")
            resume = any(fact.resume_goal_ref == record.goal_id for fact in typed_facts)
            replacement = next((fact.replacement_goal_ref for fact in typed_facts if fact.replacement_goal_ref), None)
            if replacement and not any(item.goal_id == replacement for item in records):
                continue
            if record.status in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED} and withdrawal:
                transition = self._transition(record, GoalTransitionKind.ABANDON, request, base_sequence + len(transitions)+1, rule_id="goal-lifecycle.abandon-explicit-withdrawal.v1")
                transitions.append(transition)
                records[records.index(record)] = record.model_copy(update={"status": GoalStatus.ABANDONED, "last_transition_ref": transition.transition_id})
                continue
            if record.status in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED} and impossible:
                transition = self._transition(record, GoalTransitionKind.FAIL, request, base_sequence + len(transitions)+1, rule_id="goal-lifecycle.fail-verified-impossibility.v1")
                transitions.append(transition)
                records[records.index(record)] = record.model_copy(update={"status": GoalStatus.FAILED, "last_transition_ref": transition.transition_id})
                impossible_facts = [dict(item.provenance.model_dump(exclude_none=True), evidence_id=item.evidence_id) for item in request.new_goal_evidence if item.target_goal_ref == record.goal_id and item.provenance.verified_impossible is True]
                self._set_assessment_status(record, assessments, GoalRequirementStatus.IMPOSSIBLE, "verified impossibility", impossible_facts)
                continue
            if record.status == GoalStatus.ACTIVE and defer:
                transition = self._transition(record, GoalTransitionKind.SUSPEND, request, base_sequence + len(transitions)+1, rule_id=suspend_rule)
                transitions.append(transition)
                records[records.index(record)] = record.model_copy(update={"status": GoalStatus.SUSPENDED, "last_transition_ref": transition.transition_id})
                continue
            if record.status == GoalStatus.SUSPENDED and resume:
                transition = self._transition(record, GoalTransitionKind.RESUME, request, base_sequence + len(transitions)+1, rule_id="goal-lifecycle.resume-blocker-resolved.v1")
                transitions.append(transition)
                records[records.index(record)] = record.model_copy(update={"status": GoalStatus.ACTIVE, "last_transition_ref": transition.transition_id})
                continue
            if record.status in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED} and replacement:
                transition = self._transition(record, GoalTransitionKind.SUPERSEDE, request, base_sequence + len(transitions)+1, rule_id="goal-lifecycle.supersede-material-replacement.v1", replacement_goal_ref=replacement)
                transitions.append(transition)
                records[records.index(record)] = record.model_copy(update={"status": GoalStatus.SUPERSEDED, "last_transition_ref": transition.transition_id})
                continue
            scope_closed = request.runtime.conversation_status == "closed" and record.scope == GoalScope.CONVERSATION
            closure_targets = {item.target_goal_ref for item in request.new_goal_evidence}
            explicit_closure = request.trigger == GoalUpdateTrigger.TRUSTED_SCOPE_CLOSURE and record.scope in {GoalScope.TURN, GoalScope.TASK, GoalScope.CONVERSATION} and record.goal_id in closure_targets
            if record.status in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED} and (scope_closed or explicit_closure):
                expiry_rule = {GoalScope.TURN: "goal-lifecycle.expire-turn-scope.v1", GoalScope.TASK: "goal-lifecycle.expire-task-scope.v1", GoalScope.CONVERSATION: "goal-lifecycle.expire-conversation-scope.v1"}[record.scope]
                transition = self._transition(record, GoalTransitionKind.EXPIRE, request, base_sequence + len(transitions)+1, rule_id=expiry_rule)
                transitions.append(transition)
                records[records.index(record)] = record.model_copy(update={"status": GoalStatus.EXPIRED, "last_transition_ref": transition.transition_id})

    def _rollover(self, request, records, transitions, base_sequence=0):
        """Expire every non-terminal goal scoped to a turn other than this one.

        Authorized only by the typed ``turn_rollover`` proof (request
        validation already requires it whenever such goals exist).  Uses the
        ordinary EXPIRE transition; the predecessor snapshot is never edited and
        no satisfaction evidence is manufactured.
        """

        rollover = request.turn_rollover
        if rollover is None:
            return
        for index, record in enumerate(tuple(records)):
            if not (
                record.scope == GoalScope.TURN
                and record.scope_ref != request.cognitive_turn_id
                and record.status in {GoalStatus.CANDIDATE, GoalStatus.ACTIVE, GoalStatus.SUSPENDED}
            ):
                continue
            transition = self._transition(
                record, GoalTransitionKind.EXPIRE, request, base_sequence + len(transitions) + 1,
                rule_id="goal-lifecycle.expire-turn-scope-on-successor-turn.v1",
            ).model_copy(update={"evidence_refs": (rollover.rollover_id,), "outcome_refs": ()})
            transitions.append(transition)
            records[index] = record.model_copy(update={
                "status": GoalStatus.EXPIRED, "last_transition_ref": transition.transition_id,
            })

    def _set_assessment_status(self, record, assessments, status, rationale, facts=()):
        for index, assessment in enumerate(assessments):
            if assessment.goal_ref == record.goal_id:
                refs = tuple(fact.get("evidence_id") for fact in facts if isinstance(fact, dict) and fact.get("evidence_id"))
                identity_parts = (record.goal_id, assessment.requirement_ref, *(refs[:1] or (status.value,)))
                assessments[index] = assessment.model_copy(update={
                    "assessment_id": self.identity_factory("assessment", identity_parts),
                    "status": status,
                    "rationale": rationale,
                    "evidence_refs": refs or assessment.evidence_refs,
                    "previous_assessment_ref": assessment.assessment_id,
                })

    def _candidates(self, request, records, bases=None):
        """Form candidates from the closed formation manifest.

        ``bases`` (goal id -> ``_CandidateBasis``) records, for adoption only,
        the exact act, frame, hypotheses, and unresolved arguments each
        candidate was formed from.
        """

        out = []
        bases = {} if bases is None else bases
        interpretation = request.interpretation
        turn = request.cognitive_turn_id
        task_ref = request.runtime.task_ref

        def add(entry, scope, scope_ref, roles, params=(), source=(), basis=None, priority=None):
            key = entry.definition_key
            d = definition_by_key(key)
            reqt = d.requirement_templates[0]
            req = GoalRequirement(
                requirement_id=self.identity_factory("requirement", (turn, key, *[x[1] for x in roles], *[str(value) for _, value in params])),
                requirement_key=reqt.requirement_key,
                target_refs=tuple(GoalTargetRef(role=role, ref=ref) for role, ref in roles),
                parameters=dict(params), satisfaction_policy_key=reqt.satisfaction_policy_key,
            )
            validate_requirement(d, req)
            gid = self.identity_factory("goal", (turn, key, scope_ref, entry.owner.value, req.requirement_id, *[str(value) for _, value in params]))
            if gid in bases:
                return None
            bases[gid] = basis or _CandidateBasis()
            record = GoalRecord(
                goal_id=gid, definition_ref=GoalDefinitionRef(definition_key=key, definition_version=d.definition_version),
                owner=entry.owner, domain=d.domain, mode=d.mode, scope=GoalScope(scope), scope_ref=scope_ref,
                priority_band=priority or entry.priority_band or d.default_priority_band, status=GoalStatus.CANDIDATE,
                requirements=(req,), adoption_basis=GoalAdoptionBasis(rule_id=adoption_policy(key).adoption_rule_id),
                created_turn_ref=turn, last_transition_ref=self.identity_factory("transition", (gid, "propose")),
                source_refs=tuple(source),
            )
            out.append(record)
            return record

        # Lifecycle/evidence revisions do not create a fresh response
        # obligation.  The response candidate belongs only to an actual
        # response turn; carrying it into trusted-runtime or correction
        # updates creates spurious proposals and transitions.
        if request.trigger == GoalUpdateTrigger.RESPONSE_TURN and request.runtime.response_obligation == "required":
            add(FORMATION_BY_DEFINITION[RESPONSE_OBLIGATION_DEFINITION_KEY], "turn", turn, (("turn_ref", turn),),
                source=(request.runtime.response_obligation_ref,))
        correction_replacement = any(item.provenance.replacement_goal_ref for item in request.new_goal_evidence)
        if request.trigger != GoalUpdateTrigger.RESPONSE_TURN and not (request.trigger == GoalUpdateTrigger.EXPLICIT_CORRECTION and correction_replacement):
            return out

        trace_source = (interpretation.interpretation_trace_id,)
        semantic_suffix = interpretation.interpretation_trace_id.replace("-interpretation", "")
        # The leading scenario forms first, so when an alternative forms the
        # identical candidate, adoption judges the leading reading's basis.
        retained = tuple(sorted(
            (item for item in interpretation.scenarios if item.disposition in _RETAINED),
            key=lambda item: item.disposition != HypothesisDisposition.LEADING,
        ))
        parents: list[tuple[GoalRecord, _CandidateBasis]] = []
        for scenario in retained:
            for record, basis in self._semantic_candidates(request, scenario, add, trace_source, semantic_suffix):
                parents.append((record, basis))

        # An epistemic subgoal only for the exact blocking argument of its
        # own parent: never the first global unknown, never another goal.
        for parent, basis in parents:
            resolvable = adoption_policy(parent.definition_ref.definition_key).resolvable_arguments
            for item in basis.unresolved:
                if item.argument_name not in resolvable or item.unknown_ref is None:
                    continue
                entry = FORMATION_BY_DEFINITION["epistemic.resolve_required_unknown"]
                add(entry, "task", task_ref or turn,
                    (("parent_goal_ref", parent.goal_id), ("argument_ref", item.unknown_ref)),
                    (("argument_name", item.argument_name),), source=(parent.goal_id, item.unknown_ref),
                    basis=_CandidateBasis(parent_goal_ref=parent.goal_id))

        contradictions = {item.contradiction_id: item for item in interpretation.contradictions}
        cited = tuple(dict.fromkeys(ref for scenario in retained for ref in scenario.contradiction_refs))
        for ref in cited:
            if ref in contradictions and contradictions[ref].status != "active":
                continue
            add(FORMATION_BY_DEFINITION["epistemic.resolve_material_contradiction"], "task", task_ref or turn,
                (("contradiction_ref", ref),), source=trace_source)

        event = request.runtime.last_tool_event or {}
        if event.get("status") in {"failed", "unavailable"}:
            operation = str(event.get("tool") or event.get("operation_ref") or "operation")
            failure = str(event.get("event_reference") or "failure")
            add(FORMATION_BY_DEFINITION["interaction.explain_unavailable_or_failed_operation"], "turn", turn,
                (("operation_ref", operation), ("failure_ref", failure)), source=trace_source + (operation, failure))
        order = {"interaction.discharge_response_obligation": 0, "interaction.answer_information_request": 1, "task.complete_authorized_request": 2, "interaction.handle_action_request": 2, "interaction.fulfill_conversational_deliverable": 2, "interaction.incorporate_inquiry_answer": 2, "interaction.acknowledge_relevant_information": 3, "interaction.repair_shared_understanding": 4, "interaction.respond_to_social_contact": 5}
        return sorted(out, key=lambda item: (order.get(item.definition_ref.definition_key, 10), item.goal_id))

    def _semantic_candidates(self, request, scenario, add, trace_source, semantic_suffix):
        """One candidate per eligible act-local frame of each manifest entry."""

        interpretation = request.interpretation
        turn = request.cognitive_turn_id
        in_scenario = {ref for refs in scenario.hypothesis_refs.values() for ref in refs}
        hypotheses = tuple(item for item in interpretation.hypotheses if item.hypothesis_id in in_scenario)
        listed = set(scenario.semantic_frame_refs)
        frames = tuple(item for item in interpretation.semantic_frames if not listed or item.frame_id in listed)
        frame_ids = {item.frame_id for item in frames}
        formed = []
        for entry in SEMANTIC_ENTRIES:
            if entry.requires_task_ref and not request.runtime.task_ref:
                continue
            if entry.requires_absent_task_ref and request.runtime.task_ref:
                continue
            for act in (item for item in hypotheses if item.hypothesis_key == entry.act_hypothesis_key and item.disposition in _RETAINED):
                # An act that cites no frame (a single-act reviewed design
                # case) is grounded in its scenario's frames.
                act_frames = set(act.semantic_frame_refs) & frame_ids if act.semantic_frame_refs else frame_ids
                goals = tuple(
                    item for item in hypotheses
                    if item.hypothesis_key == entry.speaker_goal_hypothesis_key and item.disposition in _RETAINED
                    and (not item.semantic_frame_refs or act_frames & set(item.semantic_frame_refs))
                )
                if not goals:
                    continue
                shared = act_frames if any(not item.semantic_frame_refs for item in goals) else (
                    act_frames & {ref for item in goals for ref in item.semantic_frame_refs}
                )
                own = [item for item in frames if item.frame_id in shared and _eligible(entry, item)]
                if entry.scoping_polarity and any(item.polarity != entry.scoping_polarity for item in own):
                    own = [item for item in own if item.polarity != entry.scoping_polarity]
                for frame in own:
                    formed.extend(self._bind(request, scenario, entry, act, frame, hypotheses, add, trace_source, semantic_suffix, turn))
        return formed

    def _bind(self, request, scenario, entry, act, frame, hypotheses, add, trace_source, semantic_suffix, turn):
        args = frame.arguments
        unresolved_names = set(frame.unresolved_arguments)
        roles = list(frame_target_refs(entry, frame, conversation_ref=request.conversation_ref,
                                       task_ref=request.runtime.task_ref))
        params = []
        for binding in entry.parameter_bindings:
            if binding.unresolved_sentinel_argument in unresolved_names:
                value = "unresolved_effect"
            elif args.get(binding.argument):
                value = str(args[binding.argument])
            else:
                base = semantic_suffix if binding.placeholder_base == "interpretation" else frame.frame_id.removesuffix("-frame")
                value = f"{base}-semantic-{binding.argument}"
            params.append((binding.name, value))
        if entry.scope == "turn":
            scope, scope_ref = "turn", turn
        elif entry.scope == "conversation":
            scope, scope_ref = "conversation", request.conversation_ref
        elif entry.scope == "task":
            scope, scope_ref = "task", request.runtime.task_ref
        elif entry.scope == "task_or_turn":
            scope, scope_ref = "task", request.runtime.task_ref or turn
        else:
            contact = str(args.get("contact_scope") or "turn")
            scope, scope_ref = ("conversation", request.conversation_ref) if contact == "conversation" else ("turn", turn)
        source = trace_source
        if entry.source_argument and args.get(entry.source_argument):
            source = source + (str(args[entry.source_argument]),)
        priority = (
            entry.unresolved_priority_band
            if entry.unresolved_priority_argument in unresolved_names else None
        )
        basis = _CandidateBasis(
            act_hypothesis=act,
            linked_hypotheses=tuple(
                item for item in hypotheses
                if not item.semantic_frame_refs or frame.frame_id in item.semantic_frame_refs
            ),
            frame_refs=(frame.frame_id,),
            frame_predicates=(frame.predicate,),
            unresolved=tuple(
                UnresolvedArgument(name, frame.frame_id, _exact_unknown(request.interpretation, scenario, frame, name))
                for name in frame.unresolved_arguments
            ),
        )
        record = add(entry, scope, scope_ref, tuple(roles), tuple(params), source=source, basis=basis, priority=priority)
        return ((record, basis),) if record is not None else ()

    def _adopt(self, request, candidates, records, proposals, transitions, base_sequence=0, bases=None):
        bases = bases or {}
        adopted_ids: set[str] = set()
        active_contradictions = frozenset(
            item.contradiction_id for item in request.interpretation.contradictions if item.status == "active"
        )
        for candidate in candidates:
            def semantic_requirement(requirement):
                return (requirement.requirement_key, tuple((target.role, target.ref) for target in requirement.target_refs), tuple(sorted(requirement.parameters.items())), requirement.satisfaction_policy_key)
            def same_semantics(item):
                return (
                    item.status not in {GoalStatus.SATISFIED, GoalStatus.FAILED, GoalStatus.ABANDONED, GoalStatus.SUPERSEDED, GoalStatus.REJECTED, GoalStatus.EXPIRED}
                    and item.definition_ref == candidate.definition_ref
                    and item.owner == candidate.owner
                    and item.domain == candidate.domain
                    and item.mode == candidate.mode
                    and item.scope == candidate.scope
                    and item.scope_ref == candidate.scope_ref
                    and tuple(semantic_requirement(req) for req in item.requirements) == tuple(semantic_requirement(req) for req in candidate.requirements)
                )
            duplicate = next((item for item in records if same_semantics(item)), None)
            if duplicate is not None:
                continue
            # Several interpretation scenarios can form the identical candidate.
            # Its identity already has a record (adopted or rejected) this
            # revision; proposing it again would duplicate a goal id.
            if any(item.goal_id == candidate.goal_id for item in records):
                continue
            key = candidate.definition_ref.definition_key
            proposal_sources = candidate.source_refs[:1] if key == "interaction.answer_information_request" else candidate.source_refs
            proposal_rule = FORMATION_BY_DEFINITION[key].proposal_rule_id
            proposal=GoalProposal(proposal_id=self.identity_factory("proposal", (candidate.goal_id,)),goal_id=candidate.goal_id,definition_ref=candidate.definition_ref,rule_id=proposal_rule,source_refs=proposal_sources); proposals.append(proposal)
            policy = adoption_policy(key)
            basis = bases.get(candidate.goal_id, _CandidateBasis())
            targets = {target.role: target.ref for requirement in candidate.requirements for target in requirement.target_refs}
            parent_ref = basis.parent_goal_ref
            decision = evaluate_adoption(policy, AdoptionContext(
                definition_key=key,
                is_system_owned=candidate.owner == GoalOwner.SYSTEM,
                has_source_refs=bool(candidate.source_refs),
                bound_targets=targets,
                act_hypothesis=basis.act_hypothesis,
                linked_hypotheses=basis.linked_hypotheses,
                frame_refs=basis.frame_refs,
                frame_predicates=basis.frame_predicates,
                unresolved=basis.unresolved,
                active_contradiction_refs=active_contradictions,
                authorization=request.runtime.authorization,
                safety_decision=request.runtime.safety_decision,
                parent_adopted=(
                    None if parent_ref is None
                    else parent_ref in adopted_ids or any(item.goal_id == parent_ref and item.status == GoalStatus.ACTIVE for item in records)
                ),
            ))
            adopted = decision.adopted
            # A candidate rejected for missing grounding records the rule it
            # would have needed (a required subgoal); rejection provenance
            # itself belongs to the reject transition and its detail.
            ungrounded = decision.detail is not None and decision.detail.failed_check == GoalRejectionCheck.UNRESOLVED_ARGUMENT
            adoption_rule = "goal-adopt.required-subgoal.v1" if ungrounded and candidate.owner != GoalOwner.SYSTEM else policy.adoption_rule_id
            final=candidate.model_copy(update={"status": GoalStatus.ACTIVE if adopted else GoalStatus.REJECTED, "adoption_basis": GoalAdoptionBasis(rule_id=adoption_rule), "last_transition_ref":self.identity_factory("transition", (candidate.goal_id,"adopt" if adopted else "reject"))})
            transitions.append(self._transition(candidate, GoalTransitionKind.PROPOSE, request,base_sequence + len(transitions)+1, from_status=None, new_record=True, rule_id=proposal_rule))
            adoption_transition = self._transition(
                final,
                GoalTransitionKind.ADOPT if adopted else GoalTransitionKind.REJECT,
                request,
                base_sequence + len(transitions)+1,
                from_status=GoalStatus.CANDIDATE,
                rule_id=adoption_rule if adopted else decision.rejection_rule_id,
            )
            if not adopted:
                adoption_transition = adoption_transition.model_copy(update={"rejection_detail": decision.detail})
            else:
                adopted_ids.add(candidate.goal_id)
            transitions.append(adoption_transition)
            records.append(final.model_copy(update={"last_transition_ref": adoption_transition.transition_id}))

    def _ensure_assessments(self, records, assessments, transitions=()):
        existing={(x.goal_ref,x.requirement_ref) for x in assessments}
        missing_grounding_rejections = {
            item.goal_id for item in transitions
            if item.transition_kind == GoalTransitionKind.REJECT
            and item.rule_id == "goal-reject.missing-required-grounding.v1"
        }
        for record in records:
            if record.status == GoalStatus.REJECTED and record.goal_id in missing_grounding_rejections:
                continue
            for req in record.requirements:
                if (record.goal_id,req.requirement_id) not in existing:
                    assessments.append(GoalRequirementAssessment(
                        assessment_id=self.identity_factory("assessment", (record.goal_id,req.requirement_id)),
                        goal_ref=record.goal_id, requirement_ref=req.requirement_id,
                        status=GoalRequirementStatus.OPEN, policy_key=req.satisfaction_policy_key,
                        rationale="awaiting typed target-linked evidence",
                    ))

    def _priorities(self, request, records, changes):
        if request.runtime.deadline_band not in {"high", "urgent"}:
            return
        rank = {GoalPriorityBand.LOW: GoalPriorityBand.MODERATE, GoalPriorityBand.MODERATE: GoalPriorityBand.HIGH}
        evidence_refs = {item.evidence_id for item in request.new_goal_evidence} | {item.source_ref for item in request.new_goal_evidence}
        targeted_goals = {item.target_goal_ref for item in request.new_goal_evidence}
        for index, record in enumerate(records):
            if record.status != GoalStatus.ACTIVE or record.priority_band not in rank:
                continue
            if targeted_goals and record.goal_id not in targeted_goals and not set(record.source_refs) & evidence_refs:
                continue
            new = rank[record.priority_band]
            records[index] = record.model_copy(update={"priority_band": new})
            changes.append(GoalPriorityChange(priority_change_id=self.identity_factory("priority", (record.goal_id,new.value)), goal_ref=record.goal_id, old_priority_band=record.priority_band, new_priority_band=new, rule_id="goal-priority.trusted-deadline.v1", evidence_refs=tuple(item.evidence_id for item in request.new_goal_evidence if item.target_goal_ref == record.goal_id), rationale="trusted deadline band"))

    def _conflicts(self, request, records):
        active = [x for x in records if x.status == GoalStatus.ACTIVE]
        considered = [x for x in records if x.status in {GoalStatus.ACTIVE, GoalStatus.SUSPENDED}]
        considered.sort(key=lambda x: x.definition_ref.definition_key == "interaction.discharge_response_obligation")
        active.sort(key=lambda x: x.definition_ref.definition_key == "interaction.discharge_response_obligation")
        conflicts = []
        route = GoalFocusRoute.CONTINUE_FAST
        for left_index, left in enumerate(considered):
            for right in considered[left_index + 1:]:
                left_key, right_key = left.definition_ref.definition_key, right.definition_ref.definition_key
                material_alternative = any("scenario.high-consequence-alternative-retention.v1" in scenario.compatibility_rule_ids for scenario in request.interpretation.scenarios)
                if left_key == right_key == "task.complete_authorized_request" and left.requirements != right.requirements and material_alternative:
                    conflicts.append(GoalConflict(conflict_id=self.identity_factory("conflict", (left.goal_id,right.goal_id)), left_goal_ref=left.goal_id, right_goal_ref=right.goal_id, classification=GoalConflictClassification.MUTUALLY_EXCLUSIVE, materiality="high", reversibility="irreversible", rule_id="goal-conflict.material-tie-route.v1", evidence_refs=(left.source_refs[0], right.source_refs[0]), resolution="unresolved_material_conflict", route=GoalFocusRoute.CLARIFY, rationale="distinct requested effects form a material tie"))
                    route = GoalFocusRoute.CLARIFY
                elif left_key == right_key == "interaction.answer_information_request" and left.requirements != right.requirements:
                    conflicts.append(GoalConflict(conflict_id=self.identity_factory("conflict", (left.goal_id,right.goal_id)), left_goal_ref=left.goal_id, right_goal_ref=right.goal_id, classification=GoalConflictClassification.RESOURCE_COMPETITION, materiality="none", reversibility="reversible", rule_id="goal-conflict.benign-stable-order.v1", evidence_refs=(left.source_refs[0], right.source_refs[0]), resolution="stable_ascending_goal_identity", route=GoalFocusRoute.CONTINUE_FAST, rationale="equivalent alternatives use stable goal identity ordering"))
                elif {left_key, right_key} == {"task.complete_authorized_request", "task.prevent_or_stop_requested_action"}:
                    conflicts.append(GoalConflict(conflict_id=self.identity_factory("conflict", (left.goal_id,right.goal_id)), left_goal_ref=left.goal_id, right_goal_ref=right.goal_id, classification=GoalConflictClassification.MUTUALLY_EXCLUSIVE, materiality="material", reversibility="unknown", rule_id="goal-conflict.material-tie-route.v1", resolution="clarify", route=GoalFocusRoute.CLARIFY, rationale="opposing task effects"))
                    route = GoalFocusRoute.CLARIFY
                elif "epistemic.resolve_material_contradiction" in {left_key, right_key}:
                    conflicts.append(GoalConflict(conflict_id=self.identity_factory("conflict", (left.goal_id,right.goal_id)), left_goal_ref=left.goal_id, right_goal_ref=right.goal_id, classification=GoalConflictClassification.UNKNOWN, materiality="high", reversibility="unknown", rule_id="goal-conflict.unknown-relation.v1", evidence_refs=(left.source_refs[0], right.source_refs[0]), resolution="investigate", route=GoalFocusRoute.SLOW, rationale="material contradiction requires evidence resolution"))
                    route = GoalFocusRoute.SLOW
                elif left.scope != right.scope and left.domain == right.domain and ({left_key, right_key} & {"task.complete_authorized_request", "task.prevent_or_stop_requested_action"}):
                    conflicts.append(GoalConflict(conflict_id=self.identity_factory("conflict", (left.goal_id,right.goal_id)), left_goal_ref=left.goal_id, right_goal_ref=right.goal_id, classification=GoalConflictClassification.RESOURCE_COMPETITION, materiality="moderate", reversibility="reversible", rule_id="goal-conflict.shared-resource.v1", evidence_refs=(left.source_refs[0], right.source_refs[0]), resolution="retain_non_focal", route=GoalFocusRoute.CONTINUE_FAST, rationale="goals compete for one response resource"))
                elif {left_key, right_key} == {"task.complete_authorized_request", "interaction.respond_to_social_contact"}:
                    conflicts.append(GoalConflict(conflict_id=self.identity_factory("conflict", (left.goal_id,right.goal_id)), left_goal_ref=left.goal_id, right_goal_ref=right.goal_id, classification=GoalConflictClassification.RESOURCE_COMPETITION, materiality="moderate", reversibility="reversible", rule_id="goal-conflict.shared-resource.v1", evidence_refs=(left.source_refs[0], right.source_refs[0]), resolution="retain_non_focal", route=GoalFocusRoute.CONTINUE_FAST, rationale="explicit task focus competes with standing social maintenance"))
                elif {left_key, right_key} == {"task.complete_authorized_request", "interaction.answer_information_request"} and left.priority_band != right.priority_band:
                    rank = {GoalPriorityBand.LOW: 1, GoalPriorityBand.MODERATE: 2, GoalPriorityBand.HIGH: 3}
                    high, low = (left, right) if rank[left.priority_band] > rank[right.priority_band] else (right, left)
                    conflicts.append(GoalConflict(conflict_id=self.identity_factory("conflict", (high.goal_id,low.goal_id)), left_goal_ref=high.goal_id, right_goal_ref=low.goal_id, classification=GoalConflictClassification.MUTUALLY_EXCLUSIVE, materiality="moderate", reversibility="reversible", rule_id="goal-conflict.higher-priority.v1", evidence_refs=(high.source_refs[0], low.source_refs[0]), resolution="prefer_higher_priority", suspended_goal_ref=low.goal_id, route=GoalFocusRoute.CONTINUE_FAST, rationale="higher-priority active commitment takes precedence"))
                elif len(active) >= 4 and ((left_key == "interaction.answer_information_request" and right_key in {"interaction.discharge_response_obligation", "interaction.acknowledge_relevant_information", "interaction.repair_shared_understanding"}) or (right_key == "interaction.answer_information_request" and left_key in {"interaction.discharge_response_obligation", "interaction.acknowledge_relevant_information", "interaction.repair_shared_understanding"})):
                    conflicts.append(GoalConflict(conflict_id=self.identity_factory("conflict", (left.goal_id,right.goal_id)), left_goal_ref=left.goal_id, right_goal_ref=right.goal_id, classification=GoalConflictClassification.COMPATIBLE, materiality="low", reversibility="reversible", rule_id="goal-conflict.compatible-effects.v1", evidence_refs=(left.source_refs[0], right.source_refs[0]), resolution="cofocus_or_retain", route=GoalFocusRoute.CONTINUE_FAST, rationale="no registered opposition"))
        pair_order = {"interaction.discharge_response_obligation": 0, "interaction.acknowledge_relevant_information": 1, "interaction.repair_shared_understanding": 2}
        conflicts.sort(key=lambda item: (pair_order.get(next((x.definition_ref.definition_key for x in active if x.goal_id == item.right_goal_ref), ""), 9)))
        return tuple(conflicts), route
    @staticmethod
    def _focus_tier(request, record, current_request_refs) -> int:
        """Priority policy v0.2 precedence tier (lower ranks first).

        0 a prerequisite blocking the current request - only through the typed
          ``parent_goal_ref`` target of a required-unknown subgoal (shared
          scope or age never makes a goal a blocker);
        1 a goal the current turn formed (its explicit request);
        2 this turn's response obligation (the coordination floor);
        3 compatible history: an unresolved goal of an earlier turn;
        4 standing social maintenance from an earlier turn;
        5 another turn's response obligation (lifecycle revisions only).
        """

        key = record.definition_ref.definition_key
        if key == RESPONSE_OBLIGATION_DEFINITION_KEY:
            current_floor = record.scope == GoalScope.TURN and record.scope_ref == request.cognitive_turn_id
            return 2 if current_floor else 5
        if key == REQUIRED_UNKNOWN_DEFINITION_KEY and any(
            target.role == "parent_goal_ref" and target.ref in current_request_refs
            for requirement in record.requirements for target in requirement.target_refs
        ):
            return 0
        if record.created_turn_ref == request.cognitive_turn_id:
            return 1
        return 4 if key == SOCIAL_CONTACT_DEFINITION_KEY else 3

    def _focus(self, request, records, conflicts, route):
        # Two deliberately distinct orders over the same active goals:
        # ``active_in_record_order`` is the canonical snapshot order that
        # GoalSnapshot validates projections against, and ``active`` is the
        # semantic ranking that selects primary/supporting focus.  Emitting a
        # record-order projection from the semantic ranking fails validation
        # once two or more active goals remain non-focal.
        active_in_record_order = tuple(x for x in records if x.status == GoalStatus.ACTIVE)
        semantic_order = {"epistemic.resolve_required_unknown": -1, "interaction.answer_information_request": 0, "task.complete_authorized_request": 1, "interaction.handle_action_request": 1, "interaction.fulfill_conversational_deliverable": 1, "interaction.incorporate_inquiry_answer": 1, "interaction.acknowledge_relevant_information": 2, "interaction.repair_shared_understanding": 3, "interaction.respond_to_social_contact": 4}
        if not active_in_record_order: raise ValueError("no active goals to focus")
        current_request_refs = {
            x.goal_id for x in active_in_record_order
            if x.created_turn_ref == request.cognitive_turn_id
            and x.definition_ref.definition_key != RESPONSE_OBLIGATION_DEFINITION_KEY
        }
        tier = {x.goal_id: self._focus_tier(request, x, current_request_refs) for x in active_in_record_order}
        def rank(x):
            key = x.definition_ref.definition_key
            return (tier[x.goal_id], 0 if key in semantic_order else 1, semantic_order.get(key, 9), x.goal_id)
        active = sorted(active_in_record_order, key=rank)
        # A satisfied sibling only demotes a goal within the same scope; a
        # different turn's satisfied goal is history, not a reason to skip
        # the current turn's request.
        def satisfied_sibling(x):
            return x.definition_ref.definition_key != RESPONSE_OBLIGATION_DEFINITION_KEY and any(
                other.goal_id != x.goal_id and other.definition_ref == x.definition_ref
                and other.scope_ref == x.scope_ref and other.status == GoalStatus.SATISFIED
                for other in records
            )
        eligible = [x for x in active if not satisfied_sibling(x)]
        primary = eligible[0] if eligible else next((item for item in active if item.definition_ref.definition_key == RESPONSE_OBLIGATION_DEFINITION_KEY), active[0])
        if route == GoalFocusRoute.CLARIFY and any(c.classification == GoalConflictClassification.MUTUALLY_EXCLUSIVE for c in conflicts):
            primary=next((x for x in active if x.definition_ref.definition_key == RESPONSE_OBLIGATION_DEFINITION_KEY), primary)
        conflict_pairs={(c.left_goal_ref,c.right_goal_ref) for c in conflicts if c.classification != GoalConflictClassification.COMPATIBLE} | {(c.right_goal_ref,c.left_goal_ref) for c in conflicts if c.classification != GoalConflictClassification.COMPATIBLE}
        blocked_parent_refs = {target.ref for requirement in primary.requirements for target in requirement.target_refs if target.role == "parent_goal_ref"}
        terminal_sibling = any(satisfied_sibling(item) for item in active)
        supporting_candidates = [x for x in active if x.goal_id != primary.goal_id and x.goal_id not in blocked_parent_refs and x.definition_ref != primary.definition_ref and ((primary.goal_id,x.goal_id) not in conflict_pairs or x.definition_ref.definition_key == RESPONSE_OBLIGATION_DEFINITION_KEY)]
        if primary.definition_ref.definition_key == RESPONSE_OBLIGATION_DEFINITION_KEY and terminal_sibling:
            supporting_candidates = []
        # This turn's response obligation is always the first supporting goal
        # (the coordination floor); the rest follow the precedence tiers.
        supporting_candidates.sort(key=lambda x: (0 if tier[x.goal_id] == 2 else 1, *rank(x)))
        supporting=() if route == GoalFocusRoute.CLARIFY else tuple(x.goal_id for x in supporting_candidates)[:2]
        focal = {primary.goal_id, *supporting}
        # The precedence rule is cited whenever active history (an earlier
        # turn's non-obligation goal) was ranked below the current turn.
        precedence_applied = tier[primary.goal_id] <= 2 and any(value in {3, 4} for value in tier.values())
        conflict_rules = tuple(dict.fromkeys(item.rule_id for item in conflicts))
        if conflict_rules and "goal-conflict.unknown-relation.v1" not in conflict_rules:
            rule_ids = conflict_rules
            if "goal-conflict.shared-resource.v1" in rule_ids and primary.definition_ref.definition_key.startswith("task."):
                rule_ids = (*rule_ids, "goal-priority.current-explicit-request.v1")
            if precedence_applied:
                rule_ids = (*rule_ids, PRECEDENCE_RULE)
        elif primary.definition_ref.definition_key == "epistemic.resolve_required_unknown":
            rule_ids = (
                "goal-priority.blocking-required-subgoal.v1",
                "goal-conflict.compatible-effects.v1",
            )
            if precedence_applied:
                rule_ids = (*rule_ids, PRECEDENCE_RULE)
        elif precedence_applied:
            rule_ids = (PRECEDENCE_RULE,)
        else:
            rule_ids = ("goal-priority.definition-default.v1",)
            missing_grounding_rejection = any(
                item.status == GoalStatus.REJECTED
                and item.adoption_basis.rule_id == "goal-adopt.required-subgoal.v1"
                for item in records
            )
            if request.previous_goal_snapshot is None and not missing_grounding_rejection:
                rule_ids = (*rule_ids, "goal-conflict.compatible-effects.v1")
        evidence_refs, floor = self._focus_evidence(request, records, primary, supporting)
        return GoalFocusDecision(
            focus_decision_id=self.identity_factory("focus", (request.cognitive_turn_id,primary.goal_id,str((request.previous_goal_snapshot.revision + 1) if request.previous_goal_snapshot else 1))),
            primary_goal_ref=primary.goal_id,
            supporting_goal_refs=supporting,
            non_focal_active_goal_refs=tuple(x.goal_id for x in active_in_record_order if x.goal_id not in focal),
            rule_ids=rule_ids,
            evidence_refs=evidence_refs,
            route=route,
            rationale="Focus is computed from the complete active set after gates, priority, and conflict resolution.",
            coordination_floor_goal_ref=floor.goal_id if floor is not None else None,
        )

    @staticmethod
    def _focus_evidence(request, records, primary, supporting):
        """Resolve focus provenance from exact records, never by definition alone.

        The coordination floor is this turn's active response obligation,
        matched by status, scope, owning turn, and definition together; zero
        matches means there is no floor, several is an integrity failure.
        Without a floor, evidence comes from the exact focal records.
        """

        floors = [
            item for item in records
            if item.status == GoalStatus.ACTIVE
            and item.scope == GoalScope.TURN
            and item.scope_ref == request.cognitive_turn_id
            and item.definition_ref.definition_key == RESPONSE_OBLIGATION_DEFINITION_KEY
        ]
        if len(floors) > 1:
            raise ValueError("several active current-turn response obligations")
        if floors:
            return floors[0].source_refs, floors[0]
        by_id = {item.goal_id: item for item in records}
        focal = (primary, *(by_id[ref] for ref in supporting))
        return tuple(dict.fromkeys(ref for item in focal for ref in item.source_refs)), None

    def _transition(self, record, kind, request, sequence, from_status=None, new_record=False, rule_id=None, replacement_goal_ref=None):
        source_status = None if new_record else (from_status if from_status is not None else record.status)
        targeted = tuple(item for item in request.new_goal_evidence if item.target_goal_ref == record.goal_id) if kind not in {GoalTransitionKind.PROPOSE, GoalTransitionKind.ADOPT, GoalTransitionKind.REJECT} else ()
        reason = (
            "candidate_not_adopted" if kind == GoalTransitionKind.REJECT
            else "rule_conditions_met" if kind in {GoalTransitionKind.PROPOSE, GoalTransitionKind.ADOPT}
            else kind.value
        )
        return GoalTransition(transition_id=self.identity_factory("transition",(record.goal_id,str(sequence),kind.value)),goal_id=record.goal_id,sequence=sequence,from_status=source_status,to_status=next_status(source_status,kind),transition_kind=kind,rule_id=rule_id or record.adoption_basis.rule_id,cognitive_turn_id=request.cognitive_turn_id,previous_snapshot_ref=request.previous_goal_snapshot.goal_snapshot_id if request.previous_goal_snapshot else None,resulting_snapshot_ref="pending",replacement_goal_ref=replacement_goal_ref,source_refs=record.source_refs,evidence_refs=tuple(item.evidence_id for item in targeted),outcome_refs=tuple(item.outcome_id for item in targeted if item.outcome_id),reason_code=reason)
