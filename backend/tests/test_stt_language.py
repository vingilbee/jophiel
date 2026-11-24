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


def test_minecraft_turns_get_the_game_word_prompt(monkeypatch) -> None:
    calls = _fake_client(monkeypatch, {"gpt-4o-mini-transcribe": "We should find a sheep to make a bed."})
    monkeypatch.setattr(stt.get_settings(), "stt_model", "gpt-4o-mini-transcribe")
    assert asyncio.run(stt.transcribe(PCM, 16000, minecraft=True)) == "We should find a sheep to make a bed."
    assert "Minecraft" in calls[0]["prompt"] and "sheep" in calls[0]["prompt"]
    asyncio.run(stt.transcribe(PCM, 16000))
    assert calls[1]["prompt"] == stt.TRANSCRIBE_PROMPT


@pytest.mark.parametrize(("text", "echo"), [
    ("Sheep, wool, bed, creeper, zombie.", True),
    ("crafting table and furnace", True),
    ("Sheep.", False),  # a real one-word answer
    ("Find a sheep for the bed.", False),
    ("Stop.", False),
    # Seen live: the whole prompt sentence, and with a "context:" header.
    ("The speaker is talking in English while playing Minecraft, so they may mention sheep, wool, bed.", True),
    ("context: ###\nThe speaker is talking in English while playing", True),
    ("The speaker is talking in English.", True),
    ("Is it in the cave?", False),
    ("Can you mention the sheep to the speaker later?", False),
])
def test_prompt_echo(text: str, echo: bool) -> None:
    assert stt.is_prompt_echo(text) is echo


def test_echoed_game_prompt_becomes_silence(monkeypatch) -> None:
    _fake_client(monkeypatch, {"gpt-4o-mini-transcribe": "Sheep, wool, bed, creeper, zombie, skeleton."})
    monkeypatch.setattr(stt.get_settings(), "stt_model", "gpt-4o-mini-transcribe")
    assert asyncio.run(stt.transcribe(PCM, 16000, minecraft=True)) == ""


@pytest.mark.parametrize(("text", "fixed"), [
    ("Hey Jofiel, are you there?", "Hey Jophiel, are you there?"),
    ("Jofy what do you think?", "Jofy what do you think?"),
    ("Jo-fy-el, wait.", "Jophiel, wait."),
    ("jo fee el", "Jophiel"),
    ("Jophie, listen.", "Jofy, listen."),
    ("Hey Joffy", "Hey Jofy"),
    ("jofee?", "Jofy?"),
    ("Joffiel?", "Jophiel?"),
    ("Jophiel.", "Jophiel."),
    # Real words stay as they are.
    ("He was jovial about it.", "He was jovial about it."),
    ("Joe feels fine.", "Joe feels fine."),
    ("Josephine called.", "Josephine called."),
])
def test_fix_name(text: str, fixed: str) -> None:
    assert stt.fix_name(text) == fixed


def test_prompt_names_her(monkeypatch) -> None:
    calls = _fake_client(monkeypatch, {"gpt-4o-mini-transcribe": "Jophy, hi."})
    monkeypatch.setattr(stt.get_settings(), "stt_model", "gpt-4o-mini-transcribe")
    assert asyncio.run(stt.transcribe(PCM, 16000)) == "Jofy, hi."
    assert "Jophiel" in calls[0]["prompt"] and "Jofy" in calls[0]["prompt"]
