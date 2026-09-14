"""Small newline-delimited JSON client for the local Blender add-on."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any


class BlenderBridgeError(RuntimeError):
    pass


async def call(command: str, **params: Any) -> dict[str, Any]:
    host = os.environ.get("BLENDER_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("BLENDER_BRIDGE_PORT", "9877"))
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3
        )
        writer.write(
            (json.dumps({"command": command, "params": params}) + "\n").encode()
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=120)
        writer.close()
        await writer.wait_closed()
    except (OSError, TimeoutError) as exc:
        raise BlenderBridgeError(
            "Blender bridge is unavailable. Open Blender, enable the "
            "MacroVision Blender Bridge add-on, and start its server."
        ) from exc
    if not raw:
        raise BlenderBridgeError("Blender bridge closed without a response.")
    response = json.loads(raw)
    if not response.get("ok"):
        raise BlenderBridgeError(response.get("error", "Blender command failed."))
    return response.get("result", {})
