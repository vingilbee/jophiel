from app.api.routes.cognitive import inspect_cognition
from app.schemas.cognitive import CognitiveInspectRequest


def test_cognitive_inspector_exposes_context_and_runtime_metrics():
    result = inspect_cognition(
        CognitiveInspectRequest(
            message="Can you analyze this image?",
            recent_messages=["Hello"],
            image_count=1,
        ),
        _current_user=object(),  # Authentication is exercised by FastAPI.
    )

    assert result.perception.is_question is True
    assert result.perception.is_request is True
    assert result.perception.signals["has_image"].value is True
    assert result.perception.signals["image_count"].value == 1
    assert result.interpretation.intent.value == "request"
    assert result.metrics.online_model_calls == 0
    assert result.metrics.recent_message_count == 1
    assert result.metrics.processing_ms >= 0
