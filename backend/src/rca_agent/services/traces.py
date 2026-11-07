"""Trace logging: one record per step of an investigation.

A trace carries what the investigation did and what it cost —
the prompt, the tool call (the SQL), the dry-run bytes, the
result size, the tokens, the cost, and the latency — so a run can
be audited without re-running it. No secrets or credentials are
logged: prompts never contain them (the model key lives in the
client, not the prompt).

- DuckDBTraceStore: a `traces` table in a DuckDB file, the offline
  default (a separate file from the warehouse, so re-seeding the
  warehouse never wipes the traces).
- BigQueryTraceStore: the `traces` table in the BigQuery dataset,
  the live store (google-cloud-bigquery, imported lazily; the
  client can be injected for tests). Written now, first exercised
  against real BigQuery in Phase 8. The live traces dataset is
  `rca_ops`, not the warehouse dataset `rca_agent`: the runner
  service account has dataEditor on `rca_ops` and only dataViewer
  on `rca_agent`, so traces are the only thing it can write — and
  the SQL allow-list stays `rca_agent` only, so the agent can never
  query (or write to) the traces table. See infra/terraform/main.tf.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Protocol

import duckdb

TRACE_COLUMNS = [
    ("investigation_id", "VARCHAR"),
    ("step", "VARCHAR"),
    ("prompt", "VARCHAR"),
    ("sql", "VARCHAR"),
    ("dry_run_bytes", "BIGINT"),
    ("result_rows", "BIGINT"),
    ("tokens_in", "BIGINT"),
    ("tokens_out", "BIGINT"),
    ("cost_usd", "DOUBLE"),
    ("latency_ms", "DOUBLE"),
    ("created_at", "VARCHAR"),
]


class TraceStore(Protocol):
    def log(self, trace: dict[str, Any]) -> None: ...


def new_trace(investigation_id: str, step: str) -> dict[str, Any]:
    """A trace with the required fields filled in; the caller adds
    the step's measurements."""
    return {
        "investigation_id": investigation_id,
        "step": step,
        "prompt": None,
        "sql": None,
        "dry_run_bytes": None,
        "result_rows": None,
        "tokens_in": None,
        "tokens_out": None,
        "cost_usd": None,
        "latency_ms": None,
        "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


class DuckDBTraceStore:
    """Traces in a DuckDB file (the offline store)."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        columns = ", ".join(f"{name} {kind}" for name, kind in TRACE_COLUMNS)
        con = duckdb.connect(db_path)
        try:
            con.execute(f"CREATE TABLE IF NOT EXISTS traces ({columns})")
        finally:
            con.close()

    def log(self, trace: dict[str, Any]) -> None:
        con = duckdb.connect(self.db_path)
        try:
            columns = ", ".join(name for name, _ in TRACE_COLUMNS)
            placeholders = ", ".join("?" for _ in TRACE_COLUMNS)
            con.execute(
                f"INSERT INTO traces ({columns}) VALUES ({placeholders})",
                [trace.get(name) for name, _ in TRACE_COLUMNS],
            )
        finally:
            con.close()

    def list_traces(self, investigation_id: str) -> list[dict[str, Any]]:
        con = duckdb.connect(self.db_path, read_only=True)
        try:
            columns = ", ".join(name for name, _ in TRACE_COLUMNS)
            rows = con.execute(
                f"SELECT {columns} FROM traces WHERE investigation_id = ? ORDER BY created_at",
                [investigation_id],
            ).fetchall()
            return [dict(zip([name for name, _ in TRACE_COLUMNS], row)) for row in rows]
        finally:
            con.close()


class BigQueryTraceStore:
    """Traces in the BigQuery `traces` table (the live store)."""

    def __init__(self, project: str, dataset: str, client: Any = None) -> None:
        self.project = project
        self.dataset = dataset
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            from google.cloud import bigquery  # noqa: PLC0415

            self._client = bigquery.Client(project=self.project)
        return self._client

    def log(self, trace: dict[str, Any]) -> None:
        table = f"{self.project}.{self.dataset}.traces"
        errors = self.client.insert_rows_json(table, [trace])
        if errors:
            raise RuntimeError(f"failed to write trace: {errors}")
