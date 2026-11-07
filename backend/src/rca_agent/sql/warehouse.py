"""Warehouse protocol: the two things the guardrails need.

BigQuery and DuckDB both implement `dry_run` + `execute`. The
agent only ever writes BigQuery SQL; the DuckDB implementation
transpiles it (see dialect.py). The units differ by design: a
BigQuery dry run reports bytes scanned, a DuckDB dry run reports
nothing (DuckDB has no byte billing) and only validates that the
query plans. The guardrails enforce the byte cap only when the
warehouse reports bytes, and the row cap in both modes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class DryRunResult:
    """Outcome of a dry run: what the query would cost, if known."""

    bytes_processed: int | None  # None when the warehouse cannot say
    valid: bool = True
    error: str | None = None


class Warehouse(Protocol):
    """A queryable warehouse behind the guardrails."""

    name: str

    def dry_run(self, sql: str) -> DryRunResult:
        """Validate the query and report what it would scan, if known."""
        ...

    def execute(self, sql: str) -> list[dict[str, Any]]:
        """Run the (already guarded) query and return rows as dicts."""
        ...
