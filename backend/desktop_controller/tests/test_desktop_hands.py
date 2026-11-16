"""General desktop control on the controller: the action contract, bounds, and releasing what was held."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from desktop_controller.capture import FakeCapture
from desktop_controller.desktop import DesktopHands, Refused, parse_action, parse_chord
from desktop_controller.fake import FakeDesktopInput
from desktop_controller.link import DesktopAction, DesktopResult, FrameHeader, relay_message, controller_message


def hands(**kwargs):
    backend = FakeDesktopInput(**kwargs)
    return DesktopHands(backend, FakeCapture(), sleep=lambda _s: None), backend


def test_chords_put_modifiers_first_and_know_aliases():
    assert parse_chord("T+Shift+Control") == ("ctrl", "shift", "t")
    assert parse_chord("win+e") == ("win", "e")
    assert parse_chord("Escape") == ("esc",)
    with pytest.raises(ValueError):
        parse_chord("ctrl+banana")


@pytest.mark.parametrize("raw, words", [
    ({"kind": "keyboard"}, "either text"),
    ({"kind": "keyboard", "text": "hi", "keys": ["enter"]}, "either text"),
    ({"kind": "keyboard", "keys": ["hyper+x"]}, "unknown key"),
    ({"kind": "keyboard", "keys": ["down"], "repeat": 21}, "bad action"),
    ({"kind": "drag", "path": [[0, 0]]}, "bad action"),
    ({"kind": "click", "point": [1, 2], "button": "side"}, "bad action"),
    ({"kind": "teleport"}, "bad action"),
])
def test_actions_that_dont_fit_are_refused(raw, words):
    with pytest.raises(Refused, match=words):
        parse_action(raw)


def test_a_double_click_with_shift_releases_shift():
    h, backend = hands()
    result, jpeg = h.run("a1", {"kind": "click", "point": [100, 200], "count": 2, "modifiers": ["shift"]})
    assert result.status == "done" and jpeg is None
    assert backend.events == [("move", 100, 200), ("key_down", "shift"), ("button_down", "left"),
                              ("button_up", "left"), ("button_down", "left"), ("button_up", "left"),
                              ("key_up", "shift")]
    assert backend.held == set()
    assert result.foreground_process == "explorer.exe"


def test_a_failure_part_way_still_releases_everything():
    h, backend = hands(fail_on="move")
    result, _ = h.run("a2", {"kind": "drag", "path": [[10, 10], [50, 50]], "modifiers": ["ctrl"]})
    assert result.status == "failed"
    assert backend.held == set()

    h, backend = hands()
    backend.fail_on = "wheel"
    result, _ = h.run("a3", {"kind": "scroll", "point": [5, 5], "down": 3, "modifiers": ["ctrl"]})
    assert result.status == "failed" and ("key_up", "ctrl") in backend.events and backend.held == set()


def test_a_drag_holds_the_button_the_whole_way():
    h, backend = hands()
    result, _ = h.run("a4", {"kind": "drag", "path": [[0, 0], [100, 0], [100, 100]], "duration_ms": 200})
    assert result.status == "done"
    kinds = [e[0] for e in backend.events]
    assert kinds[0] == "move" and kinds[1] == "button_down" and kinds[-1] == "button_up"
    assert backend.events[-2] == ("move", 100, 100)


def test_keys_are_pressed_in_order_and_let_go():
    h, backend = hands()
    result, _ = h.run("a5", {"kind": "keyboard", "keys": ["ctrl+l", "enter"]})
    assert result.status == "done"
    assert backend.events == [("key_down", "ctrl"), ("key_down", "l"), ("key_up", "l"), ("key_up", "ctrl"),
                              ("key_down", "enter"), ("key_up", "enter")]
    h.run("a6", {"kind": "keyboard", "text": "héllo\nworld"})
    assert backend.events[-1] == ("type", "héllo\nworld")


def test_nothing_is_pressed_behind_a_sign_in_prompt_or_off_screen():
    h, backend = hands()
    backend.sensitive = True
    result, _ = h.run("a7", {"kind": "click", "point": [1, 1]})
    assert result.status == "refused" and "sign-in" in result.reason and backend.events == []
    backend.sensitive = False
    result, _ = h.run("a8", {"kind": "click", "point": [5000, 1]})
    assert result.status == "refused" and "off the screen" in result.reason and backend.events == []


def test_without_input_actions_are_refused_but_screenshots_work():
    h = DesktopHands(None, FakeCapture(), sleep=lambda _s: None)
    assert h.run("a9", {"kind": "keyboard", "keys": ["enter"]})[0].status == "refused"
    result, jpeg = h.run("a10", {"kind": "screenshot"})
    assert result.status == "done" and jpeg


def test_a_screenshot_says_what_it_shows_and_a_crop_is_enlarged():
    h, _ = hands()
    result, jpeg = h.run("s1", {"kind": "screenshot", "max_width": 1280, "max_height": 800})
    assert result.screen.region == (0, 0, 1920, 1080) and result.screen.size == (1280, 720)
    assert Image.open(io.BytesIO(jpeg)).size == (1280, 720)
    result, jpeg = h.run("s2", {"kind": "screenshot", "region": [100, 100, 200, 100]})
    assert result.screen.region == (100, 100, 200, 100) and result.screen.size == (400, 200)
    result, _ = h.run("s3", {"kind": "screenshot", "region": [1900, 1070, 400, 400]})
    assert result.status == "done" and result.screen.region == (1900, 1070, 20, 10)
    result, _ = h.run("s4", {"kind": "screenshot", "region": [5000, 5000, 100, 100]})
    assert result.status == "refused"


def test_the_wire_messages_round_trip():
    action = relay_message.validate_json(DesktopAction(action_id="x1", action={"kind": "screenshot"}).model_dump_json())
    assert isinstance(action, DesktopAction)
    result = controller_message.validate_json(DesktopResult(action_id="x1", status="done").model_dump_json())
    assert isinstance(result, DesktopResult)
    header = FrameHeader(seq=0, captured_ms=0, width=10, height=10, gate_epoch=1, kind="screenshot", action_id="x1")
    assert FrameHeader.model_validate_json(header.model_dump_json()).action_id == "x1"


# --------------------------------------------------------------------------- #
# A closing connection stops the hands (its worker thread can't be cancelled)
# --------------------------------------------------------------------------- #


def test_a_stop_mid_drag_sends_nothing_more_and_lets_go_of_the_button():
    import threading

    backend = FakeDesktopInput()
    stop = threading.Event()
    pauses = []

    def sleep(seconds):
        pauses.append(seconds)
        if len(pauses) == 4:  # a few steps into the drag: the connection closes
            stop.set()

    h = DesktopHands(backend, FakeCapture(), sleep=sleep)
    result, _ = h.run("d1", {"kind": "drag", "path": [[0, 0], [500, 500]], "duration_ms": 2000,
                             "modifiers": ["shift"]}, stop)
    assert result.status == "failed" and "connection closed" in result.reason
    assert backend.held == set()
    after = backend.events[[e[0] for e in backend.events].index("button_down") + 1:]
    assert [e[0] for e in after if e[0] not in ("move",)] == ["button_up", "key_up"]  # released, newest first
    assert len([e for e in after if e[0] == "move"]) <= 3  # the moves before the stop, none after


def test_a_stop_ends_a_real_time_hold_at_once_from_another_thread():
    import threading
    import time

    backend = FakeDesktopInput()
    h = DesktopHands(backend, FakeCapture())  # real pauses, woken by the stop
    stop = threading.Event()
    out = {}
    worker = threading.Thread(target=lambda: out.update(r=h.run(
        "d2", {"kind": "drag", "path": [[0, 0], [500, 500]], "duration_ms": 5000}, stop)))
    worker.start()
    time.sleep(0.2)
    stop.set()
    sent = len(backend.events)
    worker.join(1.0)
    assert not worker.is_alive()  # well before the drag's 5 s
    assert backend.held == set()
    assert [e[0] for e in backend.events[sent:]] in (["button_up"], ["move", "button_up"])
    assert out["r"][0].status == "failed"


def test_typed_text_stops_between_pieces():
    import threading

    backend = FakeDesktopInput()
    stop = threading.Event()
    real = backend.type_text

    def type_text(text):
        real(text)
        stop.set()

    backend.type_text = type_text
    h = DesktopHands(backend, FakeCapture(), sleep=lambda _s: None)
    result, _ = h.run("t1", {"kind": "keyboard", "text": "x" * 200}, stop)
    assert result.status == "failed"
    assert [e for e in backend.events if e[0] == "type"] == [("type", "x" * 40)]
