"""Her hands' own copies of the phone's picture, drawn on in the backend (Pillow): where a tap by point landed, and a
zoom on part of the picture. The phone's screen never shows either.

- **Where it landed** (``mark``): after a tap, swipe or gesture by point, the picture after it carries a ring on
  each point (numbered when there were several) and a line for each finger's path, so a miss reads as "a little to
  the right of the button" instead of a screen where nothing happened.
- **A zoom** (``zoom``): a square of the latest picture around a point, enlarged, with a fine grid whose labels are
  the full picture's pixels, so a point read off it goes straight into ``phone_tap_point``. It enlarges the picture
  the phone sent (short edge 720); it adds no detail the picture didn't have, it puts more of her model's view on
  the part that matters and the finer lines close to it.

A picture that can't be read (a test's fake bytes, a broken JPEG) is passed on unchanged (``mark``) or refused
(``zoom`` returns None).
"""
from __future__ import annotations

import base64
import binascii
import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

log = logging.getLogger(__name__)

JPEG_QUALITY = 85
RING = (255, 0, 200)  # magenta: rare in games and apps alike
EDGE = (0, 0, 0)
ZOOM_SIDE = 720  # a zoom's picture, in its own pixels
ZOOM_MIN, ZOOM_MAX, ZOOM_DEFAULT = 60, 480, 200  # the square taken from the picture, in its pixels
_STEPS = (5, 10, 20, 25, 50, 100)

Point = tuple[int, int]
Path = tuple[int, int, int, int]


def _open(jpeg_b64: str) -> Image.Image | None:
    try:
        image = Image.open(io.BytesIO(base64.b64decode(jpeg_b64, validate=True)))
        image.load()
        return image.convert("RGB")
    except (binascii.Error, UnidentifiedImageError, OSError, ValueError):
        return None


def _jpeg(image: Image.Image) -> str:
    out = io.BytesIO()
    image.save(out, "JPEG", quality=JPEG_QUALITY)
    return base64.b64encode(out.getvalue()).decode()


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an older Pillow: one fixed size
        return ImageFont.load_default()


def _ring(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, label: str | None, font) -> None:
    draw.ellipse((x - r - 1, y - r - 1, x + r + 1, y + r + 1), outline=EDGE, width=5)
    draw.ellipse((x - r, y - r, x + r, y + r), outline=RING, width=3)
    draw.line((x - 4, y, x + 4, y), fill=RING, width=2)
    draw.line((x, y - 4, x, y + 4), fill=RING, width=2)
    if label:
        box = draw.textbbox((x + r + 2, y - r - 2), label, font=font)
        draw.rectangle((box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1), fill=RING)
        draw.text((x + r + 2, y - r - 2), label, fill=(255, 255, 255), font=font)


def mark(jpeg_b64: str, *, points: list[Point] = (), paths: list[Path] = (),
         size: tuple[int, int] | None = None) -> str:
    """The picture with where her fingers went drawn on. ``size``: the picture the points were read off; a new
    picture of another size (the phone turned) is left unmarked, since the points no longer mean the same place."""
    if not points and not paths:
        return jpeg_b64
    image = _open(jpeg_b64)
    if image is None or (size is not None and image.size != tuple(size)):
        return jpeg_b64
    draw = ImageDraw.Draw(image)
    font = _font(16)
    for x1, y1, x2, y2 in paths:
        draw.line((x1, y1, x2, y2), fill=EDGE, width=6)
        draw.line((x1, y1, x2, y2), fill=RING, width=3)
        _ring(draw, x1, y1, 9, None, font)
        draw.ellipse((x2 - 6, y2 - 6, x2 + 6, y2 + 6), fill=RING, outline=EDGE, width=2)
    many = len(points) > 1
    for i, (x, y) in enumerate(points, start=1):
        _ring(draw, x, y, 14, str(i) if many else None, font)
    return _jpeg(image)


@dataclass(frozen=True)
class Zoom:
    jpeg_b64: str
    left: int
    top: int
    right: int  # exclusive, in the full picture's pixels
    bottom: int
    factor: float
    step: int  # the grid: a line every this many of the full picture's pixels


def _step(side: int) -> int:
    """A grid step giving about ten lines across the square."""
    return min(_STEPS, key=lambda s: abs(side / s - 10))


def zoom(jpeg_b64: str, x: int, y: int, side: int = ZOOM_DEFAULT) -> Zoom | None:
    """A square of the picture around x,y (moved inside it at the edges), enlarged to ``ZOOM_SIDE`` with a grid
    labelled in the full picture's pixels. None when the picture can't be read or the point is off it."""
    image = _open(jpeg_b64)
    if image is None:
        return None
    width, height = image.size
    if not (0 <= x < width and 0 <= y < height):
        return None
    side = max(ZOOM_MIN, min(int(side), ZOOM_MAX, width, height))
    left = max(0, min(x - side // 2, width - side))
    top = max(0, min(y - side // 2, height - side))
    crop = image.crop((left, top, left + side, top + side))
    factor = ZOOM_SIDE / side
    big = crop.resize((ZOOM_SIDE, ZOOM_SIDE), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(big, "RGBA")
    font = _font(15)
    step = _step(side)

    def at(value: int, origin: int) -> float:
        return (value - origin) * factor

    xs = [v for v in range((left // step + 1) * step, left + side, step)]
    ys = [v for v in range((top // step + 1) * step, top + side, step)]
    for v in xs:
        p = at(v, left)
        draw.line((p + 1, 0, p + 1, ZOOM_SIDE), fill=(0, 0, 0, 90), width=1)
        draw.line((p, 0, p, ZOOM_SIDE), fill=(255, 255, 255, 150), width=1)
    for v in ys:
        p = at(v, top)
        draw.line((0, p + 1, ZOOM_SIDE, p + 1), fill=(0, 0, 0, 90), width=1)
        draw.line((0, p, ZOOM_SIDE, p), fill=(255, 255, 255, 150), width=1)

    def label(text: str, px: float, py: float) -> None:
        box = draw.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0] + 6, box[3] - box[1] + 6
        px = max(0, min(px, ZOOM_SIDE - tw))
        py = max(0, min(py, ZOOM_SIDE - th))
        draw.rectangle((px, py, px + tw, py + th), fill=(0, 0, 0, 170))
        draw.text((px + 3, py + 3 - box[1]), text, fill=(255, 255, 255), font=font)

    for v in xs:
        label(str(v), at(v, left) + 2, 0)
        label(str(v), at(v, left) + 2, ZOOM_SIDE)
    for v in ys:
        label(str(v), 0, at(v, top) + 2)
        label(str(v), ZOOM_SIDE, at(v, top) + 2)
    # Where she asked to look, so she can tell how far off it is from what she wants.
    cx, cy = at(x, left), at(y, top)
    draw.line((cx - 10, cy, cx + 10, cy), fill=RING + (255,), width=2)
    draw.line((cx, cy - 10, cx, cy + 10), fill=RING + (255,), width=2)
    return Zoom(_jpeg(big), left, top, left + side, top + side, factor, step)
