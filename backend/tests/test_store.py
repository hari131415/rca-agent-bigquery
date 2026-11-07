"""Store persistence: investigations and the daily byte ledger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rca_agent.services.store import FileStore, FirestoreStore, MemoryStore


def _state(
    investigation_id: str, status: str = "done", updated_at: str = "2026-11-07T00:00:00+00:00"
) -> dict[str, Any]:
    return {
        "investigation_id": investigation_id,
        "question": "Why did brake complaints spike?",
        "status": status,
        "answer": "Riverside.",
        "updated_at": updated_at,
    }


def test_memory_store_round_trip() -> None:
    store = MemoryStore()
    store.save_investigation(_state("a"))
    store.save_investigation(_state("b", "failed", updated_at="2026-11-07T00:01:00+00:00"))
    state = store.get_investigation("a")
    assert state is not None and state["status"] == "done"
    assert store.get_investigation("missing") is None
    assert [s["investigation_id"] for s in store.list_investigations()] == ["b", "a"]
    assert store.note_bytes("2026-11-07", 500) == 500
    assert store.note_bytes("2026-11-07", 250) == 750
    assert store.daily_bytes("2026-11-07") == 750
    assert store.daily_bytes("2026-11-08") == 0


def test_file_store_round_trip(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_investigation(_state("a"))
    store.save_investigation(_state("b", "failed", updated_at="2026-11-07T00:01:00+00:00"))
    state = store.get_investigation("a")
    assert state is not None and state["status"] == "done"
    assert store.get_investigation("missing") is None
    assert [s["investigation_id"] for s in store.list_investigations()] == ["b", "a"]
    assert store.note_bytes("2026-11-07", 500) == 500
    assert store.daily_bytes("2026-11-07") == 500
    # A new FileStore over the same directory sees the same data.
    reopened = FileStore(tmp_path / "store")
    reopened_state = reopened.get_investigation("a")
    assert reopened_state is not None and reopened_state["status"] == "done"
    assert reopened.daily_bytes("2026-11-07") == 500


class _FakeSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data

    @property
    def exists(self) -> bool:
        return self._data is not None

    def to_dict(self) -> dict[str, Any] | None:
        return self._data


class _FakeDocument:
    def __init__(self, store: dict[str, Any], doc_id: str) -> None:
        self._store = store
        self._doc_id = doc_id

    def set(self, data: dict[str, Any]) -> None:
        self._store[self._doc_id] = data

    def get(self) -> _FakeSnapshot:
        return _FakeSnapshot(self._store.get(self._doc_id))


class _FakeCollection:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeDocument:
        return _FakeDocument(self._store, doc_id)

    def stream(self) -> list[_FakeSnapshot]:
        return [_FakeSnapshot(data) for data in self._store.values()]


class _FakeFirestoreClient:
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))


def test_firestore_store_with_fake_client() -> None:
    store = FirestoreStore(client=_FakeFirestoreClient())
    store.save_investigation(_state("a"))
    state = store.get_investigation("a")
    assert state is not None and state["status"] == "done"
    assert store.get_investigation("missing") is None
    assert [s["investigation_id"] for s in store.list_investigations()] == ["a"]
    assert store.note_bytes("2026-11-07", 500) == 500
    assert store.note_bytes("2026-11-07", 250) == 750
    assert store.daily_bytes("2026-11-07") == 750
