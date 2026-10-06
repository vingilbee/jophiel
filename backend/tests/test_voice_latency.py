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
