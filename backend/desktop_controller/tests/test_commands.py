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


# --- Background commands (0.14.0+) -------------------------------------------------------------------------------


def background_hands(**kwargs):
    h = DesktopHands(None, None, commands=CommandRunner(command_shell(), **kwargs))
    h.jobs_reported = True
    return h


@posix
def test_a_background_command_keeps_running_and_says_when_it_ends():
    h = background_hands()
    ended = threading.Event()
    h.commands.on_finished = lambda job: ended.set()
    result, _ = h.run("b1", {"kind": "run_command", "command": "echo begun; sleep 1.5; echo finished; exit 4",
                             "timeout_s": 1, "background": True})
    assert result.status == "done" and result.running is True and result.exit_code is None
    assert "begun" in result.output and result.job
    looked, _ = h.run("b2", {"kind": "command_status", "job": result.job})
    assert looked.running is True
    assert ended.wait(10)
    [job] = h.commands.unreported()
    assert job.exit_code == 4 and "finished" in job.output()[0]
    done, _ = h.run("b3", {"kind": "command_status", "job": result.job})
    assert done.status == "done" and done.running is False and done.exit_code == 4
    # On the wire, a result without a job leaves the new fields out (an older relay forbids them).
    plain, _ = h.run("b4", {"kind": "run_command", "command": "echo quick", "timeout_s": 5, "background": True})
    assert plain.job is None and plain.exit_code == 0
    assert "job" not in plain.wire() and "running" not in plain.wire()
    assert '"job"' in done.wire()


@posix
def test_a_background_command_can_be_stopped_and_is_bounded():
    h = background_hands(max_background_s=1)
    result, _ = h.run("b5", {"kind": "run_command", "command": "sleep 30", "timeout_s": 1, "background": True})
    capped = h.commands.status(result.job)
    deadline = time.time() + 10
    while capped.running and time.time() < deadline:
        time.sleep(0.1)
    assert not capped.running and capped.exit_code is None and "still running after" in capped.why

    h = background_hands()
    result, _ = h.run("b6", {"kind": "run_command", "command": "sleep 30", "timeout_s": 1, "background": True})
    stopped, _ = h.run("b7", {"kind": "stop_command", "job": result.job})
    assert stopped.status == "done"
    job = h.commands.status(result.job)
    deadline = time.time() + 10
    while job.running and time.time() < deadline:
        time.sleep(0.1)
    assert job.why == "you stopped it" and job.exit_code is None
    missing, _ = h.run("b8", {"kind": "command_status", "job": "nope1234"})
    assert missing.status == "refused" and "no background command" in missing.reason


@posix
def test_background_needs_a_relay_that_hears_the_end():
    h = DesktopHands(None, None, commands=CommandRunner(command_shell()))
    result, _ = h.run("b9", {"kind": "run_command", "command": "echo x", "background": True})
    assert result.status == "refused" and "relay is too old" in result.reason


@posix
def test_at_most_four_background_commands_at_once():
    h = background_hands()
    for i in range(4):
        result, _ = h.run(f"c{i}", {"kind": "run_command", "command": "sleep 20", "timeout_s": 1,
                                    "background": True})
        assert result.running
    fifth, _ = h.run("c5", {"kind": "run_command", "command": "sleep 20", "timeout_s": 1, "background": True})
    assert fifth.status == "failed" and "already running" in fifth.reason
    for job in list(h.commands.jobs):
        h.commands.stop_job(job)
