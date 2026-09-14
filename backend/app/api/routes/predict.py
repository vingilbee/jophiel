"""Prediction route: upload a food photo, get predicted macronutrients.

    POST /api/predict   (multipart/form-data)
        file : the food image
        mode : "combined" (default) | "regression"  (query param)

Scoped to the authenticated user (session cookie), like the rest of the API.
The handler stays thin — validation + inference live in the service layer.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from app.ai.MacroVision import PredictionMode
from app.api.deps import get_current_user, validate_origin
from app.db.models import User
from app.schemas.prediction import PredictionOut
from app.services.prediction import InvalidImageError, predict_from_image

router = APIRouter(prefix="/predict", tags=["predict"])


@router.post("", response_model=PredictionOut, dependencies=[Depends(validate_origin)])
async def predict_macros(
    file: UploadFile = File(..., description="Food image to analyze"),
    mode: PredictionMode = Query(
        default="combined",
        description='"combined" uses the VLM + regressor; "regression" is faster, model-only.',
    ),
    current_user: User = Depends(get_current_user),
) -> PredictionOut:
    data = await file.read()
    try:
        return await predict_from_image(
            data=data,
            content_type=file.content_type,
            mode=mode,
            # Keep the photo so the chat agent can be asked about it later.
            user_id=current_user.id,
            filename=file.filename,
        )
    except InvalidImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
