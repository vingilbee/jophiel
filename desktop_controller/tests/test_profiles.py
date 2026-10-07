from __future__ import annotations

import copy

import pytest

from conftest import PROFILES
from desktop_controller.cli import profile_check
from desktop_controller.profile import ProfileError, load_profile, parse_profile


def issues_for(raw: dict) -> list[str]:
    with pytest.raises(ProfileError) as exc:
        parse_profile(raw, "test")
    return exc.value.issues


def test_minecraft_profile_loads():
    profile = load_profile(PROFILES / "minecraft_java.yaml")
    assert profile.id == "minecraft_java"
    assert profile.key_for("forward") == "W"
    assert profile.look_axes() is not None
    h, v = profile.fov_deg()
    assert v == pytest.approx(70)
    assert h == pytest.approx(102.45, abs=0.1)  # vertical 70 at 16:9


def test_generic_profile_is_a_template_until_target_is_set():
    with pytest.raises(ProfileError) as exc:
        load_profile(PROFILES / "generic_keyboard_mouse.yaml")
    assert any("template" in i for i in exc.value.issues)


def test_generic_profile_valid_once_target_set(generic):
    assert generic.camera is None
    assert "ESC" not in {b.key for b in generic.buttons.values()}


@pytest.mark.parametrize("key", ["LWIN", "WIN", "CMD", "DELETE", "APPS"])
def test_os_keys_fail_validation(minecraft_raw, key):
    minecraft_raw["buttons"]["bad"] = {"key": key, "description": "x"}
    assert any("blocked_os_key" in i for i in issues_for(minecraft_raw))


def test_unknown_key_fails(minecraft_raw):
    minecraft_raw["buttons"]["bad"] = {"key": "HYPER", "description": "x"}
    assert any("unknown key" in i for i in issues_for(minecraft_raw))


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda r: r.update(online_allowed=True), "online play"),
        (lambda r: r.update(macros={"combo": [{"press": ["forward"]}]}), "macros"),
        (lambda r: r["contexts"]["modes"]["menu"]["allow"].append("text"), "typing"),
        (lambda r: r["contexts"]["modes"]["menu"]["allow"].append("teleport"), "unknown input kind"),
        (lambda r: r["regions"].update(crosshair=[600, 300, 700, 400]), "inside the 640x360"),
        (lambda r: r["regions"].update(full=[0, 0, 10, 10]), "built-in"),
        (lambda r: r["limits"].update(max_valid_for_ms=60_000), "global ceiling"),
        (lambda r: r.update(camera={"fov_h_deg": 70, "fov_v_deg": 70}), "disagree"),
        (lambda r: r["contexts"]["detect"].update(cursor_hidden="flying"), "cursor_hidden"),
        (lambda r: r["contexts"]["modes"]["menu"].update(buttons=["nope"]), "unknown button"),
        (lambda r: r["observation"]["crops"].append({"region": "nowhere"}), "unknown region"),
        (lambda r: r["axes"]["look_yaw"].pop("counts_per_degree"), "counts_per_degree"),
        (lambda r: r.update(format_version=2), "format_version"),
    ],
)
def test_profile_rules(minecraft_raw, mutate, expected):
    raw = copy.deepcopy(minecraft_raw)
    mutate(raw)
    assert any(expected in i for i in issues_for(raw)), issues_for(raw)


def test_look_at_needs_camera(minecraft_raw):
    minecraft_raw.pop("camera")
    assert any("look_at needs" in i for i in issues_for(minecraft_raw))


def test_extra_fields_are_refused(minecraft_raw):
    minecraft_raw["buttons"]["forward"]["script"] = "rm -rf /"
    assert any("script" in i for i in issues_for(minecraft_raw))


def test_unknown_context_allows_only_the_common_subset(minecraft):
    rules = minecraft.rules_for("unknown")
    assert rules.kinds == {"hold", "tap"}
    assert "forward" not in rules.buttons
    assert "inventory" in rules.buttons


def test_cli(capsys):
    assert profile_check([str(PROFILES / "minecraft_java.yaml")]) == 0
    assert profile_check([str(PROFILES / "generic_keyboard_mouse.yaml")]) == 1
    out = capsys.readouterr().out
    assert "ok   " in out and "FAIL" in out
