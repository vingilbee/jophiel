"""Upload and retrieve user-owned Blender assets."""
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from app.core.limiter import limiter
from fastapi.responses import FileResponse

from app.api.deps import get_current_user, validate_origin
from app.core.config import get_settings
from app.db.models import User
from app.schemas.asset import AssetOut
from app.services import asset_store, slide_images

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/slide-image/{token}", include_in_schema=False)
def get_slide_image(token: str) -> FileResponse:
    try:
        path = slide_images.resolve_link(token)
    except asset_store.AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Image link is invalid or expired") from exc
    return FileResponse(path, media_type="image/png", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(validate_origin)])
@limiter.limit("10/minute")
async def upload_asset(
    request: Request, file: UploadFile = File(...), current_user: User = Depends(get_current_user)
) -> AssetOut:

    try:
        record = asset_store.save_bytes(
            current_user.id, await file.read(), filename=file.filename
        )
    except asset_store.InvalidAssetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AssetOut(**record.to_dict())


@router.get("", response_model=list[AssetOut])
def list_assets(current_user: User = Depends(get_current_user)) -> list[AssetOut]:
    return [
        AssetOut(**record.to_dict())
        for record in asset_store.list_assets(
            current_user.id, limit=get_settings().asset_list_max
        )
    ]


@router.get("/{asset_id}/raw")
def get_asset(asset_id: str, preview: bool = False, current_user: User = Depends(get_current_user)) -> FileResponse:
    try:
        record = asset_store.get_record(current_user.id, asset_id)
        path = asset_store.get_path(current_user.id, asset_id)
    except asset_store.AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Asset not found") from exc
    return FileResponse(
        path=path,
        media_type=record.media_type,
        filename=record.filename,
        content_disposition_type="inline" if preview and record.media_type == "application/pdf" else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.get("/{asset_id}", response_model=AssetOut)
def get_asset_metadata(asset_id: str, current_user: User = Depends(get_current_user)) -> AssetOut:
    try:
        record = asset_store.get_record(current_user.id, asset_id)
    except asset_store.AssetNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Asset not found") from exc
    return AssetOut(**record.to_dict())
