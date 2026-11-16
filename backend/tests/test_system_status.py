"""The System page's part states, from worker beats (services/system_status.py)."""
from app.services import system_status as S

NOW = 1_000_000.0


def view(**entry):
    return S._worker_view("w", {"interval": 1.0, **entry}, NOW)


def test_a_worker_never_seen_is_down():
    assert S._worker_view("w", None, NOW)["state"] == "down"


def test_a_recent_beat_is_alive_and_an_old_one_is_down():
    assert view(seen=NOW - 2)["state"] == "alive"
    assert view(seen=NOW - 60)["state"] == "down"


def test_a_long_job_keeps_the_worker_alive_until_the_busy_limit():
    assert view(seen=NOW - 300, busy_since=NOW - 300)["state"] == "busy"
    assert view(seen=NOW - S.BUSY_LIMIT - 1, busy_since=NOW - S.BUSY_LIMIT - 1)["state"] == "down"


def test_a_recent_error_makes_it_failing_and_an_old_one_does_not():
    failing = view(seen=NOW - 1, error_at=NOW - 10, error="ValueError: x")
    assert failing["state"] == "failing" and failing["error"] == "ValueError: x"
    assert view(seen=NOW - 1, error_at=NOW - S.ERROR_WINDOW - 1, error="old")["state"] == "alive"


def test_part_state_combines_its_workers():
    w = lambda state: {"state": state}  # noqa: E731
    assert S._part_state([]) == "off"
    assert S._part_state([w("alive"), w("busy")]) == "busy"
    assert S._part_state([w("alive"), w("down")]) == "partial"
    assert S._part_state([w("down"), w("down")]) == "down"
    assert S._part_state([w("alive"), w("failing")]) == "failing"


def test_status_without_redis_says_it_cannot_tell(monkeypatch):
    monkeypatch.setattr(S, "_client", lambda: None)
    out = S.status()
    assert out["known"] is False
    assert [p["id"] for p in out["parts"]] == ["mouth", "mind", "hands", "memory", "observation",
                                               "learning"]
    assert all(p["state"] in ("down", "off") for p in out["parts"])


def test_beat_never_raises_when_redis_fails(monkeypatch):
    class Broken:
        def hset(self, *a, **k):
            raise ConnectionError("down")
    monkeypatch.setattr(S, "_client", lambda: Broken())
    S.beat("test-worker", interval=1.0, busy=True)
    S.beat("test-worker", interval=1.0, error="boom")
