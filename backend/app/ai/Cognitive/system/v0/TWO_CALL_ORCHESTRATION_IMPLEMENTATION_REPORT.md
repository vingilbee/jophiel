# Two-call orchestration implementation report

> **Document status — 2026-10-18:** Historical implementation/evaluation record. Results, configuration, “current” status, and next-step instructions below apply to the recorded revision and sample. They are not the current backlog or proof of present behavior. Start with the [current guide](README.md) and [document index](DOCUMENTATION_INDEX.md).

Status: experimental two-call route implemented and verified offline. Live provider latency and activation remain pending.

## Deterministic consumer inventory

Fields below are read directly from installed Pydantic contracts. Existing schemas, scales, ownership and reference validation remain authoritative.

### app.ai.Cognitive.system.v0.interpretation_v0.models.InterpretationRequest

| Field | Type | Required |
| --- | --- | --- |
| schema_version | `typing.Literal[1]` | False |
| cognitive_turn_id | `<class 'str'>` | True |
| conversation_ref | `<class 'str'>` | True |
| perception | `<class 'app.ai.Cognitive.system.v0.perception_v0.models.PerceptionInterpretationHandoff'>` | True |
| messages | `tuple[app.ai.Cognitive.system.v0.perception_v0.models.PerceptionMessage, ...]` | True |
| runtime | `<class 'app.ai.Cognitive.system.v0.interpretation_v0.models.InterpretationRuntimeContext'>` | True |
| memories | `tuple[app.ai.Cognitive.system.v0.interpretation_v0.models.InterpretationMemory, ...]` | False |
| relationship | `app.ai.Cognitive.system.v0.interpretation_v0.models.InterpretationRelationshipContext | None` | False |
| biases | `<class 'app.ai.Cognitive.system.v0.interpretation_v0.models.InterpretationBiasProfile'>` | True |
| language_tag | `str | None` | False |
| ontology_version | `<class 'str'>` | False |
| feature_manifest_version | `<class 'str'>` | False |
| dialogue_context | `app.ai.Cognitive.system.v0.interpretation_v0.models.InterpretationDialogueContext | None` | False |
| open_inquiries | `tuple[app.ai.Cognitive.system.v0.interpretation_v0.models.InterpretationOpenInquiry, ...]` | False |

### app.ai.Cognitive.system.v0.contracts.interpretation.SemanticFrame

| Field | Type | Required |
| --- | --- | --- |
| frame_id | `<class 'str'>` | True |
| predicate | `<class 'str'>` | True |
| arguments | `collections.abc.Mapping[str, typing.Any]` | False |
| polarity | `typing.Literal['positive', 'negative', 'unknown']` | True |
| evidence_refs | `tuple[str, ...]` | True |
| unresolved_arguments | `tuple[str, ...]` | False |
| grounding_rule_ids | `tuple[str, ...]` | True |

### app.ai.Cognitive.system.v0.contracts.interpretation.InterpretationEnvelope

| Field | Type | Required |
| --- | --- | --- |
| schema_version | `typing.Literal[1]` | False |
| interpretation_trace_id | `<class 'str'>` | True |
| cognitive_turn_id | `<class 'str'>` | True |
| conversation_ref | `<class 'str'>` | True |
| perception_event_id | `<class 'str'>` | True |
| input_message_ids | `tuple[str, ...]` | True |
| focus_message_id | `<class 'str'>` | True |
| runtime_snapshot_id | `<class 'str'>` | True |
| evidence | `tuple[app.ai.Cognitive.system.v0.contracts.interpretation.InterpretationEvidence, ...]` | True |
| semantic_frames | `tuple[app.ai.Cognitive.system.v0.contracts.interpretation.SemanticFrame, ...]` | True |
| hypotheses | `tuple[app.ai.Cognitive.system.v0.contracts.interpretation.InterpretationHypothesis, ...]` | True |
| scenarios | `tuple[app.ai.Cognitive.system.v0.contracts.interpretation.InterpretationScenario, ...]` | True |
| unknowns | `tuple[app.ai.Cognitive.system.v0.contracts.interpretation.UnknownInput, ...]` | False |
| contradictions | `tuple[app.ai.Cognitive.system.v0.contracts.interpretation.Contradiction, ...]` | False |
| out_of_scope_records | `tuple[app.ai.Cognitive.system.v0.contracts.interpretation.OutOfScopeRecord, ...]` | False |
| context_truncated | `<class 'bool'>` | False |
| versions | `<class 'app.ai.Cognitive.system.v0.contracts.interpretation.InterpretationVersions'>` | True |

### app.ai.Cognitive.system.v0.goal_v0.models.GoalUpdateRequest

| Field | Type | Required |
| --- | --- | --- |
| schema_version | `<class 'int'>` | False |
| trigger | `<enum 'GoalUpdateTrigger'>` | True |
| cognitive_turn_id | `<class 'str'>` | True |
| conversation_ref | `<class 'str'>` | True |
| interpretation | `<class 'app.ai.Cognitive.system.v0.contracts.interpretation.InterpretationEnvelope'>` | True |
| runtime | `<class 'app.ai.Cognitive.system.v0.goal_v0.models.GoalRuntimeContext'>` | True |
| previous_goal_snapshot | `app.ai.Cognitive.system.v0.contracts.goals.GoalSnapshot | None` | False |
| previous_dynamic_state | `<class 'app.ai.Cognitive.system.v0.dynamic_state.models.StateSnapshot'>` | True |
| new_goal_evidence | `tuple[app.ai.Cognitive.system.v0.contracts.goals.GoalEvidence, ...]` | False |
| turn_rollover | `app.ai.Cognitive.system.v0.contracts.goals.GoalTurnRollover | None` | False |

### app.ai.Cognitive.system.v0.contracts.goals.GoalRecord

| Field | Type | Required |
| --- | --- | --- |
| goal_id | `<class 'str'>` | True |
| definition_ref | `<class 'app.ai.Cognitive.system.v0.contracts.goals.GoalDefinitionRef'>` | True |
| owner | `<enum 'GoalOwner'>` | True |
| domain | `<enum 'GoalDomain'>` | True |
| mode | `<enum 'GoalMode'>` | True |
| scope | `<enum 'GoalScope'>` | True |
| scope_ref | `<class 'str'>` | True |
| priority_band | `<enum 'GoalPriorityBand'>` | True |
| status | `<enum 'GoalStatus'>` | True |
| requirements | `tuple[app.ai.Cognitive.system.v0.contracts.goals.GoalRequirement, ...]` | False |
| adoption_basis | `<class 'app.ai.Cognitive.system.v0.contracts.goals.GoalAdoptionBasis'>` | True |
| created_turn_ref | `<class 'str'>` | True |
| last_transition_ref | `<class 'str'>` | True |
| source_refs | `tuple[str, ...]` | False |

### app.ai.Cognitive.system.v0.contracts.goals.GoalSnapshot

| Field | Type | Required |
| --- | --- | --- |
| schema_version | `<class 'int'>` | False |
| goal_snapshot_id | `<class 'str'>` | True |
| revision | `<class 'int'>` | True |
| supersedes_goal_snapshot_id | `str | None` | False |
| cognitive_turn_id | `<class 'str'>` | True |
| conversation_ref | `<class 'str'>` | True |
| runtime_snapshot_ref | `<class 'str'>` | True |
| interpretation_trace_ref | `<class 'str'>` | True |
| previous_dynamic_state_ref | `<class 'str'>` | True |
| input_outcome_refs | `tuple[str, ...]` | False |
| records | `tuple[app.ai.Cognitive.system.v0.contracts.goals.GoalRecord, ...]` | False |
| requirement_assessments | `tuple[app.ai.Cognitive.system.v0.contracts.goals.GoalRequirementAssessment, ...]` | False |
| evidence_conflicts | `tuple[app.ai.Cognitive.system.v0.contracts.goals.GoalEvidenceConflict, ...]` | False |
| transitions | `tuple[app.ai.Cognitive.system.v0.contracts.goals.GoalTransition, ...]` | False |
| priority_changes | `tuple[app.ai.Cognitive.system.v0.contracts.goals.GoalPriorityChange, ...]` | False |
| conflicts | `tuple[app.ai.Cognitive.system.v0.contracts.goals.GoalConflict, ...]` | False |
| focus_decision | `<class 'app.ai.Cognitive.system.v0.contracts.goals.GoalFocusDecision'>` | True |
| active_goal_refs | `tuple[str, ...]` | False |
| focal_goal_refs | `tuple[str, ...]` | False |
| versions | `<class 'app.ai.Cognitive.system.v0.contracts.goals.GoalVersions'>` | False |

### app.ai.Cognitive.system.v0.appraisal.models.AppraisalRequest

| Field | Type | Required |
| --- | --- | --- |
| cognitive_turn_id | `<class 'str'>` | True |
| interpretation | `<class 'app.ai.Cognitive.system.v0.contracts.interpretation.InterpretationEnvelope'>` | True |
| active_goals | `tuple[app.ai.Cognitive.system.v0.contracts.goals.ActiveGoalView, ...]` | True |
| runtime | `<class 'app.ai.Cognitive.system.v0.appraisal.models.RuntimeContext'>` | True |
| memories | `tuple[app.ai.Cognitive.system.v0.appraisal.models.MemoryRecord, ...]` | False |
| values | `tuple[app.ai.Cognitive.system.v0.appraisal.models.ValueRecord, ...]` | False |
| relationship | `app.ai.Cognitive.system.v0.appraisal.models.RelationshipSnapshot | None` | False |
| supplemental_inputs | `tuple[app.ai.Cognitive.system.v0.appraisal.models.SupplementalInput, ...]` | False |
| previous_frames | `tuple[app.ai.Cognitive.system.v0.appraisal.models.AppraisalFrame, ...]` | False |
| frame_id_overrides | `collections.abc.Mapping[str, str]` | False |

### app.ai.Cognitive.system.v0.appraisal.models.AppraisalDimension

| Field | Type | Required |
| --- | --- | --- |
| dimension_key | `<class 'str'>` | True |
| definition_version | `<class 'int'>` | False |
| status | `<enum 'DimensionStatus'>` | True |
| value_type | `<class 'str'>` | True |
| value | `str | None` | False |
| derivation_strength | `str | None` | False |
| evidence_refs | `tuple[str, ...]` | False |
| rule_id | `str | None` | False |
| required_input_refs | `tuple[str, ...]` | False |
| missing_input_keys | `tuple[str, ...]` | False |
| conflict_refs | `tuple[str, ...]` | False |

### app.ai.Cognitive.system.v0.appraisal.models.AppraisalResult

| Field | Type | Required |
| --- | --- | --- |
| model_trace | `app.ai.Cognitive.system.v0.appraisal.model_contracts.AppraisalModelTrace | None` | False |
| schema_version | `<class 'int'>` | False |
| appraisal_trace_id | `<class 'str'>` | True |
| cognitive_turn_id | `<class 'str'>` | True |
| interpretation_trace_id | `<class 'str'>` | True |
| frames | `tuple[app.ai.Cognitive.system.v0.appraisal.models.AppraisalFrame, ...]` | True |
| aggregate | `<class 'app.ai.Cognitive.system.v0.appraisal.models.AppraisalAggregate'>` | True |
| versions | `collections.abc.Mapping[str, str]` | True |

### app.ai.Cognitive.system.v0.dynamic_state.models.UpdateRequest

| Field | Type | Required |
| --- | --- | --- |
| cognitive_turn_id | `<class 'str'>` | True |
| conversation_ref | `<class 'str'>` | True |
| previous_snapshot | `<class 'app.ai.Cognitive.system.v0.dynamic_state.models.StateSnapshot'>` | True |
| personality | `<class 'app.ai.Cognitive.system.v0.dynamic_state.models.PersonalityProfile'>` | True |
| elapsed_monotonic_ms | `<class 'int'>` | False |
| appraisal_frames | `tuple[app.ai.Cognitive.system.v0.appraisal.models.AppraisalFrame, ...]` | False |
| outcomes | `tuple[app.ai.Cognitive.system.v0.dynamic_state.models.OutcomeObservation, ...]` | False |
| context | `app.ai.Cognitive.system.v0.dynamic_state.models.DynamicStateContext | None` | False |
| evidence | `tuple[app.ai.Cognitive.system.v0.dynamic_state.models.TransitionEvidence, ...]` | False |
| attention_signals | `tuple[app.ai.Cognitive.system.v0.dynamic_state.models.AttentionSignal, ...]` | False |
| control_context | `<class 'app.ai.Cognitive.system.v0.dynamic_state.models.ControlContext'>` | False |

### app.ai.Cognitive.system.v0.contracts.actions.ActionSelectionRequest

| Field | Type | Required |
| --- | --- | --- |
| schema_version | `typing.Literal['action-contract-v0.4']` | False |
| cognitive_turn_id | `<class 'str'>` | True |
| conversation_ref | `<class 'str'>` | True |
| core_memory_ref | `<class 'str'>` | True |
| interpretation | `<class 'app.ai.Cognitive.system.v0.contracts.interpretation.InterpretationEnvelope'>` | True |
| goal_snapshot | `<class 'app.ai.Cognitive.system.v0.contracts.goals.GoalSnapshot'>` | True |
| active_goals | `tuple[app.ai.Cognitive.system.v0.contracts.goals.ActiveGoalView, ...]` | True |
| appraisal | `<class 'app.ai.Cognitive.system.v0.appraisal.models.AppraisalResult'>` | True |
| dynamic_state | `<class 'app.ai.Cognitive.system.v0.dynamic_state.models.StateSnapshot'>` | True |
| runtime | `<class 'app.ai.Cognitive.system.v0.contracts.actions.ActionRuntimeContext'>` | True |
| personality | `<class 'app.ai.Cognitive.system.v0.contracts.actions.ActionPersonalityProfile'>` | True |
| grounded_objects | `tuple[app.ai.Cognitive.system.v0.contracts.actions.ActionGroundedObject, ...]` | False |
| target_descriptors | `tuple[app.ai.Cognitive.system.v0.contracts.actions.ActionTargetDescriptor, ...]` | False |
| prior_action_records | `tuple[app.ai.Cognitive.system.v0.contracts.actions.PriorActionRecord, ...]` | False |
| prior_realization_records | `tuple[app.ai.Cognitive.system.v0.contracts.actions.PriorRealizationRecord, ...]` | False |
| observed_outcomes | `tuple[app.ai.Cognitive.system.v0.contracts.actions.ObservedOutcomeRecord, ...]` | False |
| versions | `<class 'app.ai.Cognitive.system.v0.contracts.actions.ActionVersions'>` | False |
| requirement_handoff | `app.ai.Cognitive.system.v0.contracts.requirement_resolution.RequirementActionHandoff | None` | False |
| collaboration_handoff | `app.ai.Cognitive.system.v0.contracts.collaboration.CollaborationActionHandoff | None` | False |

### app.ai.Cognitive.system.v0.contracts.actions.ActionSelectionResult

| Field | Type | Required |
| --- | --- | --- |
| result_id | `<class 'str'>` | True |
| cognitive_turn_id | `<class 'str'>` | True |
| conversation_ref | `<class 'str'>` | True |
| request_digest | `<class 'str'>` | True |
| context_ref | `<class 'str'>` | True |
| plan | `<class 'app.ai.Cognitive.system.v0.contracts.actions.ActionPlan'>` | True |
| steps | `tuple[app.ai.Cognitive.system.v0.contracts.actions.ActionPlanningStep, ...]` | True |
| moves | `tuple[app.ai.Cognitive.system.v0.contracts.actions.GroundedActionMove, ...]` | False |
| planned_effects | `tuple[app.ai.Cognitive.system.v0.contracts.actions.PlannedActionEffect, ...]` | False |
| shadow_only | `typing.Literal[True]` | False |
| safety | `<class 'app.ai.Cognitive.system.v0.contracts.actions.ActionSafetyBasis'>` | True |
| versions | `<class 'app.ai.Cognitive.system.v0.contracts.actions.ActionVersions'>` | False |
| planning_conditions | `tuple[app.ai.Cognitive.system.v0.contracts.actions.ActionPlanningCondition, ...]` | False |

### app.ai.Cognitive.system.v0.contracts.realization.RealizationContentInventory

| Field | Type | Required |
| --- | --- | --- |
| inventory_id | `<class 'str'>` | True |
| cognitive_turn_id | `<class 'str'>` | True |
| content_version | `typing.Literal['realization-content-v0.3']` | False |
| producer_refs | `tuple[str, ...]` | True |
| items | `tuple[typing.Annotated[app.ai.Cognitive.system.v0.contracts.realization.GroundedProposition | app.ai.Cognitive.system.v0.contracts.realization.GroundedQuestion | app.ai.Cognitive.system.v0.contracts.realization.KnowledgeGap | app.ai.Cognitive.system.v0.contracts.realization.DialogueReference | app.ai.Cognitive.system.v0.contracts.realization.AuthorizedQuote | app.ai.Cognitive.system.v0.contracts.realization.ArtifactPresentation | app.ai.Cognitive.system.v0.contracts.realization.InquiryPrompt, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` | False |

### app.ai.Cognitive.system.v0.contracts.realization.RealizationRequestV0

| Field | Type | Required |
| --- | --- | --- |
| schema_version | `typing.Literal['realization-contract-v0.3']` | False |
| request_id | `<class 'str'>` | True |
| cognitive_turn_id | `<class 'str'>` | True |
| conversation_ref | `<class 'str'>` | True |
| language_tag | `typing.Literal['en']` | True |
| linguistic_context | `<class 'app.ai.Cognitive.system.v0.contracts.realization.RealizationLinguisticContext'>` | True |
| action_result_ref | `<class 'str'>` | True |
| action_plan | `<class 'app.ai.Cognitive.system.v0.contracts.realization.RealizationActionPlan'>` | True |
| content_items | `tuple[typing.Annotated[app.ai.Cognitive.system.v0.contracts.realization.GroundedProposition | app.ai.Cognitive.system.v0.contracts.realization.GroundedQuestion | app.ai.Cognitive.system.v0.contracts.realization.KnowledgeGap | app.ai.Cognitive.system.v0.contracts.realization.DialogueReference | app.ai.Cognitive.system.v0.contracts.realization.AuthorizedQuote | app.ai.Cognitive.system.v0.contracts.realization.ArtifactPresentation | app.ai.Cognitive.system.v0.contracts.realization.InquiryPrompt, FieldInfo(annotation=NoneType, required=True, discriminator='kind')], ...]` | False |
| move_bindings | `tuple[app.ai.Cognitive.system.v0.contracts.realization.RealizationMoveBinding, ...]` | True |
| discourse_controls | `<class 'app.ai.Cognitive.system.v0.contracts.realization.DiscourseControls'>` | True |
| delivery_profile | `<class 'app.ai.Cognitive.system.v0.contracts.realization.ResolvedDeliveryProfile'>` | True |
| constraints | `tuple[app.ai.Cognitive.system.v0.contracts.realization.RealizationConstraintItem, ...]` | True |
| provenance | `<class 'app.ai.Cognitive.system.v0.contracts.realization.RealizationProvenance'>` | True |
| request_digest | `<class 'str'>` | True |

## Versions and scales

Goal versions: `{'goal_contract_schema': 'goal-contract-v0.3-draft', 'goal_ontology': 'goal-ontology-v0.3-draft', 'goal_lifecycle_policy': 'goal-lifecycle-v0.3-draft', 'goal_formation_policy': 'goal-formation-v0.5-draft', 'goal_priority_policy': 'goal-priority-v0.4-draft', 'goal_satisfaction_policy': 'goal-satisfaction-v0.4-draft', 'goal_engine': 'goal-engine-v0.5', 'goal_adoption_policy': 'goal-adoption-policy-v0.3-draft', 'goal_evidence_policy': 'goal-evidence-policy-v0.3-draft'}`

Action versions: `{'contract_schema': 'action-contract-v0.4', 'registry': 'conversational-actions-v0.2-shadow', 'construction': 'action-construction-v0.2', 'hard_gate': 'action-hard-gate-v0.1', 'features': 'action-features-v0.4', 'policy': 'action-policy-v0.1', 'planner': 'action-planner-v0.4', 'numeric_policy': 'action-numeric-decimal6-v0.1', 'safety_decision_schema': 'application-safety-decision-v0.1', 'safety_policy': 'application-safety-policy-v0.1'}`

Appraisal dimensions: `{'appraisal.goal_relevance': ('unsigned_ordinal', 'Significance for the supplied focal goal.'), 'appraisal.goal_congruence': ('signed_ordinal', 'Whether the event advances or obstructs the focal goal.'), 'appraisal.novelty': ('unsigned_ordinal', 'Unexpectedness relative to supplied history; absent history is unknown.'), 'appraisal.certainty': ('unsigned_ordinal', 'Confidence in understanding this event, not confidence in the user.'), 'appraisal.controllability': ('unsigned_ordinal', 'Available effective response within supplied capabilities and permissions.'), 'appraisal.norm_compatibility': ('signed_ordinal', 'Compatibility with supplied values and explicit boundaries.'), 'appraisal.affiliative_value': ('signed_ordinal', 'Cooperation, repair or boundary conflict; disagreement is not rejection.'), 'appraisal.agency': ('agency_category', 'Grounded causal agent; never an inferred hidden motive.')}`

Appraisal scales: `{'unsigned_ordinal': ('low', 'moderate', 'high'), 'signed_ordinal': ('negative', 'neutral', 'positive'), 'agency_category': ('user', 'columbina', 'third_party', 'environment', 'unknown')}`

## Ownership and binding rules

- Perception and semantic compilers own evidence spans and canonical frame IDs. Duplicate semantic acts are normalized by the existing compiler; local proposal handles cannot override it.
- GoalEngine owns goal IDs, adoption and lifecycle. Local outcome handles must resolve through accepted frame source links or exact existing goal IDs. No model-supplied future goal IDs.
- Appraisal compiler owns frame IDs, revisions, subject and aggregate. Its scoped evidence, known/unknown/nullability and dimension-scale validation are unchanged.
- DynamicStateUpdater owns post-appraisal state, supersession and scope. Call 2 must receive its actual output.
- CollaborationCompiler owns patches, revisions, claims, inquiries and source validation. Content proposals do not confer workspace authority.
- CandidateConstructor and HardGate own candidate IDs, availability, safety, permissions and effects. Proposed behavior must match the accepted plan; disagreements fail rather than reusing words for a different action.
- RealizationCompiler and RealizationValidator own the normal request, inventory bindings, text spans and acceptance. A cached second-call candidate never authorizes delivery.
- Canonical JSON hashes bind frozen inputs and proposals. Diagnostic timings/token counts are excluded from semantic replay digests; prompt/protocol/compiler versions remain included.

## Collaboration compiler consumer inventory

Class: `collaborative_content_v0.compiler.CompileContext` (existing dataclass).

| Field | Type |
| --- | --- |
| cognitive_turn_id | `str` |
| conversation_ref | `str` |
| goal_ref | `str` |
| frame_ref | `str` |
| operation | `ArtifactOperation` |
| artifact_type | `ArtifactType` |
| target | `WorkspaceTargetBinding` |
| base | `ArtifactRevision | None` |
| sources | `SourceSet` |
| inquiry_history | `tuple[OpenInquiryRecord, ...]` |
| curiosity | `float | None` |
| max_inquiries | `int` |

Collaboration versions: `{'contract': 'collaborative-content-contract-v0.1', 'compiler': 'collaborative-content-compiler-v0.1', 'protocol': 'collaborative-content-model-protocol-v0.1', 'inquiry_policy': 'inquiry-timing-policy-v0.1', 'workspace_resolution': 'workspace-resolution-v0.1', 'projection': 'collaboration-action-projection-v0.1', 'content_producer': 'collaboration-content-projection-v0.1'}`

Realization versions: `{'contract': 'realization-contract-v0.3', 'content': 'realization-content-v0.3', 'action_manifest': 'realization-action-manifest-v0.2', 'style_manifest': 'realization-style-manifest-v0.1', 'compiler': 'realization-compiler-v0.3', 'model_protocol': 'realization-model-protocol-v0.3', 'validator': 'realization-validator-v0.3', 'engine': 'realization-engine-v0.1', 'semantic_digest': 'realization-semantic-digest-v0.1'}`

## Implemented path

1. Call 1 emits semantic candidates and outcome assessments together. The existing semantic compiler, Interpretation engine and Goal engine remain authoritative. A `goal-formation-binding-v0.1` sidecar records the actual formation basis without changing goal records/IDs. Appraisal local handles bind through accepted frames/goals and use the ordinary proposal compiler and DynamicStateUpdater.
2. Call 2 receives the actual post-update state, accepted Interpretation/Appraisal, exact workspace, readiness, curated persona/history, action definitions/recipes and bounded content requests. It emits content, ordered behavior, and wording in three sections.
3. Existing Collaboration compilation/projection builds accepted objects. The ordinary Action engine constructs, gates and plans them. Local targets resolve only against accepted objects. Experimental `strict_plan_agreement-v0.1` rejects any proposed action/target sequence differing from that plan; it never substitutes an action under existing wording. This compatibility policy does not replace deterministic scoring with model-selected behavior.
4. Ordinary Realization request/inventory compilation and validation process the captured candidate (`generation_source=captured_two_call`). There is no separate Realization API request or repair attempt. Frozen replay verifies input/prompt/proposal captures and text provenance, including the content-dependent path.

## Configuration and latency

- `COGNITIVE_SHADOW_ORCHESTRATION=two_call` opts into the experimental shadow route. Default `staged` preserves the current stage engines. The local running configuration was not activated.
- Both calls use the explicitly configured semantic-parser model (`gpt-6-luna` by default), reasoning effort `none`, SDK retries zero, one candidate, storage/tools/streaming disabled, and a bounded 6,000-token output budget. Other stage settings remain available to the staged path.
- Typed provider/compilation failures are terminal on this route rather than automatically repeating LLM work in the worker. Infrastructure retries remain a separate concern; telemetry explicitly covers one worker attempt, not an asserted lifetime total.
- Versioned spans cover payload preparation, both provider attempts and response compilation, worker attempt and available token usage. Frozen replay spans are skipped. Optional combined traces use semantic digest v0.2; historical v0.1 dumps retain their behavior.
- Call 2 projects relevant definitions and consumer fields instead of resending the whole registry/manifest. The hermetic greeting input fell from 164,251 to 30,481 canonical characters (81.4% smaller). This is payload reduction, not measured remote latency improvement.
- The normal successful route has two provider requests. Content compilation and Realization validation use captured proposals. Optional observers and repair calls are not enabled.

## Validation and outstanding gates

Hermetic checks cover exact two calls, post-state ownership, new goal bindings, unknown/null scales, foreign refs and dimension rejection, content-dependent future IDs, invalid content stopping the plan/Realization, exact behavior/target agreement, stored terminal replay, supplied wording without a third provider, timeout spans and missing credentials without fallback. Scripted doubles establish contracts, not live model quality.

A synthetic `Hi` canary failed at network transport before receiving provider usage. Automatic approval review rejected the request for network access because it would use configured credentials and transmit internal context without explicit authorization for the destination/payload. No live latency conclusion can be drawn. The reviewable harness is `backend/scripts/cognitive_two_call_canary.py`: synthetic greeting, a test persona/initial state and code-owned schemas, no real conversation, at most two requests, and no chat delivery. Approval is needed before retrying it.

Live behavioral/latency corpus evaluation, PostgreSQL checks with TEST_DATABASE_URL, and a decision on replacing strict plan agreement with a separately versioned model-selected policy remain acceptance gates. This implementation does not complete every phase of the revised handoff.

Final regression result: **295 passed, 37 skipped** across semantic grounding, Appraisal, pipeline, summary, worker, collaboration, Realization and combined orchestration suites. Skips include PostgreSQL-dependent coverage without TEST_DATABASE_URL. After the final capture-validation hardening, focused combined/replay checks also passed (**21 passed, 1 skipped**). `git diff --check` passed. No live provider latency or authenticated production-path result is claimed.


## 2026-10-03: repair of the Call 1 → Call 2 boundary

The live evaluation reported 30 generic `appraisal_binding` rejections, one
semantic vocabulary rejection, one timeout, and zero Call 2 attempts. The saved
failed canary did not include the raw provider proposal, so its generic code
cannot establish which individual binding guard rejected those 30 responses.
The earlier scripted unknown-only evaluations were insufficient evidence that
real model proposals could traverse this boundary.

Inspection and regressions identified and repaired these concrete defects:

- Local frame lookup repeated only part of semantic normalization. It now uses
  the compiler's shared candidate-argument normalization, including deictic
  referents compiled to null.
- The adapter rejected the supplied capability snapshot as evidence even though
  the canonical Appraisal compiler accepts it. Both layers now accept that same
  supplied reference; known ratings still require an accepted event citation.
- A missing assessment for any code-adopted goal aborted the whole turn. Under
  `goal-formation-binding-v0.2`, omitted or ambiguous assessments compile to
  explicit unknown/null dimensions with `two_call_missing_goal_assessment` or
  `two_call_ambiguous_goal_assessment` missing-input keys. They neither borrow
  another goal's rating nor invent a neutral score. Call 2 can then run after
  the normal state update. Frozen v0.1 captures retain strict coverage behavior.
- Provider schemas now constrain response-obligation handles to empty, new
  outcome handles to nonempty nonnegative indices, existing refs to supplied
  goal IDs, and appraisal citations to supplied evidence/context references.
- Binding failures now retain specific content-free subjects, including
  `frame_handle_foreign`, `existing_goal_foreign`, `evidence_foreign`, and
  `accepted_event_missing`. The ordinary Appraisal compiler still validates
  dimension coverage, scales, epistemic basis and scoped references.

The canary supports opt-in `--capture-proposals`: a companion local
`.proposals.json` contains captures even when compilation rejects Call 1, so
subsequent failures can be reproduced offline. It does not add provider calls
or change durable production failure payloads.

Verification: 232 tests passed and 37 integration tests skipped across combined
orchestration, semantic compilation, Appraisal, pipeline, frozen replay, worker,
Realization, and collaboration suites. Added cases exercise known ratings,
capability citations, omitted/ambiguous assessments, deictic normalization,
specific rejection codes and two-call frozen replay. The final focused run passed 26 tests, including the additional schema/reference
and legacy-policy checks. `git diff --check` passed.

Live-provider validation of these repairs is pending explicit permission for a
single synthetic canary. The earlier 32-turn report remains a pre-repair failure
measurement, not evidence of latency improvement or a successful repair.


## 2026-10-03: follow-up for `accepted_event_missing`

The previous repair did not establish live success. A subsequent synthetic
canary (`/private/tmp/cognitive-two-call-canary-fixed.json`) completed Call 1
but failed at `accepted_event_missing`; Call 2 remained unreachable.

The adapter required every known dimension's citations to intersect an accepted
semantic frame's evidence. A schema-valid model rating citing only supplied
capability/persona/state context (or no citations) consequently aborted the
whole turn. Both capability-only and empty-citation reproductions failed before
this repair.

`goal-formation-binding-v0.3` treats this specific lack of event support as an
unknown dimension: null value, insufficient basis, high uncertainty, and
`two_call_missing_accepted_event` missing-input provenance. It retains actual
supplied citations and never fabricates event or goal evidence to justify a
known rating. Valid supported ratings are unchanged. Foreign refs, invalid
scales, inconsistent known bases and high-uncertainty known ratings still fail
closed. Normal Goal, Appraisal compiler, DynamicStateUpdater, Call 2 content /
behavior compilation, and Realization validation remain authoritative. Frozen
v0.1/v0.2 traces retain their recorded policies.

Regressions verify the previously failing cases through both calls, terminal
completion, accepted shadow Realization and frozen replay. A second regression
uses serialized provider JSON through `OpenAITwoCallModel`, verifies exactly
two requests with `gpt-6-luna` / `reasoning_effort=none`, and accepts a shadow
response when all eight ratings cite only supplied context. Invalid-rating
cases still stop before Call 2. These are offline integration tests, not live
provider or latency evidence.

The synthetic canary now always saves available provider captures in its local
`.proposals.json` companion on failure, so further deterministic rejections can
be reproduced from the actual output. `--capture-proposals` additionally keeps
captures on success; no production failure payloads are expanded.

Follow-up verification: **239 passed, 37 skipped** in the relevant integration /
regression suites; `git diff --check` passed. Live-provider retest remains
unverified.


## Follow-up: schema-permitted citations rejected as `evidence_foreign`

The reported live turn completed Call 1 (15.30 s, 7,873 / 2,258 tokens) and
stopped before Call 2 at `evidence_foreign`. No raw output from that specific
turn was available in the local canary artifacts. Inspection established an
actual contract mismatch and an offline reproduction: Call 1's schema permits
all supplied semantic evidence regions, whereas the downstream Appraisal
payload contains only regions cited by retained semantic frames. A schema-valid
citation to an unused region was therefore labelled foreign by the adapter.

The `goal-formation-binding-v0.4` adapter now uses the **same supplied-reference
vocabulary as the provider schema**, via `call_one_evidence_refs`. A reference
absent from that vocabulary is still a typed `evidence_foreign` failure. The
adapter also uses the **same exact per-goal/scenario scope as the Appraisal
compiler**, via `proposal_reference_scope`; that helper extracts the existing
compiler's scope calculation without widening it or changing staged behavior.

A supplied but unbound reference reduces its dimension to unknown/null, with
insufficient basis, high uncertainty and `two_call_unbound_evidence` provenance.
Only citations actually valid for the canonical pair survive into its compiled
proposal. The original citations remain in the captured Call 1 output and its
source digest. No fabricated event links or model scores are accepted. Original
rating shape and epistemic consistency are checked before reduction, so this
path does not turn malformed scales/bases or foreign references into accepted
ratings. Existing v0.1–v0.3 frozen traces retain their prior policies.

Regressions cover schema-permitted unused regions for known, unknown, disputed,
and not-applicable dimensions; both-call completion and accepted shadow
Realization; all schema-permitted citations in one proposal; and frozen replay.
The same captured proposal under v0.3 reproduces `evidence_foreign` after exactly
one call, while v0.4 completes both calls. True-foreign and invalid-rating tests
continue to reject. Focused verification: **38 passed**. Live-provider success
has not been established by these offline checks.

Full relevant regression run after this repair: **244 passed, 37 skipped**;
`git diff --check` passed.


## Follow-up: Call 2 content rejection and oversized input

The reported bounded live follow-up reached both provider calls but failed
`content_compilation`: Call 1 10.56 s; Call 2 2.55 s with 40,641 input and 325
output tokens; no accepted shadow Realization. The original Call 2 proposal / its
nested content compiler reason was unavailable in local artifacts, so the
specific cause of that live output is not established by the generic code.
The provider timeout in the other requested turn remains a distinct failure.

Inspection confirmed a production-protocol defect: the Call 2 schema allowed
empty operations, content items and anchors that the canonical compiler rejects.
The `two-call-stage-context-v0.2` request / response projection now requires:

- exactly one content section per prepared request, using a handle-bound schema
  with that request's own source vocabulary and operation;
- nonempty patches for create/revise, no patches for presentation operations,
  and nonempty content items with bounded nonempty statement anchors;
- nonempty supplied source references in every operation, item and inquiry;
- bounded inquiry counts and explicit instructions that an inquiry cannot replace
  a ready deliverable's required contribution.

The versioned Call 2 prompt incorporates these compiler obligations. It does
not weaken the content compiler, fabricate patches, or run another repair call.
Content failures now retain their original stage/code in the terminal failure,
e.g. `content_compilation.compiler.missing_patch`, `.missing_content`, or
`.content_invalid`; malformed content remains unaccepted.

The payload audit identified unconditional catalogue expansion as the main
input-size cause. In the offline dinner fixture, a ready request previously
included 41 action definitions plus full realization manifests and construction
recipes: payload 142,167 characters; schema 13,864 characters. The new projection
includes current code-constructible candidates plus actions bindable to the
object types accepted collaboration can introduce; compact action definitions
and wording constraints replace redundant full registry rows. State, goals,
workspace, requirements, prepared content sources and the deterministic preview
are retained. Unreachable JSON-schema definitions are also removed.

For the same fixture: **12 actions; payload 61,565 characters; schema 8,891
characters**. Payload characters fell about 57%; this is an offline structural
measurement, not a measured reduction from the live 40,641-token request. The
canary now reports per-stage payload/schema/prompt character counts and its
largest input components without printing their contents.

Regressions verify compiler-specific rejection diagnostics, required output
structure, multiple request/source bindings, compact-payload content delivery,
accepted shadow Realization, and serialized provider JSON through exactly two
requests at Luna / no reasoning. Both old full and new compact projections retain
frozen replay behavior, with their respective prompt hashes. Focused checks:
**46 passed**. The pipeline remains unverified against a fresh live provider
turn until that run produces accepted shadow Realization.

Relevant full regression suite including the collaborative-content compiler:
**271 passed, 37 skipped**; `git diff --check` passed.

## Follow-up: Call 2 behavior agreement rejection

The reported live turn reached content acceptance, then failed
`call_two.behavior_plan_disagreement.proposal_rejected`: Call 1 13.70 s,
Call 2 4.03 s / 22,798 input tokens, no accepted shadow realization.
The raw proposal from that live run was not available for reproducing its exact
choice. Inspection identified a protocol mismatch independent of that sample:
Call 2 was required to equal the deterministic scorer's post-content argmax,
although Section 1.4 of the phased implementation plan specifies compiling the
model's proposed selection to actual candidates after accepting content.

New runs use `model_grounded_selection-v0.1` with planner version
`action-planner-model-grounded-v0.1`. The adapter resolves each proposed action
to exactly one constructed, hard-gate-eligible candidate at its actual planning
step. Blocker-first restrictions, canonical transition effects, primary move
selection, content compilation, and ordinary realization validation remain
mandatory. The model cannot STOP with unresolved planning conditions or omit
delivery of accepted ready content. Its original response text is validated
against the resulting plan; no reranking, replacement wording or third model
call is introduced. Deterministic scores remain recorded without claiming the
model selected their argmax. Each planning step binds its model choice to the
captured Call 2 proposal digest. Legacy staged planning and recorded strict
agreement traces retain their original policy and frozen replay behavior.

The `two-call-stage-context-v0.3` projection includes grounded, currently eligible
and unrestricted pre-content behavior examples with computed goal links, plus
local delivery bindings for future accepted content. Goal references are checked
against canonical goal targets and actual computed action-goal links. Future
artifact targets resolve through accepted content handles. Realization inventory
assembly now lives in a shared pure module, removing the orchestrator's dependency
on the persistence-oriented observation adapter.

Offline regressions prove that an eligible non-argmax acknowledgement completes
both calls, reaches `accepted_shadow`, preserves the supplied wording, and has
matching frozen replay. Ready content delivery with supplied local bindings also
reaches accepted realization. Ineligible choices, foreign references, omitted
delivery, blocker violations and detached model provenance remain rejected.

Validation: **52 focused orchestration tests passed**. The broad relevant suite
finished with **810 passed, 62 skipped, 1 failed**. The remaining failure is
`test_local_fallback_is_unchanged_by_capture_success_or_failure[True]`: its
WebSocket closes during the baseline with shadow capture disabled, before the
capture comparison. It reproduced in a narrower rerun. No live chat behavior was
changed to address that separate failure. `git diff --check` passed.

These are offline tests with captured/synthetic provider outputs. A fresh live
provider turn producing an accepted shadow response remains required to establish
live completion, latency and actual token counts for this version.

## Follow-up: tolerate cosmetic realization restrictions

The validator comments describe conservative false rejections and English lexical
limits; they are not instructions to disable semantic or grounding checks. New
two-call captures now explicitly record `style_advisory-v0.1`. Under this policy,
`exclamation_used`, `endearment_used`, and `style_marker_budget_exceeded` are
retained as sorted, typed review warnings rather than terminal violations.
All other checks remain blocking, including content/move coverage, references,
numbers/units/dates, quotations, uncertainty, unplanned acts, false execution
claims, length limits, and internal-state disclosure. A warning cannot conceal
a simultaneous blocking violation, and the contract rejects attempts to put
non-cosmetic findings into warnings.

The policy is recorded on orchestration, realization trace and validation results,
and covered by their digests. Replay checks the stored realization policy against
the captured orchestration, then revalidates using that policy. Absent fields
mean historical `strict-v0.1`; default strict fields and empty warnings are omitted
from serialization so historical traces retain their representation. Staged runs
continue to use strict validation. The summary exposes warning codes separately
from blocking validation codes; acceptance still grants no live authority and
is not a quality verdict. No additional LLM call or repair attempt is introduced.

Regressions exercise the complete adversarial mutation corpus under both policies,
including false execution claims beside tolerated style, malformed advisory
records, and two-call end-to-end acceptance/rejection plus frozen realization
replay. Relevant tests, including the content-free summary regression: **322 passed,
22 skipped**. `git diff --check` passed. Live provider behavior was not tested.

## Follow-up: sparse captured wording alignments

The reported live turn completed the Cognitive core with both calls (19.21 s
and 6.21 s; total 25.68 s), but captured Realization returned
`provider_output_invalid`. The reported response contained only one content
alignment for a three-move plan with several final bindings. The adapter required
exact coverage of every compiled binding before ordinary wording validation,
even though those binding positions are only created after Call 2 returns.
The exact raw live proposal was not available locally for replay.

New captures record `compiled_move_spans-v0.1` as their alignment policy. After
compilation, code derives candidate alignments for missing bindings from the
corresponding supplied behavior excerpt. This is metadata completion, not new
content or authorization: text remains unchanged and the ordinary Realization
validator checks move coverage/order, actual anchors and values, uncertainty,
claims and other constraints. Omitted required content now reaches a typed
validation rejection rather than being hidden by a generic conversion failure.
Explicit supplied hints still reject duplicate or foreign handles. The new
versioned Call 2 prompt instructs the model to provide ordered, nonoverlapping
move excerpts and leave content_alignments empty instead of guessing future
binding indices. No third provider call or retry is introduced.

Historical captures lacking the policy retain `complete_bindings-v0.1`, their
old prompt hashes, strict conversion and frozen replay. The new policy is
captured in the Call 2 payload and orchestration trace, and frozen replay uses
the recorded policy. Regressions verify sparse and empty alignments through
accepted dinner-content realization and replay, multiple-move conversion,
rejection of genuinely missing content, invalid explicit handles, and legacy
strict behavior. Focused orchestration tests: **62 passed**.

Live completion, the exact reported wording, and live latency remain unverified.
This repair does not reduce the large Call 1/Call 2 context payloads or establish
an improvement over the reported 25.68 seconds.

Relevant Realization, orchestration, shadow replay/contracts/summary and
collaborative-content regression suite: **442 passed, 23 skipped**.
`git diff --check` passed.
