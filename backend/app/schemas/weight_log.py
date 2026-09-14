"""Pydantic request/response schemas for weight logs."""
import uuid
from datetime import date as date_type

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WeightLogCreate(BaseModel):
    weight: float = Field(gt=0, le=1000, description="Body weight in kilograms")
    date: date_type

    model_config = ConfigDict(allow_inf_nan=False)


class WeightLogUpdate(BaseModel):
    """Partial update — only provided fields are changed."""

    weight: float | None = Field(default=None, gt=0, le=1000)
    date: date_type | None = None

    model_config = ConfigDict(allow_inf_nan=False)

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> "WeightLogUpdate":
        if not self.model_fields_set or any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in ("weight", "date")
        ):
            raise ValueError("At least one field must be provided")
        return self


class WeightLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    weight: float
    date: date_type
