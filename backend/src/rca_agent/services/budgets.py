"""Byte and query budgets: the cost controls behind the guardrails.

Two limits, both enforced before a query runs:

- Per-investigation query cap: how many SQL attempts one
  investigation may make (including refused ones — a refused
  attempt still cost the agent a try, and the self-correct loop
  must not spin forever).
- Daily byte cap: total bytes scanned across all investigations
  in one day. Bytes are reserved at dry-run time, before the
  query executes, so two queries cannot double-spend the same
  budget.

The daily bytes live in a ledger. The in-memory ledger is the
default; Phase 5 persists it through the Store (Firestore
`budgets` collection live, a budgets file offline) so a server
restart does not reset the day's spend. The accounting rules
stay here; only the storage changes.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .store import Store


def format_bytes(n: int) -> str:
    """One consistent unit for byte counts: decimal GB, exact bytes in parens."""
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:g} GB ({n:,} bytes)"
    return f"{n:,} bytes"


class DailyLedger(Protocol):
    def get(self, day: str) -> int: ...
    def add(self, day: str, byte_count: int) -> int: ...


class MemoryLedger:
    """In-memory daily byte totals."""

    def __init__(self) -> None:
        self._totals: dict[str, int] = {}

    def get(self, day: str) -> int:
        return self._totals.get(day, 0)

    def add(self, day: str, byte_count: int) -> int:
        self._totals[day] = self._totals.get(day, 0) + byte_count
        return self._totals[day]


class StoreLedger:
    """Daily byte totals persisted through the Store."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def get(self, day: str) -> int:
        return self.store.daily_bytes(day)

    def add(self, day: str, byte_count: int) -> int:
        return self.store.note_bytes(day, byte_count)


@dataclass
class BudgetTracker:
    """Per-investigation query counts + a (possibly persistent) daily byte ledger."""

    max_queries_per_investigation: int
    max_bytes_per_day: int
    day: str = field(default_factory=lambda: _dt.date.today().isoformat())
    ledger: DailyLedger = field(default_factory=MemoryLedger)
    _queries: dict[str, int] = field(default_factory=dict)

    def query_count(self, investigation_id: str) -> int:
        return self._queries.get(investigation_id, 0)

    def note_attempt(self, investigation_id: str) -> int:
        """Count one attempt for the investigation; returns the new count.

        Called for every check, allowed or refused: a refused attempt
        still cost the agent a try, and the self-correct loop must
        not spin forever on bad SQL.
        """
        self._queries[investigation_id] = self.query_count(investigation_id) + 1
        return self._queries[investigation_id]

    def check_query_cap(self, investigation_id: str) -> str | None:
        """Reason the investigation may not make another attempt, or None."""
        used = self.query_count(investigation_id)
        if used >= self.max_queries_per_investigation:
            return (
                f"investigation has used {used} of "
                f"{self.max_queries_per_investigation} allowed queries"
            )
        return None

    def check_daily_cap(self, bytes_to_add: int) -> str | None:
        """Reason the day's byte budget cannot cover this query, or None."""
        used = self.ledger.get(self.day)
        if used + bytes_to_add > self.max_bytes_per_day:
            return (
                f"daily byte budget exceeded: {format_bytes(used + bytes_to_add)} "
                f"would exceed the {format_bytes(self.max_bytes_per_day)} cap"
            )
        return None

    def reserve_bytes(self, bytes_used: int) -> None:
        """Reserve bytes for a query that passed the checks."""
        self.ledger.add(self.day, bytes_used)

    @property
    def bytes_today(self) -> int:
        return self.ledger.get(self.day)
