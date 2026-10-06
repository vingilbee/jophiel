"""Authorized Realization content from dialogue memory (realization-content-v0.2).

``DialogueRealizationContentProjectorV0`` runs after Action Selection.  It
selects only the dialogue objects a selected move references -- through its
targets, content-bearing evidence, or reference parameters -- and never
exports the snapshot, the transcript, or a database object.

Mappings (every item keeps its speaker's attribution):

    attributed user proposition        -> GroundedProposition(attributed, user)
    user question                      -> GroundedQuestion
    request / topic / option / step    -> DialogueReference (anchored)
    option set / plan                  -> DialogueReference (generic)
    delivered Columbina speech         -> AuthorizedQuote or DialogueReference only;
                                          never a proposition or trusted fact
    exact short complete utterance     -> AuthorizedQuote
    correction                         -> corrected side (non-assertable) + replacement side
    unresolved focus cue               -> DialogueReference(unresolved), or a
                                          KnowledgeGap when a prior-statement
                                          lookup found no record

Partial output is never quoted (it cannot stand in for a full answer); it is
only referenced, with its incompleteness recorded in its provenance.  Shadow
Realization candidates never appear here: they are not chat messages, so no
dialogue item can cite one.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts.actions import ActionSelectionResult, QuestionForm
from ..contracts.dialogue_memory import (
    DialogueCommitmentReference,
    DialogueCorrection,
    DialogueCue,
    DialogueCueKind,
    DialogueMemoryFailureCode,
    DialogueMemoryFailurePhase,
    DialogueOptionSet,
    DialoguePlanReference,
    DialogueProposition,
    DialogueQuestion,
    DialogueRequest,
    DialogueResolution,
    DialogueSpeaker,
    DialogueTopic,
    DialogueTurnContext,
    DialogueUtteranceReference,
)
from ..contracts.realization import (
    REALIZATION_CONTENT_VERSION,
    AuthorizedQuote,
    ContentAttribution,
    ContentEpistemicStatus,
    ContentSourceKind,
    DialogueReference,
    DialogueReferenceKind,
    GapKind,
    GroundedProposition,
    GroundedQuestion,
    KnowledgeGap,
    PropositionTerm,
    RealizationContentInventory,
    ReferenceForm,
    SensitivityClass,
    realization_uuid,
)
from ..realization_v0.content import move_references, project_dialogue_structure
from .errors import fail
from .projector import GROUNDED_PREFIX, cue_ref

DIALOGUE_MEMORY_CONTENT_PRODUCER = "dialogue-working-memory-content-projection-v0.1"
USER_STATEMENT_PREDICATE = "dialogue.user_statement"
USER_NAMING_PREDICATE = "dialogue.user_named"

_QUESTION_FORMS = {"yes_no": QuestionForm.YES_NO, "choice": QuestionForm.OPEN_SET, "open": QuestionForm.OPEN_SET}


class DialogueRealizationContentProjectorV0:
    version = DIALOGUE_MEMORY_CONTENT_PRODUCER

    def project(self, action_result: ActionSelectionResult, context: DialogueTurnContext) -> RealizationContentInventory:
        """Dialogue content for exactly the refs the selected plan binds, merged with dialogue structure."""

        if action_result.cognitive_turn_id != context.cognitive_turn_id:
            raise fail(DialogueMemoryFailurePhase.REALIZATION_CONTENT_PROJECTION,
                       DialogueMemoryFailureCode.SNAPSHOT_LINEAGE_MISMATCH,
                       "The dialogue context belongs to a different cognitive turn.")
        structure = project_dialogue_structure(action_result)
        builder = _Builder(action_result, context)
        for move in action_result.plan.moves:
            for _, ref in move_references(move):
                if ref.startswith(GROUNDED_PREFIX):
                    builder.add(ref)
        items = {item.content_id: item for item in structure.items}
        for item in builder.items.values():
            items.setdefault(item.content_id, item)
        producers = tuple(sorted({*structure.producer_refs, DIALOGUE_MEMORY_CONTENT_PRODUCER}))
        return RealizationContentInventory(
            inventory_id=realization_uuid(REALIZATION_CONTENT_VERSION, "inventory", *producers, action_result.result_id,
                                          context.context_id),
            cognitive_turn_id=action_result.cognitive_turn_id, producer_refs=producers,
            items=tuple(items[key] for key in sorted(items)),
        )


class _Builder:
    def __init__(self, action_result: ActionSelectionResult, context: DialogueTurnContext) -> None:
        self.turn = action_result.cognitive_turn_id
        self.context = context
        self.snapshot = context.prior_context_projection
        self.by_id = {item.item_id: item for item in self.snapshot.items}
        self.cues = {cue_ref(context, cue): cue for cue in context.current_interpretation_projection.cues}
        self.items: dict[str, object] = {}

    # -- provenance ------------------------------------------------------
    def provenance(self, grounded: str, item_ref: str, messages: Iterable[str], *extra: str) -> dict:
        return dict(
            cognitive_turn_id=self.turn,
            source_refs=tuple(dict.fromkeys((
                f"dialogue-snapshot:{self.snapshot.snapshot_id}",
                f"dialogue-snapshot-digest:{self.snapshot.semantic_digest}",
                f"dialogue-item:{item_ref}",
                *(f"chat-message:{message}" for message in messages),
                *extra,
            ))),
            represents_refs=(grounded,),
            authorized_for_realization=True,
            revision_ref=f"dialogue-snapshot:{self.snapshot.snapshot_id}",
        )

    def content_id(self, grounded: str, part: str = "") -> str:
        return "dialogue-memory:" + realization_uuid(REALIZATION_CONTENT_VERSION, DIALOGUE_MEMORY_CONTENT_PRODUCER,
                                                     self.turn, self.context.context_id, grounded, part)

    @staticmethod
    def _speaker(speaker: DialogueSpeaker) -> tuple[ContentSourceKind, ContentAttribution]:
        if speaker == DialogueSpeaker.USER:
            return ContentSourceKind.DELIVERED_USER_UTTERANCE, ContentAttribution.USER
        return ContentSourceKind.DELIVERED_ASSISTANT_UTTERANCE, ContentAttribution.COLUMBINA

    def put(self, item) -> None:
        self.items.setdefault(item.content_id, item)

    def reference(self, grounded: str, item, kind: DialogueReferenceKind, anchor: str | None, *extra: str) -> None:
        source, attribution = self._speaker(item.speaker)
        self.put(DialogueReference(
            content_id=self.content_id(grounded), source_kind=source, attribution=attribution,
            epistemic_status=ContentEpistemicStatus.ATTRIBUTED, sensitivity_class=item.sensitivity_class,
            reference_kind=kind, target_ref=grounded,
            reference_form=ReferenceForm.ANCHORED if anchor else ReferenceForm.GENERIC,
            surface_anchors=(anchor,) if anchor else (),
            **self.provenance(grounded, item.item_id, item.source_message_refs, *extra),
        ))

    # -- dispatch ----------------------------------------------------------
    def add(self, grounded: str) -> None:
        ref = grounded[len(GROUNDED_PREFIX):]
        if ref in self.cues:
            self.cue(grounded, self.cues[ref])
            return
        parent_id, _, sub = ref.partition("#")
        item = self.by_id.get(parent_id)
        if item is None or not item.is_current:
            # A referenced object that is not current in the committed
            # snapshot has no authorized content; Realization then reports
            # skipped_missing_content instead of inventing it.
            return
        if sub:
            self.sub(grounded, item, ref)
        elif isinstance(item, DialogueProposition):
            self.proposition(grounded, item)
        elif isinstance(item, DialogueQuestion):
            self.question(grounded, item)
        elif isinstance(item, DialogueRequest):
            self.reference(grounded, item, DialogueReferenceKind.REQUEST, item.surface_anchors[0])
        elif isinstance(item, DialogueTopic):
            self.reference(grounded, item, DialogueReferenceKind.TOPIC, item.surface_anchors[0])
        elif isinstance(item, DialogueOptionSet):
            self.reference(grounded, item, DialogueReferenceKind.PRIOR_MOVE, None)
        elif isinstance(item, DialoguePlanReference):
            self.reference(grounded, item, DialogueReferenceKind.PLAN, None)
        elif isinstance(item, DialogueCommitmentReference):
            self.reference(grounded, item, DialogueReferenceKind.PRIOR_MOVE, item.surface_anchors[0])
        elif isinstance(item, DialogueUtteranceReference):
            self.utterance(grounded, item)
        elif isinstance(item, DialogueCorrection):
            self.correction(grounded, item)

    def sub(self, grounded: str, item, ref: str) -> None:
        if isinstance(item, DialogueOptionSet):
            option = next((option for option in item.options if option.option_ref == ref), None)
            if option is not None:
                self.reference(grounded, item, DialogueReferenceKind.OPTION, option.surface_anchor)
        elif isinstance(item, DialoguePlanReference):
            step = next((step for step in item.steps if step.step_ref == ref), None)
            if step is not None:
                self.reference(grounded, item, DialogueReferenceKind.PLAN_STEP, step.surface_anchor)

    def proposition(self, grounded: str, item: DialogueProposition) -> None:
        # An attributed user statement: never grounded fact, never Columbina's claim.
        self.put(GroundedProposition(
            content_id=self.content_id(grounded), source_kind=ContentSourceKind.DELIVERED_USER_UTTERANCE,
            attribution=ContentAttribution.USER, epistemic_status=ContentEpistemicStatus.ATTRIBUTED,
            sensitivity_class=item.sensitivity_class,
            subject=PropositionTerm(ref=item.item_id, surface_anchor=item.surface_anchors[0]),
            predicate=USER_STATEMENT_PREDICATE,
            **self.provenance(grounded, item.item_id, item.source_message_refs),
        ))

    def question(self, grounded: str, item: DialogueQuestion) -> None:
        if item.speaker != DialogueSpeaker.USER:
            # Columbina's earlier question is referenced, never re-asserted as content.
            self.reference(grounded, item, DialogueReferenceKind.PRIOR_MOVE, item.surface_anchors[0])
            return
        self.put(GroundedQuestion(
            content_id=self.content_id(grounded), source_kind=ContentSourceKind.DELIVERED_USER_UTTERANCE,
            attribution=ContentAttribution.USER, epistemic_status=ContentEpistemicStatus.UNRESOLVED,
            sensitivity_class=item.sensitivity_class, question_form=_QUESTION_FORMS[item.question_shape],
            topic_surface_anchor=item.surface_anchors[0],
            **self.provenance(grounded, item.item_id, item.source_message_refs),
        ))

    def utterance(self, grounded: str, item: DialogueUtteranceReference) -> None:
        source, attribution = self._speaker(item.speaker)
        completeness = f"dialogue-completeness:{item.completeness}"
        if item.quote is not None and item.completeness == "complete":
            self.put(AuthorizedQuote(
                content_id=self.content_id(grounded), source_kind=source, attribution=attribution,
                epistemic_status=ContentEpistemicStatus.ATTRIBUTED, sensitivity_class=item.sensitivity_class,
                quote_text=item.quote.text, source_message_ref=f"chat-message:{item.message_ref}",
                span_start=item.quote.span.start, span_end=item.quote.span.end,
                **self.provenance(grounded, item.item_id, item.source_message_refs, completeness),
            ))
            return
        kind = DialogueReferenceKind.PRIOR_MOVE
        self.reference(grounded, item, kind, None, completeness)

    def correction(self, grounded: str, item: DialogueCorrection) -> None:
        # Both sides, with the corrected side non-assertable: the validator
        # then rejects any wording that reasserts it.
        for part, anchor, status in (("corrected", item.old_anchor, ContentEpistemicStatus.CORRECTED),
                                     ("replacement", item.new_anchor, ContentEpistemicStatus.ATTRIBUTED)):
            self.put(GroundedProposition(
                content_id=self.content_id(grounded, part), source_kind=ContentSourceKind.DELIVERED_USER_UTTERANCE,
                attribution=ContentAttribution.USER, epistemic_status=status,
                sensitivity_class=item.sensitivity_class,
                subject=PropositionTerm(ref=f"{item.item_id}#{part}", surface_anchor=anchor),
                predicate=USER_NAMING_PREDICATE,
                **self.provenance(grounded, item.item_id, item.source_message_refs, f"dialogue-correction-side:{part}"),
            ))

    def cue(self, grounded: str, cue: DialogueCue) -> None:
        focus = self.context.focus_message_ref
        lookup = cue.cue_kind in {DialogueCueKind.PRIOR_USER_STATEMENT_QUERY,
                                  DialogueCueKind.PRIOR_ASSISTANT_STATEMENT_QUERY}
        provenance = dict(
            cognitive_turn_id=self.turn,
            source_refs=(f"dialogue-snapshot:{self.snapshot.snapshot_id}",
                         f"dialogue-snapshot-digest:{self.snapshot.semantic_digest}",
                         f"dialogue-cue:{cue.cue_kind.value}", f"chat-message:{focus}"),
            represents_refs=(grounded,), authorized_for_realization=True,
            revision_ref=f"dialogue-snapshot:{self.snapshot.snapshot_id}",
        )
        if lookup and cue.resolution == DialogueResolution.NO_CANDIDATE:
            # An actual failed lookup: "I don't have a record of that", never
            # "you never said anything".
            self.put(KnowledgeGap(
                content_id=self.content_id(grounded), source_kind=ContentSourceKind.RETRIEVAL_NO_RESULT,
                attribution=ContentAttribution.RUNTIME, epistemic_status=ContentEpistemicStatus.UNKNOWN,
                sensitivity_class=SensitivityClass.PERSONAL, topic_ref=grounded, surface_anchor=cue.surface_anchor,
                gap_kind=GapKind.NO_RECORD, **provenance,
            ))
            return
        self.put(DialogueReference(
            content_id=self.content_id(grounded), source_kind=ContentSourceKind.CURRENT_USER_STATEMENT,
            attribution=ContentAttribution.USER, epistemic_status=ContentEpistemicStatus.UNRESOLVED,
            sensitivity_class=SensitivityClass.PERSONAL, reference_kind=DialogueReferenceKind.UNRESOLVED_ELEMENT,
            target_ref=grounded, reference_form=ReferenceForm.GENERIC, **provenance,
        ))


__all__ = [
    "DIALOGUE_MEMORY_CONTENT_PRODUCER", "DialogueRealizationContentProjectorV0", "USER_NAMING_PREDICATE",
    "USER_STATEMENT_PREDICATE",
]
