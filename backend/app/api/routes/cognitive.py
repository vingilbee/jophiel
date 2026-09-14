"""Developer-facing inspection endpoint for local cognitive input processing."""

from functools import lru_cache
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from openai import OpenAIError

from app.ai.Cognitive.system import CognitiveInputPipeline, PerceptionSignal
from app.ai.Cognitive.system.llm import predict_state
from app.api.deps import get_current_user, validate_origin
from app.db.models import User
from app.schemas.cognitive import (
    CognitiveInspectMetrics,
    CognitiveInspectRequest,
    CognitiveInspectResponse,
    CognitiveLLMInspectResponse,
)

router = APIRouter(prefix="/cognitive", tags=["cognitive"])


@lru_cache
def _pipeline() -> CognitiveInputPipeline:
    # Loading remains lazy so importing the API never trains or loads a model.
    return CognitiveInputPipeline()


@router.post("/inspect", response_model=CognitiveInspectResponse | CognitiveLLMInspectResponse, dependencies=[Depends(validate_origin)])
def inspect_cognition(
    request: CognitiveInspectRequest,
    _current_user: User = Depends(get_current_user),
) -> CognitiveInspectResponse | CognitiveLLMInspectResponse:
    if request.mode == "llm":
        started = perf_counter()
        try:
            state = predict_state(
                request.message, request.recent_messages,
                image_count=request.image_count, asset_count=request.asset_count,
                available_capabilities=request.available_capabilities,
            )
        except (OpenAIError, ValueError) as exc:
            raise HTTPException(502, "Cognitive prediction failed; no action was executed. Try again.") from exc
        return CognitiveLLMInspectResponse(
            state=state, image_count=request.image_count, asset_count=request.asset_count,
            metrics=CognitiveInspectMetrics(
                processing_ms=round((perf_counter() - started) * 1000, 4),
                character_count=len(request.message), word_count=len(request.message.split()),
                recent_message_count=len(request.recent_messages), online_model_calls=1,
            ),
        )
    signals = {
        "has_image": PerceptionSignal(
            value=request.image_count > 0,
            confidence=1.0,
            source="runtime_context",
        ),
        "image_count": PerceptionSignal(
            value=request.image_count,
            confidence=1.0,
            source="runtime_context",
        ),
        "has_asset": PerceptionSignal(
            value=request.asset_count > 0,
            confidence=1.0,
            source="runtime_context",
        ),
        "asset_count": PerceptionSignal(
            value=request.asset_count,
            confidence=1.0,
            source="runtime_context",
        ),
    }
    started = perf_counter()
    result = _pipeline().process(
        request.message,
        request.recent_messages,
        context_signals=signals,
    )
    processing_ms = (perf_counter() - started) * 1000
    word_count = result.perception.signals["word_count"].value
    return CognitiveInspectResponse(
        perception=result.perception,
        interpretation=result.interpretation,
        metrics=CognitiveInspectMetrics(
            processing_ms=round(processing_ms, 4),
            character_count=len(request.message),
            word_count=word_count if isinstance(word_count, int) else 0,
            recent_message_count=len(request.recent_messages),
        ),
    )
