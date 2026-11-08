"""Trace logging: the DuckDB store offline and the BigQuery store
against a fake client (real BigQuery is Phase 8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from rca_agent.services.traces import BigQueryTraceStore, DuckDBTraceStore, new_trace


def test_duckdb_trace_store(tmp_path: Path) -> None:
    store = DuckDBTraceStore(str(tmp_path / "traces.duckdb"))
    trace = new_trace("inv-1", "sql")
    trace.update(
        {"prompt": "Write one SELECT.", "sql": "SELECT 1", "tokens_in": 10, "tokens_out": 5}
    )
    store.log(trace)
    store.log(new_trace("inv-1", "execute"))
    rows = store.list_traces("inv-1")
    assert [row["step"] for row in rows] == ["sql", "execute"]
    assert rows[0]["sql"] == "SELECT 1"
    assert rows[0]["tokens_in"] == 10
    assert store.list_traces("inv-2") == []


class _FakeBigQueryClient:
    def __init__(self) -> None:
        self.rows: list[tuple[str, list[dict[str, Any]]]] = []
        self.queries: list[tuple[str, Any]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.rows.append((table, rows))
        return []

    def query(self, sql: str, job_config: Any = None) -> Any:
        self.queries.append((sql, job_config))
        rows = [
            {"investigation_id": "inv-1", "step": "plan", "cost_usd": 0.01},
            {"investigation_id": "inv-1", "step": "sql", "cost_usd": 0.02},
        ]

        class _Result:
            def result(self) -> list[dict[str, Any]]:
                return rows

        return _Result()


def test_bigquery_trace_store_list_traces() -> None:
    """list_traces exists on the live store too — the first live
    eval crashed because it did not. Skipped where the BigQuery
    library is not installed (the gcp extra is live-only)."""
    pytest.importorskip("google.cloud.bigquery")
    client = _FakeBigQueryClient()
    store = BigQueryTraceStore("my-project", "rca_ops", client=client)
    rows = store.list_traces("inv-1")
    assert [row["step"] for row in rows] == ["plan", "sql"]
    assert sum(row["cost_usd"] for row in rows) == pytest.approx(0.03)
    sql, job_config = client.queries[0]
    assert "my-project.rca_ops.traces" in sql
    # The id travels as a query parameter, not in the SQL text.
    assert "@investigation_id" in sql
    assert "inv-1" not in sql
    params = job_config.query_parameters
    assert len(params) == 1 and params[0].value == "inv-1"


def test_bigquery_trace_store_fake_client() -> None:
    client = _FakeBigQueryClient()
    store = BigQueryTraceStore("my-project", "rca_agent", client=client)
    trace = new_trace("inv-1", "plan")
    trace["tokens_in"] = 42
    store.log(trace)
    assert len(client.rows) == 1
    table, rows = client.rows[0]
    assert table == "my-project.rca_agent.traces"
    assert rows[0]["investigation_id"] == "inv-1"
    assert rows[0]["tokens_in"] == 42


def test_bigquery_trace_store_reports_errors() -> None:
    class _FailingClient:
        def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return [{"errors": ["row 0 invalid"]}]

    store = BigQueryTraceStore("my-project", "rca_agent", client=_FailingClient())
    try:
        store.log(new_trace("inv-1", "plan"))
    except RuntimeError as exc:
        assert "failed to write trace" in str(exc)
    else:
        raise AssertionError("expected a RuntimeError")
