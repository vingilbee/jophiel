"""Pure, closed-world content inventory for realization compilation."""

from ..contracts.dialogue_memory import DialogueTurnContext
from ..contracts.realization import (
    REALIZATION_CONTENT_VERSION, RealizationContentInventory, RealizationFailurePhase, realization_uuid,
)
from ..contracts.collaboration import COLLABORATION_CONTENT_PRODUCER
from ..contracts.requirement_resolution import REQUIREMENT_RESOLUTION_CONTENT_PRODUCER
from ..dialogue_memory_v0.content_projector import DialogueRealizationContentProjectorV0
from ..dialogue_memory_v0.errors import DialogueMemoryError
from .content import project_dialogue_structure
from .errors import RealizationExecutionError


def realization_inventory(action, dialogue_context: DialogueTurnContext | None, requirement_content=(),
                          collaboration_content=(), understanding_content=()):
    """Dialogue structure alone (capture v0.2), or merged with selected dialogue memory (v0.3).

    ``requirement_content`` is the closed-world content requirement resolution
    projected for this turn (trusted answers, explicit gaps, requests, and
    grounded decline reasons).  When there is none the inventory is exactly
    the historical one, so earlier traces replay byte for byte.

    Raises ``RealizationExecutionError`` (content resolution) when the dialogue
    projection fails: nothing is invented to fill the gap.
    """

    if dialogue_context is None:
        base = project_dialogue_structure(action)
    else:
        try:
            base = DialogueRealizationContentProjectorV0().project(action, dialogue_context)
        except DialogueMemoryError as exc:
            raise RealizationExecutionError(
                RealizationFailurePhase.CONTENT_RESOLUTION, f"dialogue_memory.{exc.code.value}",
                "The dialogue content inventory could not be projected.",
            ) from exc
    if not requirement_content and not collaboration_content and not understanding_content:
        return base
    items = {item.content_id: item for item in base.items}
    added = set()
    from ..two_call.v1_grounding import UNDERSTANDING_CONTENT_PRODUCER
    for producer, content in ((REQUIREMENT_RESOLUTION_CONTENT_PRODUCER, requirement_content),
                              (COLLABORATION_CONTENT_PRODUCER, collaboration_content),
                              (UNDERSTANDING_CONTENT_PRODUCER, understanding_content)):
        for item in content:
            if item.content_id in items and items[item.content_id] != item:
                raise RealizationExecutionError(
                    RealizationFailurePhase.CONTENT_RESOLUTION, "requirement_content_collision",
                    "Projected stage content collides with other content.",
                )
            items.setdefault(item.content_id, item)
            added.add(producer)
    producers = tuple(sorted({*base.producer_refs, *added}))
    return RealizationContentInventory(
        inventory_id=realization_uuid(REALIZATION_CONTENT_VERSION, "inventory", *producers, base.inventory_id),
        cognitive_turn_id=base.cognitive_turn_id, producer_refs=producers,
        items=tuple(items[key] for key in sorted(items)),
    )

