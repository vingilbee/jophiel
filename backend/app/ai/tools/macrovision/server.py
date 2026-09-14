"""MacroVision MCP server: food photo → macronutrients, and depth on its own.

Exposes the nutrition model as MCP tools so the chat agent reaches it the same
way it reaches Gmail — one tool protocol, one place to add capabilities — rather
than through a bespoke code path.

How this one is run
-------------------
Unlike the Gmail server, this is **mounted into the API process** over
streamable HTTP (``mount.py``) instead of being spawned per turn over stdio.
Gmail's subprocess costs a few hundred milliseconds; this one would reload 3.6GB
of ZoeDepth and ViT weights on *every chat turn*. Mounted, it shares the
process-wide :func:`get_predictor` singleton and a tool call costs only
inference.

It still runs standalone for external MCP clients (Claude Desktop and friends)::

    MACROVISION_USER_ID=<uuid> uv run python -m app.ai.tools.macrovision.server

which loads its own copy of the models — fine for a long-lived desktop client,
which is exactly the case where paying once is acceptable.

Identity
--------
Tools act on one user's stored images. Over HTTP the user comes from a signed
bearer token (``scope.py``); over stdio, from ``MACROVISION_USER_ID``. Images
are addressed by ``image_id`` and resolved inside that user's directory, so a
guessed or borrowed id can't reach anyone else's photo.

Tools take ``image_id``, never raw image data: an MCP tool argument is part of
the model's context, and a base64 JPEG there would be both enormous and useless
to it.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from app.ai.MacroVision import get_predictor
from app.ai.MacroVision.types import NutritionHints
from app.ai.tools.macrovision.scope import (
    SCOPE_HEADER,
    SCOPE_PREFIX,
    USER_ID_ENV,
    read_scope_token,
)
from app.core.config import get_settings
from app.services import image_store

logger = logging.getLogger(__name__)
load_dotenv()
USDA_API_KEY = os.environ["USDA_API_KEY"]



mcp = FastMCP(
    "macrovision",
    instructions=(
        "Estimate macronutrients (calories, protein, carbs, fat) from a photo "
        "of food, and inspect the depth map behind that estimate. Images are "
        "referred to by image_id — call macrovision_list_food_photos to "
        "discover ids you weren't given."
        """
        Your job is to answer diet and nutrition questions clearly and accurately:
        - Explain macronutrients (calories, protein, carbs, fat), portion sizes, and \
        how foods fit into a user's goals.
        - Be concise and practical. Lead with the answer, then any useful detail.
        - When a question depends on specifics you don't have (the user's goals, \
        weight, or logged intake), say what you'd need rather than guessing.

        You are not a medical professional. For medical conditions, medications, or \
        clinical dietary needs, recommend consulting a doctor or registered dietitian. \
        Do not diagnose or prescribe.
        """
        
    ),
    # Mounted at the app root path; see mount.py.
    streamable_http_path="/",
    # Each chat turn is a fresh connection with nothing to carry between calls,
    # so there's no session state worth tracking.
    stateless_http=True,
    # Quiet by default: it shares a process (or a stdout pipe) with the API.
    log_level="WARNING",
)

_READ_ONLY = ToolAnnotations(readOnlyHint=True)


class _NoUser(RuntimeError):
    """The caller didn't identify a user, so there are no images to act on."""


def _user_id(ctx: Context) -> uuid.UUID:
    """Resolve whose images this call may touch.

    HTTP callers present a signed bearer token; the stdio entrypoint names the
    user in the environment. Anything else is refused — defaulting to "some
    user" here would be a cross-account data leak.
    """
    request = getattr(ctx.request_context, "request", None)
    if request is not None:
        header = request.headers.get(SCOPE_HEADER, "")
        if header.startswith(SCOPE_PREFIX):
            user_id = read_scope_token(header[len(SCOPE_PREFIX) :])
            if user_id is not None:
                return user_id
        raise _NoUser(
            "Missing or expired authorization for the MacroVision MCP server."
        )

    env_user = os.environ.get(USER_ID_ENV)
    if env_user:
        try:
            return uuid.UUID(env_user)
        except ValueError as exc:
            raise _NoUser(f"{USER_ID_ENV} is not a valid UUID.") from exc

    raise _NoUser(
        f"No user in context. Set {USER_ID_ENV} when running this server standalone."
    )


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


def _resolve_image(ctx: Context, image_id: str) -> tuple[uuid.UUID, bytes]:
    """Load one of the calling user's images, or raise a model-readable error."""
    user_id = _user_id(ctx)
    try:
        data, _ = image_store.read_bytes(user_id, image_id)
    except image_store.ImageNotFoundError as exc:
        raise ValueError(
            f"No image {image_id!r} available. Call macrovision_list_food_photos "
            "to see which photos this user has."
        ) from exc
    return user_id, data


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=_READ_ONLY)
async def macrovision_list_food_photos(ctx: Context, limit: int = 5) -> str:
    """List the user's recently uploaded food photos, newest first.

    Returns each photo's image_id, dimensions, and upload time. Use this when
    the user refers to a photo ("my last meal", "the one from this morning")
    without giving you an id. Depth maps this tool generated are excluded.
    """
    settings = get_settings()
    limit = max(1, min(limit, settings.image_list_max))
    records = image_store.list_images(_user_id(ctx), limit=limit)
    if not records:
        return (
            "This user has no uploaded food photos. Ask them to attach one in "
            "the chat, or to upload it on the Predict page."
        )
    return _json([r.to_dict() for r in records])


# --------------------------------------------------------------------------- #
# Nutrition prediction
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=_READ_ONLY)
async def macrovision_predict_combined(
    ctx: Context, 
    image_id: str,
    known_weight_g: float | None = None,
    known_ingredients: list[str] | None = None,
    extra_notes: str | None = None) -> str:
    """Estimate macros from a food photo using the full pipeline. Most accurate.

    Runs the depth + ViT regressor *and* a vision-language model: the VLM
    identifies the dish and its per-100g nutrition, and the regressor's calorie
    estimate corrects the VLM's portion-size guess. Returns calories, fat,
    carbs, protein (grams, for the whole portion), the identified dish, and the
    portion_scale correction that was applied.

    Prefer this for any real "how many calories is this?" question. It is the
    slower of the two — a few seconds — because it calls a VLM. If the VLM is
    unavailable the result falls back to regression, which you can tell from
    `source` being "regression" rather than "combined".
    """
    _, data = _resolve_image(ctx, image_id)
    hints: NutritionHints = {}
    if known_weight_g is not None:
        hints["known_weight_g"] = known_weight_g
    if known_ingredients is not None:
        hints["known_ingredients"] = known_ingredients
    if extra_notes is not None:
        hints["extra_notes"] = extra_notes

    prediction = await get_predictor().predict_async(
        data, mode="combined", hints=hints
    )
    return _json({"image_id": image_id, **prediction.to_dict()})


@mcp.tool(annotations=_READ_ONLY)
async def macrovision_predict_regression(ctx: Context, image_id: str) -> str:
    """Estimate macros from a food photo using only the trained regressor. Fast.

    Runs ZoeDepth for a depth map and feeds RGB + depth to the fine-tuned ViT
    cross-attention regressor. No VLM, so it is markedly faster and cheaper than
    macrovision_predict_combined — but it cannot name the dish and is generally
    less accurate on unusual foods.

    Use it when the user explicitly asks for a quick estimate, when you want a
    second opinion to compare against the combined result, or when the combined
    call has already failed.
    """
    _, data = _resolve_image(ctx, image_id)
    prediction = await get_predictor().predict_async(data, mode="regression")
    return _json({"image_id": image_id, **prediction.to_dict()})


# --------------------------------------------------------------------------- #
# Depth
# --------------------------------------------------------------------------- #
@mcp.tool(annotations=_READ_ONLY)
async def macrovision_estimate_depth(
    ctx: Context, image_id: str, render_image: bool = True
) -> str:
    """Estimate the metric depth map of a photo. No nutrition prediction.

    Runs the fine-tuned ZoeDepth model alone and returns the depth distribution
    in metres: min/max/mean/median, the 10th and 90th percentiles, standard
    deviation, and `relief_m` (max minus min — how much the scene actually
    stands out from its background). Also reports the shape of the
    (1,3,224,224) depth tensor the regressor consumes.

    This is the geometry behind a macro prediction, so it is the tool to reach
    for when a portion estimate looks wrong: a small `relief_m` means the photo
    is nearly flat — shot straight down, or of a flat plate — and the model had
    little volume signal to work from, which is worth telling the user.

    With render_image=true (the default) a false-colour depth map is saved to
    the user's images and its `depth_image_id` returned, so it can be displayed.
    Set it to false when you only need the numbers.
    """
    user_id, data = _resolve_image(ctx, image_id)
    estimate = await get_predictor().estimate_depth_async(
        data, render_image=render_image, include_tensor=True
    )

    result: dict[str, Any] = {"image_id": image_id, **estimate.to_dict()}
    if estimate.depth_image is not None:
        record = image_store.save_pil(
            user_id, estimate.depth_image, kind="depth", derived_from=image_id
        )
        result["depth_image_id"] = record.image_id
    return _json(result)

# --------------------------------------------------------------------------- #
# USDA LOOKUP
# --------------------------------------------------------------------------- #
usda_url = "https://api.nal.usda.gov/fdc"
@mcp.tool(annotations=_READ_ONLY)
async def usda_get_food_details(
    ctx: Context,
    food_ids: list[str],
    format: Optional[str] = None,
    nutrients: Optional[list[int]] = None,
):
    """Fetch full details for multiple foods in a single request.
    More efficient than calling usda_get_food_detail repeatedly.
    Accepts up to 20 fdcIds per USDA API limits.
    """
    if len(food_ids) > 20:
        return {"error": "USDA API allows a maximum of 20 fdcIds per request."}

    url = f"{usda_url}/v1/foods"
    body = {
        "fdcIds": food_ids,
        "api_key": USDA_API_KEY,
    }
    if format is not None:
        body["format"] = format
    if nutrients is not None:
        body["nutrients"] = nutrients

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"USDA API returned {e.response.status_code}", "detail": e.response.text}
    except httpx.RequestError as e:
        return {"error": f"Request failed: {e}"}



@mcp.tool(annotations=_READ_ONLY)
async def usda_search_food(
    ctx: Context,
    query: str,
    page_size: Optional[int] = 10,
    data_type: Optional[list[str]] = None,  # e.g. ["Foundation", "SR Legacy", "Branded"]
):
    """Search USDA FoodData Central for foods matching a query.
    Returns a list of candidate foods with their fdcId, description, and data type.
    Use the fdcId from a result to call usda_get_food_detail for full nutrient info.
    """
    url = f"{usda_url}/v1/foods/search"
    params = {
        "api_key": USDA_API_KEY,
        "query": query,
        "pageSize": page_size,
    }
    if data_type is not None:
        params["dataType"] = data_type

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"USDA API returned {e.response.status_code}", "detail": e.response.text}
    except httpx.RequestError as e:
        return {"error": f"Request failed: {e}"}

    # Trim the response to what's actually useful for picking a food_id
    foods = data.get("foods", [])
    return [
        {
            "fdcId": f.get("fdcId"),
            "description": f.get("description"),
            "dataType": f.get("dataType"),
            "brandOwner": f.get("brandOwner"),  # only present for Branded items
        }
        for f in foods
    ]

@mcp.tool(annotations=_READ_ONLY)
async def usda_list_foods(
    ctx: Context,
    page_size: Optional[int] = 25,
    page_number: Optional[int] = 1,
    data_type: Optional[list[str]] = None,
    sort_by: Optional[str] = None,      # e.g. "dataType.keyword", "description.keyword", "fdcId", "publishedDate"
    sort_order: Optional[str] = None,   # "asc" or "desc"
):
    """Browse USDA FoodData Central foods with pagination, without a search query.
    Use usda_search_food instead if you have a specific food name to look up.
    """
    url = f"{usda_url}/v1/foods/list"
    params = {
        "api_key": USDA_API_KEY,
        "pageSize": page_size,
        "pageNumber": page_number,
    }
    if data_type is not None:
        params["dataType"] = data_type
    if sort_by is not None:
        params["sortBy"] = sort_by
    if sort_order is not None:
        params["sortOrder"] = sort_order

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"USDA API returned {e.response.status_code}", "detail": e.response.text}
    except httpx.RequestError as e:
        return {"error": f"Request failed: {e}"}

    return [
        {
            "fdcId": f.get("fdcId"),
            "description": f.get("description"),
            "dataType": f.get("dataType"),
            "brandOwner": f.get("brandOwner"),
        }
        for f in data
    ]




if __name__ == "__main__":
    # stdio transport: the parent talks JSON-RPC over this process's
    # stdin/stdout, so nothing else may ever be printed to stdout here.
    mcp.run()
