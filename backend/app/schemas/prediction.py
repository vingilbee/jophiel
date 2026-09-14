"""Pydantic response schema for image → macronutrient predictions."""
from pydantic import BaseModel, ConfigDict, Field


class PredictionOut(BaseModel):
    """Predicted macros for an uploaded food photo.

    Mirrors ``app.ai.MacroVision.NutritionPrediction``. ``dish`` and
    ``portion_scale`` are only populated by the "combined" (VLM) mode.
    """

    calories: float = Field(description="Estimated calories (kcal)")
    fat: float = Field(description="Estimated fat (g)")
    carbs: float = Field(description="Estimated carbohydrates (g)")
    protein: float = Field(description="Estimated protein (g)")
    dish: str | None = Field(default=None, description="VLM-identified dish name")
    portion_scale: float | None = Field(
        default=None, description="Portion-size correction applied to the VLM estimate"
    )
    source: str = Field(description='Which model path produced this: "combined" or "regression"')
    image_id: str | None = Field(
        default=None,
        description=(
            "Id of the stored photo, when it was kept. Lets the chat agent "
            "reason about the same image afterwards."
        ),
    )

    model_config = ConfigDict(allow_inf_nan=False)
