# MacroVision Blender bridge

This bridge lets the app's Blender specialist edit the currently open scene
through a small, explicit command surface. It does not expose arbitrary Python.

## Setup

1. In Blender 4.2+, open **Edit → Preferences → Add-ons → Install from Disk**.
2. Select `macrovision_blender_bridge.py`, then enable the add-on.
3. In the 3D View press **N**, open **Agent**, and click **Start bridge**.
4. Set `BLENDER_TOOLS_ENABLED=true` in `backend/.env` and restart the API.
5. Apply the database migration with `uv run alembic upgrade head`.

The add-on listens only on `127.0.0.1:9877`. The backend spawns the MCP server
per agent turn, following the same registry/sub-agent pattern as Gmail.

Supported uploads: `.blend`, `.fbx`, `.obj`, `.glb`, and `.gltf` (100 MB by
default). Completed scenes are saved as new user-scoped assets; source uploads
are never overwritten.

## Current tool surface

- bridge status and complete scene summary
- mesh topology, bounds, materials, modifiers, vertex groups, and rig inspection
- front, side, and perspective renders returned to the agent as MCP images
- list/import user-owned assets
- create armatures and parented bone hierarchies
- bind meshes with automatic weights
- create actions and keyframe pose-bone location/rotation/scale
- set keyframe interpolation
- render a preview
- save a new `.blend` output

The next useful additions are IK constraints, control-rig shapes, vertex-group
editing, NLA tracks, animation retargeting, and downloadable preview images.
