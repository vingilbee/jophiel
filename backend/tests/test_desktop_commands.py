"""Commands on her desktop, backend side (run_command): offered only by a controller that can run them, what she
reads back, what's recorded (trail, capture, observation, System page), the prompt, and her desktop turns'
reasoning. No database, no network."""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.ai import agent
from app.ai.context import AgentRuntimeContext
from app.ai.observe import registry
from app.ai.prompts import build_system_prompt
from app.ai.tools.desktop_control import build_desktop_control_tools
from app.ai.tools.desktop_control.tools import command_text
from app.services import desktop_control, mind_desktop, system_parts
from app.services.desktop_control import DesktopAccess, Outcome, TurnRef
from tests.test_mind_desktop import FakeRedis


def outcome(status="done", **kw):
    now = datetime.now(timezone.utc)
    return Outcome(uuid.uuid4().hex, status, started_at=now, ended_at=now, **kw)


def names(tools):
    return [t.name for t in tools]


def test_run_command_is_offered_only_when_the_controller_can_run_commands():
    uid = uuid.uuid4()
    assert "run_command" not in names(build_desktop_control_tools(uid))
    assert "run_command" in names(build_desktop_control_tools(uid, commands=True))


def test_access_reads_the_controllers_report():
    access = DesktopAccess(uuid.uuid4(), "Kali", True, "allowed", False, os_name="Linux 7.1.5+kali-arm64",
                           commands=True)
    assert "running Linux 7.1.5+kali-arm64" in access.prompt_line() and "Windows" not in access.prompt_line()
    assert "a computer of your own)" in DesktopAccess(uuid.uuid4(), "VM", True, "allowed", False).prompt_line()


def test_for_turn_says_commands_only_when_usable_and_reported(monkeypatch):
    # (its session never connects: access_for is stubbed, and a commit with nothing to commit sends nothing)
    for access, expected in (
            (DesktopAccess(uuid.uuid4(), "Kali", True, "allowed", False, commands=True), True),
            (DesktopAccess(uuid.uuid4(), "Kali", False, "allowed", False, commands=True), False),  # offline
            (DesktopAccess(uuid.uuid4(), "Kali", True, "allowed", False), False)):  # an older controller
        monkeypatch.setattr(desktop_control, "access_for", lambda db, uid, a=access: a)
        assert desktop_control.for_turn(uuid.uuid4())[2] is expected
    monkeypatch.setattr(desktop_control, "access_for", lambda db, uid: None)
    assert desktop_control.for_turn(uuid.uuid4()) == (None, False, False)


def test_what_she_reads_back_has_the_output_and_never_overclaims():
    text = command_text(outcome(exit_code=0, output="Nmap done: 256 IP addresses (9 hosts up)\n"), "nmap -sn x")
    assert text.startswith("Ran `nmap -sn x`: exit code 0.") and "9 hosts up" in text
    cut = command_text(outcome(exit_code=0, output="a\n[... cut ...]\nz", output_cut=True), "seq")
    assert "cut in the middle" in cut
    stopped = command_text(outcome("failed", reason="it was still running after 60 s, so it was stopped",
                                   output="Starting Nmap"), "nmap -O x")
    assert stopped.startswith("Not finished: it was still running after 60 s") and "Starting Nmap" in stopped
    assert "Nothing ran" in command_text(outcome("refused", reason="the user has your view switched off"), "ls")
    assert "check before running it again" in command_text(outcome("unconfirmed"), "ls")


def test_a_command_waits_as_long_as_its_own_limit():
    assert desktop_control.wait_seconds({"kind": "run_command", "timeout_s": 300}) >= 305
    assert desktop_control.wait_seconds({"kind": "click"}) < 10


def test_the_prompt_sends_terminal_work_to_run_command_only_when_she_has_it():
    uid, line = uuid.uuid4(), DesktopAccess(uuid.uuid4(), "Kali", True, "allowed", False).prompt_line()
    without = build_system_prompt(AgentRuntimeContext(user_id=uid, desktop=line), [])
    assert "## Your desktop" in without and "run_command" not in without
    with_it = build_system_prompt(AgentRuntimeContext(user_id=uid, desktop=line, desktop_commands=True), [])
    assert "Terminal work goes through `run_command`" in with_it


def test_a_command_is_recorded_everywhere_it_should_be(monkeypatch):
    fake = FakeRedis()
    from app.services import desktop_preview, learning_capture, observation
    monkeypatch.setattr(desktop_preview, "redis_client", lambda: fake)
    captured, reports = [], []
    monkeypatch.setattr(learning_capture, "execution", lambda **kw: captured.append(kw))
    monkeypatch.setattr(observation, "record", lambda db, report: reports.append(report))

    class Db:
        def begin_nested(self):
            return self

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def commit(self):
            pass

        def rollback(self):
            pass

    uid = uuid.uuid4()
    done = outcome(exit_code=0, output="Nmap scan report for 192.168.1.76\nHost is up.\n")
    desktop_control.remember(Db(), uid, tool="run_command", intended={"command": "nmap -sn 192.168.1.0/24",
                                                                      "timeout_s": 120},
                             outcome=done, turn=TurnRef(uuid.uuid4(), uuid.uuid4(), uuid.uuid4()), step=3)
    # The owner-only capture keeps the command and its output.
    executed = captured[0]["executed"]
    assert captured[0]["intended"]["command"] == "nmap -sn 192.168.1.0/24"
    assert executed["exit_code"] == 0 and "192.168.1.76" in executed["output"]
    # Memory's observation says that she ran one, not what (nor what it printed).
    payload = reports[0].payload
    assert payload["summary"] == "ran a command (exit code 0)" and "nmap" not in str(payload)
    assert registry.normalize("desktop", "desktop_action", payload).summary.startswith("On her desktop")
    # Her background mind's trail has the command and how its output ended.
    entry = desktop_control.trail(uid)[0]
    assert entry["what"] == "ran `nmap -sn 192.168.1.0/24`" and entry["exit_code"] == 0
    line = mind_desktop._line(entry, entry["at"])
    assert "exit code 0" in line and "its output ended: Nmap scan report for 192.168.1.76 | Host is up." in line
    # The System page shows the exit code and what it printed.
    row = SimpleNamespace(id=uuid.uuid4(), intended={"tool": "run_command", **captured[0]["intended"]},
                          executed=executed, result="succeeded", ended_at=done.ended_at, captured_at=done.ended_at,
                          deviation=None, turn_id=None)
    shown = system_parts._desktop_out(row)
    assert shown["exit_code"] == 0 and "192.168.1.76" in shown["output"]


def test_desktop_turns_think_before_each_step_and_others_dont(monkeypatch):
    agent.get_chat_model.cache_clear()
    try:
        plain = agent.get_chat_model("gpt-6-luna")
        assert plain.reasoning_effort == "none" and not plain.use_responses_api
        # Tools with reasoning are refused on Chat Completions: a reasoning turn goes through the Responses API,
        # its reasoning passed back encrypted between calls, nothing stored.
        thinking = agent.get_chat_model("gpt-6.2-sol", "low")
        assert thinking.use_responses_api and thinking.reasoning == {"effort": "low"}
        assert thinking.include == ["reasoning.encrypted_content"] and thinking.store is False
        assert thinking.reasoning_effort is None
    finally:
        agent.get_chat_model.cache_clear()


@pytest.mark.parametrize("tool", ["run_command", "keyboard"])
def test_observation_accepts_her_desktop_tools(tool):
    payload = {"action_id": "a" * 32, "tool": tool, "status": "done", "summary": "ran a command (exit code 1)"}
    assert registry.normalize("desktop", "desktop_action", payload).spec.consumers == ("memory", "learning")


@pytest.mark.parametrize("desktop, override, expected", [
    ("Your desktop ...", None, ("gpt-6-luna", "low")),  # her mind on her desktop: luna, thinking low
    (None, None, (None, None)),  # an ordinary turn stays on the quick model, no reasoning
    ("Your desktop ...", "gpt-4o-mini", ("gpt-4o-mini", "low")),  # an explicit override (a nudge) wins
])
def test_her_desktop_turns_run_on_a_reasoning_model(monkeypatch, desktop, override, expected):
    import asyncio
    from app.ai import chat

    built = []
    monkeypatch.setattr(chat, "build_agent", lambda tools, model=None, pre_model_hook=None, reasoning=None:
                        built.append((model, reasoning)) or "agent")
    monkeypatch.setattr(chat, "_resolve_connections", lambda context: {})
    monkeypatch.setattr(chat, "get_agent", lambda model=None: built.append((model, None)) or "agent")
    context = AgentRuntimeContext(user_id=uuid.uuid4(), desktop=desktop, desktop_usable=True,
                                  model_override=override, memory_tools=False)

    async def run():
        async with chat._orchestrator_for(context) as (agent_, specs):
            return agent_

    asyncio.run(run())
    assert built[-1] == expected


@pytest.mark.parametrize("game, idle, event, override, expected", [
    ("You are playing Minecraft ...", False, None, None, ("gpt-6-luna", "low")),  # in a game: luna, thinking low
    ("You are not playing right now.", True, None, None, (None, None)),  # a paired game, not playing: quick model
    (None, False, "event", None, ("gpt-6-luna", "low")),  # a game event woke her
    ("You are playing Minecraft ...", False, None, "gpt-4o-mini", ("gpt-4o-mini", None)),  # an override wins
])
def test_her_game_turns_run_on_a_reasoning_model(monkeypatch, game, idle, event, override, expected):
    import asyncio
    from app.ai import chat
    from app.ai.context import GameEvent

    built = []
    monkeypatch.setattr(chat, "build_agent", lambda tools, model=None, pre_model_hook=None, reasoning=None:
                        built.append((model, reasoning)) or "agent")
    monkeypatch.setattr(chat, "build_desktop_tools", lambda *a, **k: [])
    monkeypatch.setattr(chat, "_resolve_connections", lambda context: {})
    monkeypatch.setattr(chat, "get_agent", lambda model=None: built.append((model, None)) or "agent")
    context = AgentRuntimeContext(user_id=uuid.uuid4(), game=game, game_idle=idle, model_override=override,
                                  game_event=GameEvent.__new__(GameEvent) if event else None, memory_tools=False)

    async def run():
        async with chat._orchestrator_for(context) as (agent_, specs):
            return agent_

    asyncio.run(run())
    assert built[-1] == expected
