"""DuckDB warehouse: the offline stand-in for BigQuery.

Executes BigQuery SQL by transpiling it (dialect.py) and running
it against a local DuckDB file. The dry run is EXPLAIN: it proves
the query parses and plans, but reports no byte count, because
DuckDB has no byte billing. The byte cap therefore does not apply
offline; the row cap (LIMIT) does, in both modes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .dialect import to_duckdb
from .warehouse import DryRunResult


class DuckDBWarehouse:
    """A Warehouse backed by a DuckDB file (offline mode)."""

    name = "duckdb"

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def dry_run(self, sql: str) -> DryRunResult:
        import duckdb

        try:
            con = duckdb.connect(str(self.db_path), read_only=True)
            try:
                con.execute(f"EXPLAIN {to_duckdb(sql)}").fetchall()
            finally:
                con.close()
        except Exception as exc:  # noqa: BLE001 - surfaced as the verdict reason
            return DryRunResult(bytes_processed=None, valid=False, error=str(exc))
        return DryRunResult(bytes_processed=None, valid=True)

    def execute(self, sql: str) -> list[dict[str, Any]]:
        import duckdb

        con = duckdb.connect(str(self.db_path), read_only=True)
        try:
            cursor = con.execute(to_duckdb(sql))
            columns = [description[0] for description in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            con.close()
