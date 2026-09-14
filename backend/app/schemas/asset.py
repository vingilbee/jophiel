from pydantic import BaseModel


class AssetOut(BaseModel):
    asset_id: str
    filename: str
    media_type: str
    size_bytes: int
    created_at: str
    kind: str
    derived_from: str | None = None
