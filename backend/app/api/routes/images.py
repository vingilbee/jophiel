"""Image routes: upload a photo, list them, fetch one back.

    POST   /api/images            ?kind= (multipart: file)     -> ImageOut
    GET    /api/images            ?limit=&kind=                -> [ImageOut]
    GET    /api/images/{id}/raw                                -> the bytes
    DELETE /api/images/{id}

Uploading is what lets the chat agent work with a photo: the client stores the
image once, then sends only its ``image_id`` with the message. The agent hands
that id to the food-vision specialist, which runs the model on it.

Everything is scoped to the authenticated user by the store's own directory
layout, so an id belonging to someone else is indistinguishable from one that
doesn't exist — both are 404.
"""
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from app.core.limiter import limiter
from fastapi.responses import Response

from app.api.deps import get_current_user, validate_origin
from app.core.config import get_settings
from app.db.models import User
from app.schemas.image import ImageOut
from app.services import image_store

router = APIRouter(prefix="/images", tags=["images"])


@router.post("", response_model=ImageOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(validate_origin)])
@limiter.limit("30/minute")
async def upload_image(
    request: Request,
    file: UploadFile = File(..., description="Image to store"),
    kind: Literal["upload", "screen"] = Query(
        default="upload",
        description=(
            '"upload" for a photo the user is keeping; "screen" for an '
            "ephemeral frame of their shared screen, which is pruned "
            "aggressively and hidden from the photo listing."
        ),
    ),
    current_user: User = Depends(get_current_user),
) -> ImageOut:
    settings = get_settings()
    if kind == "screen" and not settings.vision_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Vision is disabled."
        )

    data = await file.read()
    try:
        record = image_store.save_bytes(
            current_user.id,
            data,
            content_type=file.content_type,
            filename=file.filename,
            kind=kind,
        )
    except image_store.InvalidImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    if kind == "screen":
        # Prune on write rather than on a sweeper: frames only arrive while
        # someone is actually sharing, so the one moment we know retention
        # needs enforcing is the moment a new frame lands. Protects the frame
        # just written, which is by definition the live one.
        image_store.prune(
            current_user.id,
            kind="screen",
            keep=settings.screen_frame_keep,
            max_age_seconds=settings.screen_frame_ttl_minutes * 60,
            protect=record.image_id,
        )
    return ImageOut(**record.to_dict())


@router.get("", response_model=list[ImageOut])
def list_images(
    limit: int = Query(default=10, ge=1),
    kind: str | None = Query(
        default="upload",
        description='Filter by kind ("upload" or "depth"). Omit for all.',
    ),
    current_user: User = Depends(get_current_user),
) -> list[ImageOut]:
    limit = min(limit, get_settings().image_list_max)
    records = image_store.list_images(current_user.id, limit=limit, kind=kind)
    return [ImageOut(**r.to_dict()) for r in records]


@router.get("/{image_id}/raw")
def get_image_bytes(
    image_id: str,
    current_user: User = Depends(get_current_user),
) -> Response:
    """Serve the image itself, for rendering in the chat transcript."""
    try:
        data, media_type = image_store.read_bytes(current_user.id, image_id)
    except image_store.ImageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        ) from exc
    return Response(
        content=data,
        media_type=media_type,
        # Ids are random and never reused, so the bytes behind one can't change.
        # `private` keeps it out of any shared cache — these are user photos.
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(validate_origin)])
def delete_image(
    image_id: str,
    current_user: User = Depends(get_current_user),
) -> None:
    if not image_store.delete_image(current_user.id, image_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )
