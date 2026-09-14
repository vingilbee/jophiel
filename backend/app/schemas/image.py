"""Pydantic schemas for the image store."""
from pydantic import BaseModel


class ImageOut(BaseModel):
    """A stored image. ``image_id`` is what the chat agent and tools refer to."""

    image_id: str
    media_type: str
    width: int
    height: int
    size_bytes: int
    created_at: str
    # "upload" for a user's photo, "depth" for a depth map derived from one.
    kind: str = "upload"
    filename: str | None = None
    derived_from: str | None = None
