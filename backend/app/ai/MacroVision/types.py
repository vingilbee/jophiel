from typing import Any, TypedDict


class NutritionHints(TypedDict, total=False):
    """Known facts about a meal that the orchestrator can supply.

    All fields optional. ``known_weight_g`` affects portion math directly;
    ``known_ingredients`` and ``extra_notes`` are passed to the VLM as
    context only.
    """
    known_weight_g: float
    known_ingredients: list[dict[str, Any]]
    extra_notes: str