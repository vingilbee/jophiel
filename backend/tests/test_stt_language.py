"""A misheard language must not reach the agent (it would answer in kind)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services import stt

PCM = b"\x01\x00" * 16000  # 1s at 16 kHz


@pytest.mark.parametrize(("text", "wrong"), [
    ("喂", True), ("웨이", True), ("待って", True), ("Wait.", False),
    ("How much protein is in 豆腐?", False), ("", False), ("...", False),
])
def test_is_wrong_script(text: str, wrong: bool) -> None:
    assert stt.is_wrong_script(text) is wrong


def _fake_client(monkeypatch, replies: dict[str, str]):
    calls: list[dict] = []

    async def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=replies[kwargs["model"]])

    client = SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create)))
    monkeypatch.setattr(stt, "_client", lambda api_key: client)
    return calls


def test_wrong_script_retries_with_whisper(monkeypatch) -> None:
    calls = _fake_client(monkeypatch, {"gpt-4o-mini-transcribe": "喂", "whisper-1": "Wait."})
    monkeypatch.setattr(stt.get_settings(), "stt_model", "gpt-4o-mini-transcribe")
    assert asyncio.run(stt.transcribe(PCM, 16000)) == "Wait."
    assert [c["model"] for c in calls] == ["gpt-4o-mini-transcribe", "whisper-1"]
    assert all(c["language"] == "en" and c["prompt"] for c in calls)


def test_still_wrong_script_becomes_silence(monkeypatch) -> None:
    _fake_client(monkeypatch, {"gpt-4o-mini-transcribe": "웨이", "whisper-1": "웨이"})
    monkeypatch.setattr(stt.get_settings(), "stt_model", "gpt-4o-mini-transcribe")
    assert asyncio.run(stt.transcribe(PCM, 16000)) == ""


def test_english_is_not_retried(monkeypatch) -> None:
    calls = _fake_client(monkeypatch, {"gpt-4o-mini-transcribe": "Wait, what about rice?"})
    monkeypatch.setattr(stt.get_settings(), "stt_model", "gpt-4o-mini-transcribe")
    assert asyncio.run(stt.transcribe(PCM, 16000)) == "Wait, what about rice?"
    assert len(calls) == 1
