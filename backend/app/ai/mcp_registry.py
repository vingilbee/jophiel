# app/ai/mcp_registry.py
from functools import lru_cache
from pathlib import Path
from string import Template
from typing import Any
import json

TOOLS_DIR = Path(__file__).parent / "tools"
_CONFIG_PATHS = {p.parent.name: p for p in TOOLS_DIR.glob("*/config.json")}


@lru_cache
def _load_config(name: str) -> dict[str, Any]:
    with _CONFIG_PATHS[name].open() as f:
        return json.load(f)[name]


def _fill_template(value: Any, params: dict[str, str]) -> Any:
    if isinstance(value, str):
        return Template(value).safe_substitute(params)
    if isinstance(value, dict):
        return {k: _fill_template(v, params) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill_template(v, params) for v in value]
    return value


def build_server_config(connections: dict[str, dict]) -> dict[str, dict]:
    config: dict[str, dict] = {}
    for name, params in connections.items():
        if name not in _CONFIG_PATHS:
            continue
        config[name] = _fill_template(_load_config(name), params)
    return config