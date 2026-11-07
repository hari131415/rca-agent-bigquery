"""Trace logging: the DuckDB store offline and the BigQuery store
against a fake client (real BigQuery is Phase 8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

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

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.rows.append((table, rows))
        return []


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
