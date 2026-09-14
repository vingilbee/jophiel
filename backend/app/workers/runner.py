"""Run one independent thread per job type within a single worker process."""
from collections.abc import Callable
import logging
import signal
import threading

import redis

from app.core.config import get_settings
from app.workers import chat, file_deletion, memory

logger = logging.getLogger(__name__)


def _work_loop(name: str, run_once: Callable[[], bool], queue: str, stop: threading.Event) -> None:
    settings = get_settings()
    interval = max(1, int(getattr(settings, "chat_worker_poll_seconds", settings.memory_worker_poll_seconds)))
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
        for name, callback, queue in (
            ("chat", chat.run_once, getattr(settings, "chat_queue_name", "macrovision:chat:jobs")),
            ("memory", memory.run_once, settings.memory_queue_name),
            ("file-deletion", file_deletion.run_once, settings.file_deletion_queue_name),
        ):
            thread = threading.Thread(
                target=_work_loop, args=(name, callback, queue, stop), name=f"worker-{name}",
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
