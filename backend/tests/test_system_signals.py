"""Signals between her parts (services/system_signals.py): only real wires, repeats folded, never raising."""
from app.services import system_signals as S


class FakeRedis:
    def __init__(self):
        self.added = []

    def xadd(self, key, fields, maxlen=None, approximate=None):
        self.added.append((key, fields))

    def expire(self, key, seconds):
        pass

    def pipeline(self, transaction=True):
        return self

    def execute(self):
        pass

    def xrevrange(self, key, count=None):
        return [(f"{i}-0".encode(), {b"from": f[b"from"] if b"from" in f else f["from"].encode(),
                                     b"to": f["to"].encode(), b"kind": f["kind"].encode(),
                                     b"at": f["at"].encode()})
                for i, (_, f) in reversed(list(enumerate(self.added)))][:count]


def setup(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(S, "_client", lambda: fake)
    monkeypatch.setattr(S, "_down_until", 0.0)
    S._recent.clear()
    return fake


def test_a_real_hand_off_is_recorded_per_user(monkeypatch):
    fake = setup(monkeypatch)
    S.emit("u1", "cognition", "mouth", "decision", "turn-1")
    assert fake.added == [("system:signals:u1", {**fake.added[0][1], "from": "cognition", "to": "mouth",
                                                 "kind": "decision", "ref": "turn-1"})]


def test_a_pair_that_is_not_a_wire_is_dropped(monkeypatch):
    fake = setup(monkeypatch)
    S.emit("u1", "mouth", "cognition", "decision")
    S.emit(None, "cognition", "mouth", "decision")
    assert fake.added == []


def test_the_same_hand_off_repeated_at_once_is_one_signal(monkeypatch):
    fake = setup(monkeypatch)
    for _ in range(5):
        S.emit("u1", "observation", "memory", "event")
    S.emit("u2", "observation", "memory", "event")
    assert len(fake.added) == 2


def test_redis_failing_is_silent_and_backs_off(monkeypatch):
    calls = []

    class Broken:
        def pipeline(self, transaction=True):
            return self

        def xadd(self, *a, **k):
            calls.append(1)

        def expire(self, *a):
            pass

        def execute(self):
            raise ConnectionError("down")
    monkeypatch.setattr(S, "_client", lambda: Broken())
    monkeypatch.setattr(S, "_down_until", 0.0)
    S._recent.clear()
    S.emit("u1", "mind", "memory", "thought")
    S.emit("u1", "mind", "hands", "tip")
    assert calls == [1]


def test_recent_reads_oldest_first(monkeypatch):
    fake = setup(monkeypatch)
    S.emit("u1", "memory", "cognition", "memory_check")
    S.emit("u1", "cognition", "mouth", "decision")
    out = S.recent("u1")
    assert [(s["from"], s["to"]) for s in out] == [("memory", "cognition"), ("cognition", "mouth")]


def test_the_wires_match_the_page():
    import pathlib
    import re
    anatomy = (pathlib.Path(__file__).resolve().parents[2] / "frontend/src/components/system/anatomy.ts").read_text()
    page = set(re.findall(r"\{ from: '(\w+)', to: '(\w+)' \}", anatomy))
    assert page == set(S.WIRES)
