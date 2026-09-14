"""Request and response contracts for the cognitive-system inspector."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.ai.Cognitive.system.schemas import Interpretation, Perception
from app.ai.Cognitive.system.llm import CognitiveState


class CognitiveInspectRequest(BaseModel):
    mode: Literal["rules", "llm"] = "rules"
    message: str = Field(min_length=1, max_length=8000)
    recent_messages: list[Annotated[str, Field(max_length=8000)]] = Field(default_factory=list, max_length=24)
    available_capabilities: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=32)
    image_count: int = Field(default=0, ge=0, le=8)
    asset_count: int = Field(default=0, ge=0, le=4)


class CognitiveInspectMetrics(BaseModel):
    processing_ms: float
    character_count: int
    word_count: int
    recent_message_count: int
    online_model_calls: int = 0


class CognitiveInspectResponse(BaseModel):
    perception: Perception
    interpretation: Interpretation
    metrics: CognitiveInspectMetrics


class CognitiveLLMInspectResponse(BaseModel):
    mode: Literal["llm"] = "llm"
    state: CognitiveState
    image_count: int
    asset_count: int
    metrics: CognitiveInspectMetrics
