"""Her phone_tap_on (app/ai/tools/phone_grounding.py): the picture at the pointing model's own size, its answer
read and scaled back to the phone's picture, then tapped like phone_tap_point. The model itself is faked."""
from __future__ import annotations

import asyncio
import uuid

import pytest

from app.ai.tools import phone_control as control_tools
from app.ai.tools import phone_grounding
from app.services import phone_actions
from app.services import phone_control
from test_phone_phase9 import screen, tools_by_name


def _real_jpeg(width=800, height=400, color=(30, 120, 40)):
    import base64
    import io

    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "JPEG")
    return base64.b64encode(out.getvalue()).decode()


def test_the_picture_goes_at_the_models_own_size():
    w, h = phone_grounding.smart_size(720, 1600)
    assert w % 28 == 0 and h % 28 == 0 and w * h <= phone_grounding.MAX_PIXELS
    assert abs(w / h - 720 / 1600) < 0.03
    assert phone_grounding.smart_size(560, 560) == (560, 560)  # already a fit: unchanged
    picture, sent, own = phone_grounding.prepare(_real_jpeg(800, 400))
    assert own == (800, 400) and sent == phone_grounding.smart_size(800, 400)
    assert phone_grounding.prepare("not a picture") is None


@pytest.mark.parametrize("answer, point", [
    ("Thought: the attack button.\nAction: click(start_box='(412,1630)')", (412, 1630)),
    ("Action: click(point='<point>412 1630</point>')", (412, 1630)),
    ("Action: click(start_box='<|box_start|>(400,1600,424,1660)<|box_end|>')", (412, 1630)),
    ("click(start_box='[10, 20]')", (10, 20)),
    ("Action: I can't see it", None),
])
def test_the_models_answer_is_read_as_one_point(answer, point):
    assert phone_grounding.parse(answer) == point


def test_a_point_is_scaled_back_to_the_phones_picture():
    assert phone_grounding.scale((280, 560), (560, 1120), (720, 1440)) == (360, 720)
    assert phone_grounding.scale((560, 1120), (560, 1120), (720, 1440)) == (719, 1439)  # kept on the picture
    assert phone_grounding.scale((600, 10), (560, 1120), (720, 1440)) is None


def _phone(monkeypatch, sent, picture):
    def act(user_id, conversation_id, action, args, **kwargs):
        sent.append((action, args))
        shown = {**screen("s1"), "image": {"jpeg_base64": picture, "width": 800, "height": 400}, "items": []}
        return phone_actions.Outcome("done", uuid.uuid4(), "Done", screen=shown)

    monkeypatch.setattr(phone_actions, "act", act)
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_control, "remember", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_grounding, "available", lambda: True)
    monkeypatch.setattr(phone_grounding, "_trace", lambda *args, **kwargs: None)


def _tools():
    tools, control = control_tools.build_phone_control_tools(uuid.uuid4(), uuid.uuid4(), turn_id=uuid.uuid4())
    return tools_by_name(tools), control


def test_tap_on_asks_the_pointing_model_then_taps_where_it_pointed(monkeypatch):
    sent, picture = [], _real_jpeg(800, 400)
    _phone(monkeypatch, sent, picture)
    asked = []

    async def ask(picture_b64, target):
        asked.append(target)
        w, h = phone_grounding.smart_size(800, 400)
        return f"Action: click(start_box='({w // 2},{h // 4})')"

    monkeypatch.setattr(phone_grounding, "ask", ask)
    named, control = _tools()
    call = lambda target: asyncio.run(named["phone_tap_on"].ainvoke({"targets": [target]}))  # noqa: E731

    assert call("the Attack button").startswith("Not done: there's no picture")
    assert sent == []
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    tapped = call("the   Attack button,\nbottom left")
    assert asked == ["the Attack button, bottom left"]
    action, args = sent[-1]
    assert action == "tap_point" and args["screen"] == "s1"
    assert abs(args["x"] - 400) <= 2 and abs(args["y"] - 100) <= 2
    assert tapped.startswith(f"Pointed and tapped, in order: “the Attack button, bottom left” at {args['x']},{args['y']}")
    assert control.acted and control.steps == 2


def test_tap_on_taps_nothing_when_the_model_gives_no_point_or_fails(monkeypatch):
    sent, picture = [], _real_jpeg(800, 400)
    _phone(monkeypatch, sent, picture)
    answers = iter(["Action: I can't find it", RuntimeError("down")])

    async def ask(picture_b64, target):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(phone_grounding, "ask", ask)
    named, _ = _tools()
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    before = len(sent)
    for _ in range(2):
        said = asyncio.run(named["phone_tap_on"].ainvoke({"targets": ["the barracks"]}))
        assert said.startswith("Not done:") and "give its point as \"x,y\"" in said
    assert len(sent) == before


def test_tap_on_is_offered_only_with_the_pointing_models_key(monkeypatch):
    monkeypatch.setattr(phone_control, "begin_turn", lambda *args, **kwargs: None)
    monkeypatch.setattr(phone_grounding, "available", lambda: False)
    assert "phone_tap_on" not in _tools()[0]
    monkeypatch.setattr(phone_grounding, "available", lambda: True)
    assert "phone_tap_on" in _tools()[0]


def test_the_screen_note_names_tap_on_only_when_it_is_offered(monkeypatch):
    shown = {**screen("s1"), "image": {"jpeg_base64": _real_jpeg(), "width": 800, "height": 400}}
    monkeypatch.setattr(phone_control, "_pointing", lambda: False)
    assert "phone_tap_on" not in phone_control.render(shown, picture=True)
    monkeypatch.setattr(phone_control, "_pointing", lambda: True)
    assert "phone_tap_on takes what to tap" in phone_control.render(shown, picture=True)


def test_tap_on_finds_several_targets_at_once_and_taps_them_in_one_step(monkeypatch):
    sent, picture = [], _real_jpeg(800, 400)
    _phone(monkeypatch, sent, picture)
    w, h = phone_grounding.smart_size(800, 400)
    spots = {"card A": (w // 8, h // 2), "grass": (w // 2, h // 8), "card B": (w // 4, h // 2)}

    async def ask(picture_b64, target):
        await asyncio.sleep(0)
        if target not in spots:
            return "Action: I can't see it"
        x, y = spots[target]
        return f"Action: click(start_box='({x},{y})')"

    monkeypatch.setattr(phone_grounding, "ask", ask)
    named, _ = _tools()
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    before = len(sent)
    said = asyncio.run(named["phone_tap_on"].ainvoke({"targets": ["card A", "grass", "nowhere"]}))
    assert said.startswith("Not done:") and "“nowhere”" in said and len(sent) == before  # one missing: none tapped
    said = asyncio.run(named["phone_tap_on"].ainvoke({"targets": ["card A", "grass", "card B", "grass"],
                                                      "gap_ms": 5000}))
    action, args = sent[-1]
    assert action == "tap_points" and len(sent) == before + 1 and args["gap_ms"] == 1000
    assert [(round(x / 100), round(y / 100)) for x, y in args["points"]] == [(1, 2), (4, 0), (2, 2), (4, 0)]
    assert said.startswith("Pointed and tapped, in order: “card A” at")
    assert asyncio.run(named["phone_tap_on"].ainvoke({"targets": []})).startswith("Not done: give 1 to")


def test_tap_on_takes_plain_points_beside_descriptions(monkeypatch):
    sent, picture = [], _real_jpeg(800, 400)
    _phone(monkeypatch, sent, picture)
    asked = []
    w, h = phone_grounding.smart_size(800, 400)

    async def ask(picture_b64, target):
        asked.append(target)
        return f"Action: click(start_box='({w // 8},{h // 2})')"

    monkeypatch.setattr(phone_grounding, "ask", ask)
    named, _ = _tools()
    asyncio.run(named["read_phone_screen"].ainvoke({}))
    before = len(sent)
    said = asyncio.run(named["phone_tap_on"].ainvoke({"targets": ["the dragon card", "300,200", "900,10"]}))
    assert said.startswith("Not done:") and "off the picture (800x400)" in said and len(sent) == before
    asked.clear()
    said = asyncio.run(named["phone_tap_on"].ainvoke({"targets": ["the dragon card", "300,200", "(310, 210)",
                                                                  "320 220"]}))
    assert asked == ["the dragon card"]  # points go straight to the phone, no model asked
    action, args = sent[-1]
    assert action == "tap_points" and args["points"][1:] == [[300, 200], [310, 210], [320, 220]]
    assert abs(args["points"][0][0] - 100) <= 2 and abs(args["points"][0][1] - 200) <= 2
    assert "“the dragon card” at" in said and "; 300,200; 310,210; 320,220." in said
