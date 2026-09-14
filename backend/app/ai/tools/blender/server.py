"""MCP tools for safe, explicit animation and rigging operations in Blender."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations

from app.ai.tools.blender.client import call
from app.services import asset_store

mcp = FastMCP(
    "blender",
    instructions=(
        "Inspect and edit the user's open Blender scene through explicit scene, "
        "rigging, animation, render, import, and export operations."
    ),
    log_level="WARNING",
)
_READ_ONLY = ToolAnnotations(readOnlyHint=True)


def _user_id() -> uuid.UUID:
    value = os.environ.get("BLENDER_USER_ID")
    if not value:
        raise RuntimeError("BLENDER_USER_ID is required.")
    return uuid.UUID(value)


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, default=str)


@mcp.tool(annotations=_READ_ONLY)
async def blender_status() -> str:
    """Check the bridge and report the open blend file, scene, and Blender version."""
    return _json(await call("status"))


@mcp.tool(annotations=_READ_ONLY)
async def blender_scene_summary() -> str:
    """List scene objects, their types/transforms, armatures, actions, and frame range."""
    return _json(await call("scene_summary"))


@mcp.tool(annotations=_READ_ONLY)
async def blender_inspect_mesh(object_name: str) -> str:
    """Inspect mesh geometry, bounds, topology counts, materials, modifiers, vertex groups, and rig binding."""
    return _json(await call("inspect_mesh", object_name=object_name))


@mcp.tool(annotations=_READ_ONLY)
async def blender_render_mesh_views(
    object_names: list[str], resolution: int = 512
) -> list:
    """Render front, side, and perspective views and return actual images for visual inspection.

    Use this before placing a rig and again after posing it. The images are
    delivered through MCP, so the vision-capable specialist can examine them.
    """
    if not object_names:
        raise ValueError("Provide at least one mesh object name.")
    resolution = max(256, min(resolution, 1024))
    directory = Path(tempfile.mkdtemp(prefix="macrovision-blender-"))
    result = await call(
        "render_mesh_views",
        object_names=object_names,
        directory=str(directory),
        resolution=resolution,
    )
    paths = [Path(path) for path in result.get("paths", [])]
    if len(paths) != 3 or any(not path.is_file() for path in paths):
        raise RuntimeError("Blender did not produce all three inspection views.")
    return [
        _json({"objects": object_names, "views": [p.stem for p in paths]}),
        *[Image(path=path) for path in paths],
    ]


@mcp.tool(annotations=_READ_ONLY)
async def blender_list_assets(limit: int = 10) -> str:
    """List the user's uploaded .blend, FBX, OBJ, GLB, and glTF assets."""
    limit = max(1, min(limit, 20))
    return _json([r.to_dict() for r in asset_store.list_assets(_user_id(), limit=limit)])


@mcp.tool()
async def blender_import_asset(asset_id: str) -> str:
    """Import one user-owned asset into the open scene by asset_id."""
    path = asset_store.get_path(_user_id(), asset_id)
    return _json(await call("import_asset", path=str(path)))


@mcp.tool()
async def blender_create_armature(name: str, location: list[float] | None = None) -> str:
    """Create an armature object. Add bones with blender_add_bone."""
    return _json(await call("create_armature", name=name, location=location or [0, 0, 0]))


@mcp.tool()
async def blender_add_bone(
    armature: str,
    name: str,
    head: list[float],
    tail: list[float],
    parent: str | None = None,
    connected: bool = False,
) -> str:
    """Add or replace an edit bone using world-space head/tail coordinates."""
    return _json(await call(
        "add_bone", armature=armature, name=name, head=head, tail=tail,
        parent=parent, connected=connected,
    ))


@mcp.tool()
async def blender_parent_mesh_to_armature(
    mesh: str, armature: str, automatic_weights: bool = True
) -> str:
    """Bind a mesh to an armature, normally using Blender automatic weights."""
    return _json(await call(
        "parent_mesh", mesh=mesh, armature=armature,
        automatic_weights=automatic_weights,
    ))


@mcp.tool()
async def blender_create_action(
    armature: str, name: str, frame_start: int = 1, frame_end: int = 250
) -> str:
    """Create and activate an animation action on an armature."""
    return _json(await call(
        "create_action", armature=armature, name=name,
        frame_start=frame_start, frame_end=frame_end,
    ))


@mcp.tool()
async def blender_keyframe_bone(
    armature: str,
    bone: str,
    frame: int,
    location: list[float] | None = None,
    rotation_quaternion: list[float] | None = None,
    scale: list[float] | None = None,
) -> str:
    """Pose a bone and insert location, quaternion rotation, and/or scale keyframes."""
    if location is None and rotation_quaternion is None and scale is None:
        raise ValueError("Provide at least one transform to keyframe.")
    return _json(await call(
        "keyframe_bone", armature=armature, bone=bone, frame=frame,
        location=location, rotation_quaternion=rotation_quaternion, scale=scale,
    ))


@mcp.tool()
async def blender_set_interpolation(
    armature: str,
    interpolation: Literal["CONSTANT", "LINEAR", "BEZIER"] = "BEZIER",
) -> str:
    """Set interpolation for every keyframe in the armature's active action."""
    return _json(await call(
        "set_interpolation", armature=armature, interpolation=interpolation
    ))


@mcp.tool()
async def blender_render_preview(filepath: str = "/tmp/blender-preview.png") -> str:
    """Render the current camera to a PNG path and return that path."""
    return _json(await call("render_preview", filepath=filepath))


@mcp.tool()
async def blender_save_project(filename: str = "animated.blend") -> str:
    """Save a copy of the open scene into the user's asset store and return its asset_id."""
    safe_name = filename if filename.lower().endswith(".blend") else f"{filename}.blend"
    # The bridge writes only to this random, user-scoped destination.
    target_id = os.urandom(8).hex()
    directory = asset_store._user_dir(_user_id())  # scoped and UUID-validated
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f"{target_id}.blend"
    await call("save_project", path=str(temporary))
    data = temporary.read_bytes()
    temporary.unlink(missing_ok=True)
    record = asset_store.save_bytes(_user_id(), data, filename=safe_name, kind="output")
    return _json(record.to_dict())


if __name__ == "__main__":
    mcp.run(transport="stdio")
