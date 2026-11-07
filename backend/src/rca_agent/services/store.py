"""Persistence for investigations and the daily byte ledger.

A Store is the API's memory: it holds investigation states (the
full state dict) and the per-day byte totals. Three implementations:

- MemoryStore: in-memory, for tests.
- FileStore: one JSON file per investigation plus a budgets file —
  the offline default (no cloud, survives a restart).
- FirestoreStore: the live store (google-cloud-firestore, imported
  lazily so the offline path never needs the library; the emulator
  is honored through FIRESTORE_EMULATOR_HOST).

States are stored as JSON. DuckDB rows can carry dates and
decimals, so values are converted on the way in (dates to ISO
strings, decimals to floats).
"""

from __future__ import annotations

import datetime as _dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast


def to_jsonable(value: Any) -> Any:
    """Convert a value to something json.dumps accepts."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    return str(value)


class Store(Protocol):
    def save_investigation(self, state: dict[str, Any]) -> None: ...
    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None: ...
    def list_investigations(self) -> list[dict[str, Any]]: ...
    def note_bytes(self, day: str, byte_count: int) -> int: ...
    def daily_bytes(self, day: str) -> int: ...


class MemoryStore:
    """In-memory store, for tests."""

    def __init__(self) -> None:
        self._investigations: dict[str, dict[str, Any]] = {}
        self._budgets: dict[str, int] = {}

    def save_investigation(self, state: dict[str, Any]) -> None:
        self._investigations[str(state["investigation_id"])] = json.loads(
            json.dumps(to_jsonable(state))
        )

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        return self._investigations.get(investigation_id)

    def list_investigations(self) -> list[dict[str, Any]]:
        return sorted(
            self._investigations.values(),
            key=lambda s: s.get("updated_at", ""),
            reverse=True,
        )

    def note_bytes(self, day: str, byte_count: int) -> int:
        self._budgets[day] = self._budgets.get(day, 0) + byte_count
        return self._budgets[day]

    def daily_bytes(self, day: str) -> int:
        return self._budgets.get(day, 0)


class FileStore:
    """One JSON file per investigation plus a budgets file."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, investigation_id: str) -> Path:
        return self.directory / f"{investigation_id}.json"

    def _budgets_path(self) -> Path:
        return self.directory / "budgets.json"

    def save_investigation(self, state: dict[str, Any]) -> None:
        self._path(str(state["investigation_id"])).write_text(
            json.dumps(to_jsonable(state)), encoding="utf-8"
        )

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        path = self._path(investigation_id)
        if not path.exists():
            return None
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))

    def list_investigations(self) -> list[dict[str, Any]]:
        states = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in self.directory.glob("*.json")
            if path.name != "budgets.json"
        ]
        return sorted(states, key=lambda s: s.get("updated_at", ""), reverse=True)

    def note_bytes(self, day: str, byte_count: int) -> int:
        budgets = self._read_budgets()
        budgets[day] = budgets.get(day, 0) + byte_count
        self._budgets_path().write_text(json.dumps(budgets), encoding="utf-8")
        return budgets[day]

    def daily_bytes(self, day: str) -> int:
        return self._read_budgets().get(day, 0)

    def _read_budgets(self) -> dict[str, int]:
        path = self._budgets_path()
        if not path.exists():
            return {}
        return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


class FirestoreStore:
    """The live store: collections `investigations` and `budgets`.

    The client is created lazily (google.cloud.firestore is only
    imported here) and can be injected for tests. The emulator is
    honored by the client library through FIRESTORE_EMULATOR_HOST.
    """

    def __init__(self, database: str = "rca-agent", client: Any = None) -> None:
        self.database = database
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import firestore  # noqa: PLC0415

            self._client = firestore.Client(database=self.database)
        return self._client

    def save_investigation(self, state: dict[str, Any]) -> None:
        self.client.collection("investigations").document(str(state["investigation_id"])).set(
            to_jsonable(state)
        )

    def get_investigation(self, investigation_id: str) -> dict[str, Any] | None:
        snapshot = self.client.collection("investigations").document(investigation_id).get()
        if not snapshot.exists:
            return None
        return cast(dict[str, Any], snapshot.to_dict())

    def list_investigations(self) -> list[dict[str, Any]]:
        states = [
            snapshot.to_dict() for snapshot in self.client.collection("investigations").stream()
        ]
        return sorted(states, key=lambda s: s.get("updated_at", ""), reverse=True)

    def note_bytes(self, day: str, byte_count: int) -> int:
        doc = self.client.collection("budgets").document(day)
        snapshot = doc.get()
        total = (snapshot.to_dict() or {}).get("bytes", 0) if snapshot.exists else 0
        total += byte_count
        doc.set({"bytes": total})
        return total

    def daily_bytes(self, day: str) -> int:
        snapshot = self.client.collection("budgets").document(day).get()
        if not snapshot.exists:
            return 0
        return cast(int, (snapshot.to_dict() or {}).get("bytes", 0))
