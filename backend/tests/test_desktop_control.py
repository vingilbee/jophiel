"""General desktop control on the backend: coordinates, the picture hook, results, observations, prompt.

Pure parts need nothing. ``act`` runs against the in-memory Redis stand-in with a fake relay answering.
"""
from __future__ import annotations

import base64
import json
import threading
import uuid

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.ai.context import AgentRuntimeContext
from app.ai.observe import registry
from app.ai.prompts import build_system_prompt
from app.ai.tools.desktop_control.tools import (
    ARTIFACT_KEY, SCREENSHOTS_SHOWN, NotOnScreenshot, Screens, result_text, screenshot_hook,
)
from app.services import desktop_control
from app.services.desktop_control import DesktopAccess, Outcome

SCREEN = (0, 0, 1920, 1080)


def full(screens: Screens):
    return screens.add({"region": SCREEN, "size": (1280, 720), "screen": SCREEN})


# --------------------------------------------------------------------------- #
# Coordinates: a point on the screenshot she saw -> a screen pixel
# --------------------------------------------------------------------------- #


def test_points_on_a_scaled_screenshot_map_to_the_screen():
    screens = Screens()
    full(screens)
    assert screens.to_screen(0, 0) == (0, 0)
    assert screens.to_screen(640, 360) == (960, 540)
    assert screens.to_screen(1279, 719) == (1919, 1079)
    with pytest.raises(NotOnScreenshot):
        screens.to_screen(1280, 10)


def test_a_crop_has_its_own_pixels_and_offset():
    screens = Screens()
    full(screens)
    region = screens.crop_region([640, 360, 100, 50])  # on s1: screen 960,540, 150x75
    assert region == (960, 540, 150, 75)
    crop = screens.add({"region": region, "size": (300, 150), "screen": SCREEN})
    assert crop.id == "s2" and crop.cropped
    assert screens.to_screen(0, 0) == (960, 540)  # the latest is the crop
    assert screens.to_screen(150, 75) == (1035, 577)
    assert screens.to_screen(640, 360, "s1") == (960, 540)  # an earlier one by name


def test_without_a_screenshot_there_are_no_points():
    with pytest.raises(NotOnScreenshot, match="screenshot first"):
        Screens().to_screen(1, 1)
    screens = Screens()
    full(screens)
    with pytest.raises(NotOnScreenshot, match="no screenshot s9"):
        screens.to_screen(1, 1, "s9")


def test_results_say_what_happened_and_never_overclaim():
    assert result_text(Outcome("a", "done"), "clicked").startswith("Done: clicked.")
    assert "Nothing was pressed" in result_text(Outcome("a", "refused", "view off"), "x")
    assert "may or may not" not in result_text(Outcome("a", "failed", "blocked"), "x")
    assert result_text(Outcome("a", "unconfirmed", "no answer"), "x").startswith("Unconfirmed")


# --------------------------------------------------------------------------- #
# The picture hook
# --------------------------------------------------------------------------- #


def _shot_turn(n: int):
    messages = [SystemMessage("system"), HumanMessage("open notepad")]
    for i in range(1, n + 1):
        call = {"name": "screenshot", "args": {}, "id": f"c{i}"}
        messages.append(AIMessage("", tool_calls=[call]))
        messages.append(ToolMessage(f"Screenshot s{i}", tool_call_id=f"c{i}", artifact={
            ARTIFACT_KEY: {"id": f"s{i}", "jpeg_b64": base64.b64encode(b"jpeg%d" % i).decode()}}))
    return messages


def test_pictures_follow_their_tool_answers_and_only_the_newest_are_shown():
    messages = _shot_turn(SCREENSHOTS_SHOWN + 2)
    out = screenshot_hook({"messages": messages})["llm_input_messages"]
    pictures = [m for m in out if isinstance(m, HumanMessage) and isinstance(m.content, list)]
    assert len(pictures) == SCREENSHOTS_SHOWN
    for picture in pictures:  # each right after a tool answer, before the next model turn
        before = out[out.index(picture) - 1]
        assert isinstance(before, ToolMessage)
    labels = [part["text"] for p in pictures for part in p.content if part["type"] == "text" and part["text"].startswith("Screenshot")]
    assert labels[-1] == f"Screenshot s{SCREENSHOTS_SHOWN + 2}:"
    assert len(messages) == len(out) - SCREENSHOTS_SHOWN  # the state itself is untouched


def test_parallel_tool_answers_stay_together():
    messages = [HumanMessage("hi"), AIMessage("", tool_calls=[
        {"name": "screenshot", "args": {}, "id": "a"}, {"name": "click", "args": {}, "id": "b"}]),
        ToolMessage("Screenshot s1", tool_call_id="a", artifact={ARTIFACT_KEY: {"id": "s1", "jpeg_b64": "eA=="}}),
        ToolMessage("Done", tool_call_id="b")]
    out = screenshot_hook({"messages": messages})["llm_input_messages"]
    assert [type(m).__name__ for m in out] == ["HumanMessage", "AIMessage", "ToolMessage", "ToolMessage", "HumanMessage"]


def test_no_screenshots_no_change():
    messages = [HumanMessage("hello")]
    assert screenshot_hook({"messages": messages})["llm_input_messages"] == messages


# --------------------------------------------------------------------------- #
# act(): one action there and back (fake relay over the Redis stand-in)
# --------------------------------------------------------------------------- #


class _Db:
    def commit(self):
        pass


def test_act_refuses_without_a_usable_desktop(monkeypatch):
    monkeypatch.setattr(desktop_control, "access_for", lambda db, uid: None)
    assert desktop_control.act(_Db(), uuid.uuid4(), {"kind": "screenshot"}).status == "refused"
    access = DesktopAccess(uuid.uuid4(), "VM", online=True, gate="manual_auth", busy=False)
    monkeypatch.setattr(desktop_control, "access_for", lambda db, uid: access)
    outcome = desktop_control.act(_Db(), uuid.uuid4(), {"kind": "click", "point": [1, 1]})
    assert outcome.status == "refused" and "signing in" in outcome.reason


class _Redis:
    """Just what act() and the relay helpers use."""

    def __init__(self):
        self.lists, self.values, self.streams = {}, {}, {}
        self.cond = threading.Condition()

    def xadd(self, key, fields, **_):
        with self.cond:
            self.streams.setdefault(key, []).append(fields)
            self.cond.notify_all()

    def blpop(self, keys, timeout):
        with self.cond:
            ok = self.cond.wait_for(lambda: self.lists.get(keys[0]), timeout)
            return (keys[0], self.lists[keys[0]].pop(0)) if ok else None

    def pipeline(self):
        return _Pipe(self)

    def set(self, key, value, ex=None):
        self.values[key] = value

    def get(self, key):
        return self.values.get(key)


class _Pipe:
    def __init__(self, redis_):
        self.r, self.ops = redis_, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def rpush(self, key, value):
        self.ops.append((key, value))

    def expire(self, *a):
        pass

    def execute(self):
        with self.r.cond:
            for key, value in self.ops:
                self.r.lists.setdefault(key, []).append(value.encode() if isinstance(value, str) else value)
            self.r.cond.notify_all()


@pytest.fixture
def relay(monkeypatch):
    fake = _Redis()
    env_id = uuid.uuid4()
    monkeypatch.setattr(desktop_control.desktop_preview, "redis_client", lambda: fake)
    monkeypatch.setattr(desktop_control, "access_for",
                        lambda db, uid: DesktopAccess(env_id, "VM", online=True, gate="allowed", busy=False))

    def answer(result: dict, jpeg: bytes | None = None):
        def run():
            with fake.cond:
                fake.cond.wait_for(lambda: fake.streams.get(f"desktop:commands:{env_id}"), 5)
                fields = fake.streams[f"desktop:commands:{env_id}"][0]
            assert json.loads(fields["action"])["kind"]
            if jpeg is not None:
                desktop_control.store_shot(fields["action_id"], jpeg)
            desktop_control.store_result(fields["action_id"], {**result, "action_id": fields["action_id"]})
        threading.Thread(target=run, daemon=True).start()
    return answer


def test_act_carries_a_screenshot_back(relay):
    screen = {"region": [0, 0, 1920, 1080], "size": [1280, 720], "screen": [0, 0, 1920, 1080]}
    relay({"status": "done", "screen": screen, "foreground_process": "notepad.exe"}, b"\xff\xd8jpeg")
    outcome = desktop_control.act(_Db(), uuid.uuid4(), {"kind": "screenshot"})
    assert outcome.status == "done" and outcome.jpeg == b"\xff\xd8jpeg" and outcome.screen == screen
    assert outcome.foreground_process == "notepad.exe"


def test_a_screenshot_whose_picture_was_dropped_is_a_failure(relay):
    relay({"status": "done", "screen": {"region": [0, 0, 8, 8], "size": [8, 8], "screen": [0, 0, 8, 8]}})
    outcome = desktop_control.act(_Db(), uuid.uuid4(), {"kind": "screenshot"})
    assert outcome.status == "failed" and "lost" in outcome.reason


def test_no_answer_is_unconfirmed(relay, monkeypatch):
    monkeypatch.setattr(desktop_control, "wait_seconds", lambda action: 0.05)
    outcome = desktop_control.act(_Db(), uuid.uuid4(), {"kind": "click", "point": [1, 1]})
    assert outcome.status == "unconfirmed" and "may or may not" in outcome.reason


# --------------------------------------------------------------------------- #
# Observation: what she did and saw, and what memory keeps
# --------------------------------------------------------------------------- #


def _payload(**kw):
    return {"action_id": "a" * 32, "tool": "click", "status": "done", "summary": "clicked (left)",
            "foreground_process": "chrome.exe", "foreground_title": "Inbox - Mail", "app_changed": False, **kw}


def test_desktop_actions_are_registered_observations():
    shaped = registry.normalize("desktop", "desktop_action", _payload())
    assert shaped.salience == "routine" and "chrome.exe" in shaped.summary
    assert shaped.spec.consumers == ("memory", "learning")
    with pytest.raises(registry.ObservationInvalid):
        registry.normalize("desktop", "desktop_action", _payload(tool="teleport"))


def test_memory_keeps_failures_and_new_windows_only():
    decide = lambda **kw: registry.decide("desktop", "desktop_action", 1, _payload(**kw), "u")
    assert isinstance(decide(), registry.Skip)
    seen = decide(app_changed=True)
    assert isinstance(seen, registry.Promote) and seen.kind == "desktop_seen" and "Inbox - Mail" in seen.text
    failed = decide(status="failed", reason="Windows blocked the input")
    assert failed.kind == "action_failed" and "blocked" in failed.text
    assert decide(status="failed", reason="Windows blocked the input").coalesce_key == failed.coalesce_key
    assert isinstance(decide(status="refused", reason="view off"), registry.Skip)


# --------------------------------------------------------------------------- #
# Prompt and access
# --------------------------------------------------------------------------- #


def test_the_prompt_has_a_desktop_section_only_with_a_desktop():
    uid = uuid.uuid4()
    assert "## Your desktop" not in build_system_prompt(AgentRuntimeContext(user_id=uid), [])
    line = DesktopAccess(uuid.uuid4(), "Legion", True, "allowed", False).prompt_line()
    prompt = build_system_prompt(AgentRuntimeContext(user_id=uid, desktop=line), [])
    assert "## Your desktop" in prompt and "Legion" in prompt and "can see and use it" in prompt


def test_an_unusable_desktop_is_a_line_without_how_to_use_it():
    uid = uuid.uuid4()
    line = DesktopAccess(uuid.uuid4(), "Legion", False, "allowed", False).prompt_line()
    prompt = build_system_prompt(AgentRuntimeContext(user_id=uid, desktop=line, desktop_usable=False), [])
    assert "## Your desktop" in prompt and "controller is offline" in prompt
    assert "Look, act, check" not in prompt and "no desktop tools" in prompt


def test_access_explains_why_not():
    assert DesktopAccess(uuid.uuid4(), "VM", False, "allowed", False).why_not().endswith("(its controller is offline)")
    assert "switched off" in DesktopAccess(uuid.uuid4(), "VM", True, "blocked", False).why_not()
    assert "game session" in DesktopAccess(uuid.uuid4(), "VM", True, "allowed", True).why_not()
    assert DesktopAccess(uuid.uuid4(), "VM", True, "allowed", False).usable
