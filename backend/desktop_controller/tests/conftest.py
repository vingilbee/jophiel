from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from desktop_controller.profile import Profile, parse_profile

PROFILES = Path(__file__).resolve().parents[2] / "desktop_profiles"  # backend/desktop_profiles


def raw_profile(name: str) -> dict:
    return yaml.safe_load((PROFILES / f"{name}.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def minecraft_raw() -> dict:
    return raw_profile("minecraft_java")


@pytest.fixture
def minecraft(minecraft_raw) -> Profile:
    return parse_profile(minecraft_raw, "minecraft_java")


@pytest.fixture
def generic() -> Profile:
    raw = raw_profile("generic_keyboard_mouse")
    raw["target"]["process"] = "somegame.exe"
    raw["id"] = "somegame_generic"
    return parse_profile(raw, "generic")


@pytest.fixture
def alt_profile(minecraft_raw) -> Profile:
    """Minecraft plus Alt and F4 bound separately: legal alone, never together."""
    raw = copy.deepcopy(minecraft_raw)
    raw["buttons"]["alt"] = {"key": "ALT", "description": "alt"}
    raw["buttons"]["f4"] = {"key": "F4", "description": "f4"}
    return parse_profile(raw, "alt")
