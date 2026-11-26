import json
import uuid
from types import SimpleNamespace

import pytest

from app.schemas.chat import VoiceLatencyReport, chat_ws_message_adapter
from app.services import voice_latency


@pytest.fixture
def log_file(tmp_path, monkeypatch):
    path = tmp_path / "voice_latency.jsonl"
    settings = SimpleNamespace(
        voice_latency_marks_enabled=True, voice_latency_log_path=str(path)
    )
    monkeypatch.setattr(voice_latency, "get_settings", lambda: settings)
    return path, settings


def test_mark_appends_one_json_line_per_stage(log_file):
    path, _ = log_file
    turn = uuid.uuid4()
    voice_latency.mark("stt_final", turn, source="api", wall_ms=1000.0, stt_model="whisper-1")
    voice_latency.mark("job_enqueued", turn, source="api", wall_ms=1050.0, ignored=None)

    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["stage"] for line in lines] == ["stt_final", "job_enqueued"]
    assert lines[0]["turn_id"] == str(turn)
    assert lines[0]["stt_model"] == "whisper-1"
    assert "ignored" not in lines[1]


def test_mark_is_a_noop_when_disabled_or_without_turn(log_file):
    path, settings = log_file
    voice_latency.mark("stt_final", None, source="api")
    settings.voice_latency_marks_enabled = False
    voice_latency.mark("stt_final", uuid.uuid4(), source="api")
    assert not path.exists()


def test_mark_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(voice_latency, "get_settings", boom)
    voice_latency.mark("stt_final", uuid.uuid4(), source="api")


def test_client_report_keeps_only_known_stages(log_file):
    path, _ = log_file
    turn = uuid.uuid4()
    voice_latency.record_client_report(
        turn, outcome="audio", endpoint="vad",
        marks={"vad_endpoint": 10.0, "made_up": 5.0},
    )
    stages = [json.loads(line)["stage"] for line in path.read_text().splitlines()]
    assert stages == ["vad_endpoint", "client_report"]


def test_segments_and_summary_build_the_waterfall(log_file):
    path, _ = log_file
    turn = uuid.uuid4()
    for stage, at in [
        ("speech_last_active", 0), ("vad_endpoint", 800), ("voice_end_sent", 805),
        ("voice_end_received", 810), ("prior_turn_stopped", 815), ("stt_final", 1815),
        ("job_enqueued", 1840), ("job_claimed", 1850), ("context_ready", 2350),
        ("tools_ready", 2400), ("model_first_token", 3100),
        ("first_clause_emitted", 3400), ("tts_request_started", 3400),
        ("tts_first_byte", 5300), ("redis_first_audio", 5301),
        ("ws_first_audio", 5310), ("browser_first_audio", 5315),
        ("browser_playback_started", 5320),
    ]:
        voice_latency.mark(stage, turn, source="test", wall_ms=float(at))
    # A retried job's later claim must not overwrite the first one.
    voice_latency.mark("job_claimed", turn, source="worker", wall_ms=9999.0)

    turns = voice_latency.group_turns(voice_latency.read_records(path))
    segs = voice_latency.segments(turns[str(turn)]["stages"])
    assert segs["endpoint_wait"] == 800
    assert segs["stt"] == 1000
    assert segs["queue_wait"] == 10
    assert segs["tts_first_byte"] == 1900
    assert segs["TOTAL_first_audio"] == 5320

    summary = voice_latency.summarize(turns)
    assert summary["TOTAL_first_audio"]["p50"] == 5320
    assert list(summary)[0] == "endpoint_wait"


def test_manual_endpoint_starts_the_clock_at_voice_end_sent():
    stages = {"voice_end_sent": 100.0, "browser_playback_started": 4100.0}
    assert voice_latency.segments(stages)["TOTAL_first_audio"] == 4000


def test_percentile_nearest_rank():
    values = [float(v) for v in range(1, 21)]
    assert voice_latency.percentile(values, 95) == 19
    assert voice_latency.percentile([5.0], 95) == 5


def test_ws_adapter_accepts_latency_report_and_rejects_unknown_stage():
    turn = str(uuid.uuid4())
    message = chat_ws_message_adapter.validate_json(json.dumps({
        "type": "voice_latency", "turn_id": turn, "outcome": "audio",
        "endpoint": "manual", "marks": {"voice_end_sent": 1.5},
    }))
    assert isinstance(message, VoiceLatencyReport)
    assert message.marks == {"voice_end_sent": 1.5}

    with pytest.raises(ValueError):
        chat_ws_message_adapter.validate_json(json.dumps({
            "type": "voice_latency", "turn_id": turn, "outcome": "audio",
            "marks": {"transcript": 1.0},
        }))


# --------------------------------------------------------------------------- #
# Her loop inside model_ttft (app/ai/loop_timing.py)


def _scripted_agent():
    """A real LangGraph agent whose model is scripted: a tool call, then words. The tool runs a model itself (as
    the phone checks do), which must count as the tool's time, not as a call of hers."""
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
    from langchain_core.tools import tool
    from langgraph.prebuilt import create_react_agent

    class Scripted(BaseChatModel):
        replies: list

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])

        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            reply = self.replies.pop(0)
            chunk = AIMessageChunk(content=reply.content, tool_call_chunks=[
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(reply.tool_calls)],
                usage_metadata={"input_tokens": 3000, "output_tokens": 5, "total_tokens": 3005,
                                "input_token_details": {"cache_read": 2048}})
            if run_manager is not None:
                run_manager.on_llm_new_token(reply.content or "", chunk=ChatGenerationChunk(message=chunk))
            yield ChatGenerationChunk(message=chunk)

    inner = Scripted(replies=[AIMessage("inner verdict")])

    @tool
    def read_phone_screen() -> str:
        """Read the screen."""
        inner.invoke("look")
        return "screen 1"

    model = Scripted(replies=[
        AIMessage("", tool_calls=[{"name": "read_phone_screen", "args": {}, "id": "c1"}]),
        AIMessage("Done."),
    ])
    return create_react_agent(model, tools=[read_phone_screen])


def test_loop_timer_marks_her_model_calls_and_tools_only(monkeypatch):
    import asyncio

    from app.ai import chat as ai_chat
    from app.ai.context import AgentRuntimeContext

    agent = _scripted_agent()

    class Built:
        async def __aenter__(self):
            return agent, []

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(ai_chat, "_orchestrator_for", lambda context, queue=None: Built())
    monkeypatch.setattr(ai_chat, "_build_inputs", lambda messages, context, specs: {"messages": messages})
    marks: list[tuple[str, dict]] = []

    async def run():
        async for _ in ai_chat.stream_chat(messages=[("user", "hi")], context=AgentRuntimeContext(uuid.uuid4()),
                                           on_stage=lambda stage, **dims: marks.append((stage, dims))):
            pass

    asyncio.run(run())
    stages = [stage for stage, _ in marks]
    assert stages == ["tools_ready",
                      "loop_model_start", "loop_model_first_chunk", "loop_model_end",
                      "loop_tool_start", "loop_tool_end",
                      "loop_model_start", "loop_model_first_chunk", "loop_model_end"]
    ends = [dims for stage, dims in marks if stage == "loop_model_end"]
    assert ends[0] == {"call": 1, "tool_calls": 1, "input_tokens": 3000, "cached_tokens": 2048, "output_tokens": 5}
    assert ends[1]["call"] == 2 and ends[1]["tool_calls"] == 0
    assert dict(marks)["loop_tool_start"] == {"tool": "read_phone_screen", "call": 1}


def test_loop_steps_and_metrics_from_marks(log_file):
    path, _ = log_file
    turn = uuid.uuid4()
    for stage, at, dims in [
        ("tools_ready", 1000, {}),
        ("loop_model_start", 1000, {"call": 1}),
        ("loop_model_first_chunk", 1600, {"call": 1}),
        ("loop_model_end", 1700, {"call": 1, "tool_calls": 1, "input_tokens": 9000, "cached_tokens": 6000}),
        ("loop_tool_start", 1700, {"tool": "read_phone_screen", "call": 1}),
        ("loop_tool_end", 2900, {"tool": "read_phone_screen", "ok": True}),
        ("loop_model_start", 2900, {"call": 2}),
        ("loop_model_first_chunk", 3300, {"call": 2}),
        ("loop_model_end", 3500, {"call": 2, "tool_calls": 0}),
        ("model_first_token", 3300, {}),
    ]:
        voice_latency.mark(stage, turn, source="worker", wall_ms=float(at), **dims)

    turns = voice_latency.group_turns(voice_latency.read_records(path))
    one = turns[str(turn)]
    assert "loop_model_start" not in one["stages"] and one["stages"]["model_first_token"] == 3300
    steps = voice_latency.loop_steps(one)
    assert [(s["kind"], s.get("ms")) for s in steps] == [("model", 700), ("tool", 1200), ("model", 600)]
    assert steps[0]["first_chunk_ms"] == 600 and steps[0]["cached_tokens"] == 6000
    metrics = voice_latency.loop_metrics(one)
    assert metrics == {"loop_model_calls": 2, "loop_model_ms": 1300, "loop_tools": 1, "loop_tool_ms": 1200,
                       "loop_first_call_wait": 600, "loop_first_input_tokens": 9000,
                       "loop_first_cached_tokens": 6000}
    summary = voice_latency.summarize(turns)
    assert summary["loop_tool_ms"]["p50"] == 1200 and summary["model_ttft"]["p50"] == 2300


# --------------------------------------------------------------------------- #
# A request that opens the same way from one turn to the next (prompt caching)


def test_standing_prompt_first_and_the_moment_right_before_the_message():
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.ai.chat import _build_inputs
    from app.ai.context import AgentRuntimeContext

    context = AgentRuntimeContext(user_id=uuid.uuid4(), user_name="Sam", now="Tuesday 14:02",
                                  awareness="- Eyes: closed", working_memory="- a goal")
    history = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"},
               {"role": "user", "content": "second"}]
    messages = _build_inputs(history, context, [])["messages"]
    assert [type(m).__name__ for m in messages] == ["SystemMessage", "HumanMessage", "AIMessage",
                                                    "SystemMessage", "HumanMessage"]
    standing, moment = messages[0].content, messages[3].content
    assert "You are Jophiel" in standing and "You are speaking with Sam." in standing
    for volatile in ("Tuesday 14:02", "Eyes: closed", "a goal"):
        assert volatile not in standing and volatile in moment
    assert isinstance(messages[-1], HumanMessage) and messages[-1].content == "second"

    # Another moment, same standing prompt: the request's opening is unchanged.
    later = AgentRuntimeContext(user_id=context.user_id, user_name="Sam", now="Tuesday 14:09",
                                awareness="- Eyes: open", working_memory="- another goal")
    again = _build_inputs([*history, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "third"}],
                          later, [])["messages"]
    assert again[0].content == standing and again[1:3] == messages[1:3]
    assert isinstance(again[-2], SystemMessage) and "Tuesday 14:09" in again[-2].content


def test_history_window_start_holds_for_several_turns():
    from app.services.chat import HISTORY_STEP, _held_window

    keep = 24

    def rows(newest: int):
        fetched = range(max(1, newest - keep - HISTORY_STEP + 1), newest + 1)
        return [SimpleNamespace(conversation_sequence=seq) for seq in fetched]

    starts = []
    for newest in range(40, 40 + 3 * HISTORY_STEP):
        held = _held_window(rows(newest), keep)
        assert keep <= len(held) < keep + HISTORY_STEP
        assert held[-1].conversation_sequence == newest
        starts.append(held[0].conversation_sequence)
    # Over 3 * HISTORY_STEP new messages the start moves 3 times, not once a turn.
    assert sum(a != b for a, b in zip(starts, starts[1:])) == 3
    assert _held_window(rows(10), keep) == rows(10)  # a short conversation: everything
    gappy = [SimpleNamespace(conversation_sequence=seq) for seq in (1, 2, 3, 100)]
    assert _held_window(gappy, 3) == gappy[-3:]
