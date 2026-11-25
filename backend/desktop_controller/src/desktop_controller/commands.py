"""Commands on her own machine: one shell command at a time, its output back as text (0.13.0+).

Terminal work through the screen means typing, then reading the answer off screenshots that soon fall out of
view; a command's output as text stays with her for the whole turn. This module runs one command and says what
became of it:

- **Bounded.** A time limit (``timeout_s``, at most ``MAX_TIMEOUT_S``); past it, or once the connection closes
  (``stop``), the command and everything it started are stopped. The output kept is at most
  ``COMMAND_OUTPUT_CHARS``: its start and its end, cut in the middle (``output_cut``).
- **Never interactive.** Input is closed (a command waiting for a password or a confirmation gets end-of-file and
  stops rather than hanging), and colours and pagers are turned off, so the text is the text.
- **Its own process group**, so stopping it stops what it started too.
- **Or kept running** (``run_background``, 0.14.0+): a command still running when its wait ends isn't stopped. It
  becomes a job (``Job``, a short id) that keeps going, at most ``MAX_JOBS`` at once and ``MAX_BACKGROUND_S`` each
  (then it's stopped), and survives the connection closing. When it ends, ``on_finished`` is called (the connection
  sends a ``CommandDone``; one that ends while no relay is connected is sent at the next connection). Its output is
  read the same way, so ``status`` gives what it has printed so far, or all of it once it ended. Finished jobs are
  kept ``KEEP_FINISHED_S``.

``CommandRunner.run`` never raises: a command that can't start is ``Failed``.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from .link import COMMAND_OUTPUT_CHARS

MAX_COMMAND = 4000
MAX_TIMEOUT_S = 600
HEAD_CHARS = 3000  # of a long output, its start (the command's header, early errors) and the rest from its end
READ_CHUNK = 4096
POLL_S = 0.2
MAX_JOBS = 4
MAX_BACKGROUND_S = 4 * 3600
KEEP_FINISHED_S = 3600
# Escape sequences (colours, cursor moves) and control characters other than tab and newline.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[@-_]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class CommandFailed(Exception):
    """It couldn't run (no shell, couldn't start)."""


def clean(text: str) -> str:
    """Output as a person would read it on a plain terminal."""
    text = text.replace("\r\n", "\n")
    # A carriage return redraws the line (progress bars): keep what it ended as.
    text = "\n".join(line.rsplit("\r", 1)[-1] for line in text.split("\n"))
    return _CONTROL.sub("", _ANSI.sub("", text))


def keep(text: str, limit: int = COMMAND_OUTPUT_CHARS) -> tuple[str, bool]:
    """At most ``limit`` characters: its start and its end, with how much was cut between them."""
    if len(text) <= limit:
        return text, False
    head = text[:HEAD_CHARS]
    tail = text[-(limit - HEAD_CHARS - 80):]
    cut = len(text) - len(head) - len(tail)
    return f"{head}\n[... {cut} characters cut ...]\n{tail}", True


class _Collected:
    """The output so far, read on a thread of its own (a full pipe would stall the command)."""

    def __init__(self, stream) -> None:
        self.parts: list[bytes] = []
        self.size = 0
        self.dropped = 0
        self._stream = stream
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        # Keep the start, and a rolling end: never more than a few times what's sent back.
        budget = COMMAND_OUTPUT_CHARS * 8
        while True:
            chunk = self._stream.read1(READ_CHUNK) if hasattr(self._stream, "read1") else self._stream.read(READ_CHUNK)
            if not chunk:
                return
            self.parts.append(chunk)
            self.size += len(chunk)
            while self.size > budget and len(self.parts) > 2:
                gone = self.parts.pop(1)
                self.size -= len(gone)
                self.dropped += len(gone)

    def text(self) -> tuple[str, bool]:
        decode = lambda parts: clean(b"".join(parts).decode("utf-8", errors="replace"))  # noqa: E731
        if not self.dropped:
            return keep(decode(self.parts))
        joined = f"{decode(self.parts[:1])}\n[... about {self.dropped} bytes cut ...]\n{decode(self.parts[1:])}"
        return keep(joined)[0], True


@dataclass
class Job:
    """A command kept running past its wait (``run_background``)."""

    id: str
    command: str
    process: subprocess.Popen = field(repr=False)
    collected: _Collected = field(repr=False)
    windows: bool
    started: float = field(default_factory=time.time)
    ended: float | None = None
    exit_code: int | None = None
    why: str | None = None  # why it didn't finish by itself (stopped: the time limit, or asked to)
    reported: bool = False
    stop_asked: bool = False

    @property
    def running(self) -> bool:
        return self.ended is None

    def output(self) -> tuple[str, bool]:
        return self.collected.text()


class CommandRunner:
    """Runs one command in ``shell`` (the program and flags; the command goes last, as one argument).
    ``on_finished``: called (on the job's own thread) when a background job ends."""

    def __init__(self, shell: list[str] | None, *, max_background_s: float = MAX_BACKGROUND_S) -> None:
        self.shell = shell
        self.max_background_s = max_background_s
        self.jobs: dict[str, Job] = {}
        self.on_finished: Callable[[Job], None] | None = None
        self._lock = threading.Lock()

    def _spawn(self, command: str) -> tuple[subprocess.Popen, _Collected, bool]:
        if not self.shell:
            raise CommandFailed("this machine has no shell to run commands in")
        env = {**os.environ, "TERM": "dumb", "NO_COLOR": "1", "PAGER": "cat", "GIT_PAGER": "cat",
               "SYSTEMD_PAGER": "", "DEBIAN_FRONTEND": "noninteractive", "PYTHONUNBUFFERED": "1"}
        windows = sys.platform == "win32"
        try:
            process = subprocess.Popen(
                [*self.shell, command], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=env, cwd=os.path.expanduser("~"),
                **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if windows else {"start_new_session": True}))
        except OSError as exc:
            raise CommandFailed(f"couldn't start it: {exc}") from None
        return process, _Collected(process.stdout), windows

    @staticmethod
    def _wait(process: subprocess.Popen, timeout_s: float, stop: threading.Event) -> str | None:
        """None once it exited; else ``"stop"`` (the connection closed) or ``"time"`` (its wait ran out)."""
        deadline = time.monotonic() + timeout_s
        while process.poll() is None:
            if stop.is_set():
                return "stop"
            if time.monotonic() >= deadline:
                return "time"
            stop.wait(POLL_S)
        return None

    def run(self, command: str, timeout_s: float, stop: threading.Event) -> tuple[int | None, str, bool, str | None]:
        """``(exit_code, output, output_cut, why_not_finished)``. ``exit_code`` None: it was stopped (the time
        limit, or the connection closed) and ``why_not_finished`` says which. Raises CommandFailed when it
        couldn't start."""
        process, collected, windows = self._spawn(command)
        ended = self._wait(process, timeout_s, stop)
        why = {"stop": "the connection closed part way; it was stopped",
               "time": f"it was still running after {int(timeout_s)} s, so it was stopped"}.get(ended or "")
        if why is not None:
            _stop_group(process, windows)
        collected.thread.join(timeout=2)
        output, cut = collected.text()
        return (process.returncode if why is None else None), output, cut, why

    def run_background(self, command: str, wait_s: float, stop: threading.Event
                       ) -> tuple[str | None, int | None, str, bool]:
        """``(job, exit_code, output, output_cut)``: like ``run``, but a command still running when ``wait_s`` ends
        (or when the connection closes) keeps running as a job (``job`` its id; the output is what it printed so
        far). Raises CommandFailed when it couldn't start, or when ``MAX_JOBS`` are already running."""
        self._forget_old()
        with self._lock:
            if sum(1 for job in self.jobs.values() if job.running) >= MAX_JOBS:
                raise CommandFailed(f"{MAX_JOBS} background commands are already running; stop or wait for one")
        process, collected, windows = self._spawn(command)
        if self._wait(process, wait_s, stop) is None:
            collected.thread.join(timeout=2)
            output, cut = collected.text()
            return None, process.returncode, output, cut
        job = Job(uuid.uuid4().hex[:8], command, process, collected, windows)
        with self._lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._watch, args=(job,), name=f"command-job-{job.id}", daemon=True).start()
        output, cut = collected.text()
        return job.id, None, output, cut

    def _watch(self, job: Job) -> None:
        try:
            job.process.wait(timeout=max(0.0, self.max_background_s - (time.time() - job.started)))
        except subprocess.TimeoutExpired:
            job.why = f"it was still running after {int(self.max_background_s // 60)} min, so it was stopped"
            _stop_group(job.process, job.windows)
        job.collected.thread.join(timeout=2)
        job.exit_code = None if job.why is not None else job.process.returncode
        job.ended = time.time()
        callback = self.on_finished
        if callback is not None:
            try:
                callback(job)
            except Exception:  # noqa: BLE001 - reported at the next connection instead
                pass

    def status(self, job_id: str) -> Job | None:
        self._forget_old()
        with self._lock:
            return self.jobs.get(job_id)

    def stop_job(self, job_id: str) -> Job | None:
        """Stop a running job (and what it started); its end is reported like any other."""
        job = self.status(job_id)
        if job is not None and job.running:
            job.stop_asked = True
            job.why = "you stopped it"
            _stop_group(job.process, job.windows)
        return job

    def unreported(self) -> list[Job]:
        """Finished jobs whose end no relay has been sent yet."""
        with self._lock:
            return [job for job in self.jobs.values() if not job.running and not job.reported]

    def _forget_old(self) -> None:
        now = time.time()
        with self._lock:
            for job_id in [j.id for j in self.jobs.values() if j.ended is not None and now - j.ended > KEEP_FINISHED_S]:
                self.jobs.pop(job_id, None)


def _stop_group(process: subprocess.Popen, windows: bool) -> None:
    """Stop the command and what it started: politely, then for sure."""
    try:
        if windows:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, timeout=5)
        else:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=2)
                return
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
