"""Run one independent thread per job type within a single worker process."""
from collections.abc import Callable
import logging
import signal
import threading

import redis

from app.core.config import get_settings
from app.workers import agent_events, chat, cognitive_shadow, desktop, dialogue_memory, file_deletion, memory, whatsapp

logger = logging.getLogger(__name__)


def _work_loop(
    name: str, run_once: Callable[[], bool], queue: str, stop: threading.Event,
    interval_seconds: float | None = None,
) -> None:
    settings = get_settings()
    configured_interval = (
        interval_seconds
        if interval_seconds is not None
        else getattr(settings, "chat_worker_poll_seconds", settings.memory_worker_poll_seconds)
    )
    interval = max(1, int(configured_interval))
    wakeups = redis.from_url(
        settings.redis_url, socket_connect_timeout=interval, socket_timeout=interval + 2,
    ) if settings.redis_url else None
    logger.info("%s worker thread started", name)
    try:
        while not stop.is_set():
            try:
                # run_once creates/closes its own DB sessions in this thread.
                worked = run_once()
            except Exception:
                logger.exception("%s job processing failed; will retry", name)
                stop.wait(interval)
                continue
            if stop.is_set():
                break
            if worked:
                continue
            if wakeups is not None:
                try:
                    # Each thread listens only to its own queue. PostgreSQL
                    # polling recovers lost notifications after restarts.
                    wakeups.blpop(queue, timeout=interval)
                    continue
                except Exception:
                    logger.warning("%s Redis wake-up failed; polling PostgreSQL", name)
            stop.wait(interval)
    finally:
        if wakeups is not None:
            wakeups.close()
        logger.info("%s worker thread stopped", name)


def run(stop: threading.Event | None = None) -> None:
    """Start both workers; SIGINT/SIGTERM stop new claims and drain active jobs."""
    logging.basicConfig(level=logging.INFO)
    install_signals = stop is None and threading.current_thread() is threading.main_thread()
    stop = stop if stop is not None else threading.Event()
    settings = get_settings()
    previous = {}
    if install_signals:
        def request_stop(_signum, _frame):
            stop.set()
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, request_stop)
    threads = []
    try:
        worker_specs = [
            ("chat", chat.run_once, getattr(settings, "chat_queue_name", "macrovision:chat:jobs"), None),
            ("memory", memory.run_once, settings.memory_queue_name, None),
            ("file-deletion", file_deletion.run_once, settings.file_deletion_queue_name, None),
        ]
        if getattr(settings, "whatsapp_enabled", False):
            worker_specs.append((
                "whatsapp", whatsapp.run_once,
                getattr(settings, "whatsapp_queue_name", "macrovision:whatsapp:wakeups"),
                getattr(settings, "whatsapp_worker_poll_seconds", 1.0),
            ))
        if getattr(settings, "cognitive_live_shadow_enabled", False) and getattr(
            settings, "cognitive_live_shadow_run_in_shared_worker", True
        ):
            # A separate thread and queue: shadow cognition never shares the
            # chat thread, so its CPU work cannot sit in front of a reply.
            cognitive_shadow.check_semantic_parser_configuration(settings)
            cognitive_shadow.check_realization_configuration(settings)
            worker_specs.append((
                "cognitive-shadow", cognitive_shadow.run_once,
                getattr(settings, "cognitive_live_shadow_queue_name", "macrovision:cognitive-shadow:wakeups"),
                getattr(settings, "cognitive_live_shadow_worker_poll_seconds", 2.0),
            ))
        if getattr(settings, "cognitive_dialogue_memory_enabled", False):
            # Its own thread and queue: dialogue state updates never sit in
            # front of a chat reply or a shadow observation.
            worker_specs.append((
                "dialogue-memory", dialogue_memory.run_once,
                getattr(settings, "cognitive_dialogue_memory_queue_name", "macrovision:dialogue-memory:wakeups"),
                getattr(settings, "cognitive_dialogue_memory_worker_poll_seconds", 2.0),
            ))
        if getattr(settings, "desktop_agent_enabled", False):
            # The gameplay driver: its own thread (and one thread per playing
            # session), so a model call never sits in front of a chat reply.
            worker_specs.append(("desktop-driver", desktop.run_once, "macrovision:desktop:wakeups", 1.0))
            # Columbina waking on game events: its own thread, so a background
            # turn never sits in front of a reply (and vice versa).
            worker_specs.append(("agent-events", agent_events.run_once, "macrovision:agent-events:wakeups", 1.0))
        for name, callback, queue, interval in worker_specs:
            thread = threading.Thread(
                target=_work_loop, args=(name, callback, queue, stop, interval), name=f"worker-{name}",
            )
            thread.start()
            threads.append(thread)
        while not stop.wait(0.2):
            if any(not thread.is_alive() for thread in threads):
                raise RuntimeError("A worker thread exited unexpectedly")
    finally:
        stop.set()
        for thread in threads:
            thread.join()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
