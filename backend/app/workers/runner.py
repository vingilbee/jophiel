"""Run one independent thread per job type within a single worker process."""
from collections.abc import Callable
import logging
import signal
import threading

import redis

from app.core.config import get_settings
from app.workers import (
    agent_events, chat, desktop, file_deletion, game_learning, game_practice, learning,
    memory, mind, recall_embeddings, observation,
    whatsapp,
)

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
                if name in ("coordinator", "observation"):
                    from app.services import coordinator_runtime
                    wake = coordinator_runtime.wake if name == "coordinator" else coordinator_runtime.capture_wake
                    wake.clear()
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
            if name in ("coordinator", "observation"):
                wake.wait(interval)
            else:
                stop.wait(interval)
    finally:
        if name == "coordinator":
            from app.services.coordinator_runtime import close
            close()
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
    # Load the live Student 1 in the background (only when enabled), so the first chat turn doesn't wait.
    from app.services import cognitive_live
    threading.Thread(target=cognitive_live.warm, name="cognitive-live-warm", daemon=True).start()
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
        if getattr(settings, "desktop_agent_enabled", False):
            # The gameplay driver: its own thread (and one thread per playing
            # session), so a model call never sits in front of a chat reply.
            worker_specs.append(("desktop-driver", desktop.run_once, "macrovision:desktop:wakeups", 1.0))
            # Columbina waking on game events: its own thread, so a background
            # turn never sits in front of a reply (and vice versa).
            worker_specs.append(("agent-events", agent_events.run_once, "macrovision:agent-events:wakeups", 1.0))
            # Reflection on ended game sessions: rare, slow model calls on their own thread.
            worker_specs.append(("game-learning", game_learning.run_once, settings.game_learning_queue_name, 5.0))
            # Teaching herself a move (stage H1): one attempt at a time, minutes each, own thread.
            worker_specs.append(("game-practice", game_practice.run_once, settings.game_practice_queue_name, 2.0))
        # Embedding memory events for recall: background, batched; recall works before it catches up.
        worker_specs.append(("recall-embeddings", recall_embeddings.run_once,
                             "macrovision:recall-embeddings:wakeups", 5.0))
        # Learning capture upkeep: training examples from captured decisions, raw captures past retention.
        worker_specs.append(("learning", learning.run_once, "macrovision:learning:wakeups", 30.0))
        # What sources report (conversation, deliveries, games), captured once.
        worker_specs.append(("observation", observation.run_once, "macrovision:observation:wakeups", 1.0))
        # Background recall and thinking for user requests (shared-state pipeline, step 4) and autonomous
        # proposals (step 5): always on, since every reply goes through the pipeline. Its own thread, so a slow
        # thought never sits in front of a reply.
        from app.workers import coordinator
        worker_specs.append(("coordinator", coordinator.run_once, settings.coordinator_queue_name,
                             getattr(settings, "coordinator_heartbeat_seconds", 1.0)))
        # Her mind: a tick about once a second over every source (services/mind.py; Minecraft only with
        # desktop mode, where it also records its ticks), on its own thread so it never delays a reply or her hands.
        worker_specs.append(("mind", mind.run_once, "macrovision:mind:wakeups", 1.0))
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
