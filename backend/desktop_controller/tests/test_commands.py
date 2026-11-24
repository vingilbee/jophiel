"""Commands on her own machine (desktop_controller.commands, the run_command action): output as text, bounded in
time and size, never interactive, stopped with what it started."""
import sys
import threading
import time

import pytest

from desktop_controller.commands import CommandRunner, clean, keep
from desktop_controller.desktop import DesktopHands, parse_action, Refused
from desktop_controller.link import COMMAND_OUTPUT_CHARS, DesktopResult
from desktop_controller.machine import command_shell

posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell commands")


def hands():
    return DesktopHands(None, None, commands=CommandRunner(command_shell()))


@posix
def test_output_and_exit_code_come_back_as_text():
    result, jpeg = hands().run("a1", {"kind": "run_command", "command": "echo hello; echo oops >&2; exit 3"})
    assert jpeg is None and result.status == "done" and result.exit_code == 3
    assert "hello" in result.output and "oops" in result.output and not result.output_cut


@posix
def test_a_command_past_its_time_is_stopped_with_what_it_started():
    started = time.monotonic()
    result, _ = hands().run("a2", {"kind": "run_command", "command": "echo begun; sleep 30 & sleep 30",
                                   "timeout_s": 1})
    assert time.monotonic() - started < 8
    assert result.status == "failed" and result.exit_code is None and "still running after 1 s" in result.reason
    assert "begun" in result.output  # what it said before it was stopped


@posix
def test_a_closing_connection_stops_it():
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    result, _ = hands().run("a3", {"kind": "run_command", "command": "sleep 30", "timeout_s": 60}, stop)
    assert result.status == "failed" and "connection closed" in result.reason


@posix
def test_input_is_closed_so_nothing_waits_for_a_password():
    result, _ = hands().run("a4", {"kind": "run_command", "command": "read answer; echo got:$answer", "timeout_s": 5})
    assert result.status == "done" and "got:" in result.output


@posix
def test_long_output_keeps_its_start_and_end():
    result, _ = hands().run("a5", {"kind": "run_command", "command": "seq 1 200000", "timeout_s": 20})
    assert result.output_cut and result.output.startswith("1\n") and result.output.rstrip().endswith("200000")
    assert len(result.output) <= COMMAND_OUTPUT_CHARS + 200
    DesktopResult.model_validate_json(result.model_dump_json())  # it fits the wire


def test_colours_and_redrawn_lines_are_cleaned():
    assert clean("\x1b[31mred\x1b[0m\n10%\r50%\r100%\n") == "red\n100%\n"
    text, cut = keep("a" * 50)
    assert text == "a" * 50 and not cut


def test_without_a_runner_commands_are_refused():
    result, _ = DesktopHands(None, None).run("a6", {"kind": "run_command", "command": "ls"})
    assert result.status == "refused" and "can't run commands" in result.reason


def test_a_command_is_checked_like_any_action():
    with pytest.raises(Refused):
        parse_action({"kind": "run_command", "command": ""})
    with pytest.raises(Refused):
        parse_action({"kind": "run_command", "command": "ls", "timeout_s": 10_000})
