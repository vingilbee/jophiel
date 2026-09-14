"""Pydantic request/response schemas for calorie logs."""
import uuid
from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CalorieLogCreate(BaseModel):
    calories: float = Field(ge=0, description="Total calories (kcal)")
    fat: float = Field(ge=0, description="Fat in grams")
    carbs: float = Field(ge=0, description="Carbohydrates in grams")
    protein: float = Field(ge=0, description="Protein in grams")
    date: date_type

    model_config = ConfigDict(allow_inf_nan=False)


class CalorieLogUpdate(BaseModel):
    """Partial update — only provided fields are changed."""

    calories: float | None = Field(default=None, ge=0)
    fat: float | None = Field(default=None, ge=0)
    carbs: float | None = Field(default=None, ge=0)
    protein: float | None = Field(default=None, ge=0)
    date: date_type | None = None

    model_config = ConfigDict(allow_inf_nan=False)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "CalorieLogUpdate":
        if not self.model_fields_set or any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in ("calories", "fat", "carbs", "protein", "date")
        ):
            raise ValueError("At least one field must be provided")
        return self


class CalorieLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    calories: float
    fat: float
    carbs: float
    protein: float
    date: date_type
