"""Independent progress, retry, and shutdown of worker threads."""
from threading import Event, Thread, current_thread
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.workers import runner


@pytest.fixture
def settings(monkeypatch):
    config = SimpleNamespace(
        redis_url=None, memory_worker_poll_seconds=1,
        memory_queue_name="test:memory", file_deletion_queue_name="test:deletion",
        coordinator_queue_name="test:coordinator",
    )
    monkeypatch.setattr(runner, "get_settings", lambda: config)
    # The coordinator thread always runs (the shared-state pipeline is the only preparation); keep it idle.
    from app.workers import coordinator
    monkeypatch.setattr(coordinator, "run_once", lambda: False)
    return config


@pytest.mark.parametrize("busy_queue", ["memory", "deletion"])
def test_one_busy_queue_does_not_block_the_other(settings, monkeypatch, busy_queue):
    stop, busy, release, other_finished = Event(), Event(), Event(), Event()
    thread_names = set()

    def slow_job():
        thread_names.add(current_thread().name)
        busy.set()
        release.wait(5)
        return True

    def other_job():
        assert busy.wait(3)
        thread_names.add(current_thread().name)
        other_finished.set()
        stop.set()
        return True

    monkeypatch.setattr(runner.memory, "run_once", slow_job if busy_queue == "memory" else other_job)
    monkeypatch.setattr(runner.file_deletion, "run_once", other_job if busy_queue == "memory" else slow_job)
    supervisor = Thread(target=runner.run, args=(stop,))
    supervisor.start()
    try:
        assert other_finished.wait(3), "Other queue waited for the busy job"
        assert supervisor.is_alive(), "Shutdown should let the busy job finish"
    finally:
        stop.set()
        release.set()
        supervisor.join(5)
    assert not supervisor.is_alive()
    assert thread_names == {"worker-memory", "worker-file-deletion"}


def test_failed_job_is_retried_without_stopping_loop(settings):
    stop = Event()
    calls = []

    def job():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("temporary failure")
        stop.set()
        return True

    thread = Thread(target=runner._work_loop, args=("memory", job, "test:memory", stop))
    thread.start()
    thread.join(4)
    stop.set()
    thread.join(2)
    assert not thread.is_alive()
    assert len(calls) == 2


@pytest.mark.parametrize("queue", ["test:memory", "test:deletion"])
def test_worker_waits_only_on_its_own_queue_and_closes_client(settings, monkeypatch, queue):
    settings.redis_url = "redis://test.invalid"
    stop = Event()
    client = Mock()
    client.blpop.side_effect = lambda *args, **kwargs: stop.set()
    monkeypatch.setattr(runner.redis, "from_url", Mock(return_value=client))
    runner._work_loop("test", lambda: False, queue, stop)
    client.blpop.assert_called_once_with(queue, timeout=1)
    client.close.assert_called_once()
