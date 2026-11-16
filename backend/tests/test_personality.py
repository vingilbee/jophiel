from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.services import personality


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(personality, "_snapshot", None)


def install_rows(monkeypatch, rows):
    loads = []

    @contextmanager
    def session():
        loads.append(1)
        yield SimpleNamespace(scalars=lambda statement: SimpleNamespace(all=lambda: rows))

    monkeypatch.setattr(personality, "SessionLocal", session)
    return loads


def test_workers_share_one_immutable_text_snapshot(monkeypatch):
    row = SimpleNamespace(name="jophiel", version=4, profile="  Curious and kind.  ")
    loads = install_rows(monkeypatch, [row])
    with ThreadPoolExecutor(max_workers=8) as pool:
        snapshots = list(pool.map(lambda _: personality.current(), range(30)))
    assert loads == [1]
    assert all(snapshot is snapshots[0] for snapshot in snapshots)
    assert snapshots[0].text == "Curious and kind."
    row.profile = "Changed in the database."
    assert personality.current().text == "Curious and kind."


@pytest.mark.parametrize("rows", [[], [SimpleNamespace(), SimpleNamespace()],
                                  [SimpleNamespace(profile="  ")]])
def test_missing_ambiguous_or_empty_personality_does_not_cache(monkeypatch, rows):
    install_rows(monkeypatch, rows)
    with pytest.raises(RuntimeError):
        personality.current()
    assert personality._snapshot is None
