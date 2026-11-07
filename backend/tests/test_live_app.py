"""The live app wiring: create_live_app builds the app over the
Vertex LLM, the BigQuery warehouse, the Firestore store, and the
BigQuery trace store — with the real Google clients replaced by
fakes (no GCP credentials in CI). The point is the wiring: the
right components, the right datasets, and an investigation that
runs end to end.
"""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rca_agent.api.app import create_live_app
from rca_agent.llm.mock import MockLLM
from rca_agent.sql.duckdb import DuckDBWarehouse
from rca_agent.sql.warehouse import DryRunResult


class _FakeFirestoreClient:
    """A minimal fake for the Firestore client library."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self, name)


class _FakeCollection:
    def __init__(self, client: _FakeFirestoreClient, name: str) -> None:
        self.client = client
        self.name = name

    def document(self, doc_id: str) -> _FakeDocument:
        return _FakeDocument(self.client, self.name, doc_id)

    def stream(self) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(id=doc_id, to_dict=lambda d=d: d)
            for (collection, doc_id), d in self.client.docs.items()
            if collection == self.name
        ]


class _FakeDocument:
    def __init__(self, client: _FakeFirestoreClient, collection: str, doc_id: str) -> None:
        self.client = client
        self.collection = collection
        self.doc_id = doc_id

    def set(self, data: dict[str, Any]) -> None:
        self.client.docs[(self.collection, self.doc_id)] = data

    def get(self) -> SimpleNamespace:
        data = self.client.docs.get((self.collection, self.doc_id))
        return SimpleNamespace(exists=data is not None, to_dict=lambda: data)


class _FakeBigQueryClient:
    """A fake BigQuery client: dry runs report 0 bytes, queries run
    against a DuckDB warehouse so the SQL is real."""

    def __init__(self, duckdb_warehouse: DuckDBWarehouse) -> None:
        self.duckdb_warehouse = duckdb_warehouse
        self.inserted: list[dict[str, Any]] = []

    def query(self, sql: str, job_config: Any = None) -> SimpleNamespace:
        if job_config is not None and getattr(job_config, "dry_run", False):
            return SimpleNamespace(total_bytes_processed=0)
        # Real BigQuery rows are mapping-like (dict(row) works); a
        # plain dict behaves the same, so return the DuckDB rows as-is.
        rows = self.duckdb_warehouse.execute(sql)
        return SimpleNamespace(result=lambda: rows)

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.inserted.extend(rows)
        return []


def _live_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[FastAPI, _FakeBigQueryClient, _FakeFirestoreClient]:
    """Build the live app with every Google client faked."""
    import shutil

    from rca_agent.sql.duckdb import DuckDBWarehouse

    db_path = tmp_path / "offline.duckdb"
    shutil.copy(Path(__file__).resolve().parent.parent.parent / "data" / "offline.duckdb", db_path)
    duck = DuckDBWarehouse(str(db_path))
    bq_client = _FakeBigQueryClient(duck)
    fs_client = _FakeFirestoreClient()

    # The BigQuery warehouse and trace store build their client
    # lazily; point them at the fake. The Vertex LLM is replaced
    # by the mock (same interface) so no model is called. The
    # warehouse's dry_run is patched too: it imports
    # google.cloud.bigquery to build the QueryJobConfig, and that
    # library is not installed in CI (the gcp extra is only
    # installed for the live run).
    monkeypatch.setattr(
        "rca_agent.sql.bigquery.BigQueryWarehouse.dry_run",
        lambda self, sql: DryRunResult(bytes_processed=0, valid=True),
    )
    monkeypatch.setattr("rca_agent.sql.bigquery.BigQueryWarehouse._client", lambda self: bq_client)
    monkeypatch.setattr(
        "rca_agent.services.traces.BigQueryTraceStore.client",
        property(lambda self: bq_client),
    )
    monkeypatch.setattr(
        "rca_agent.services.store.FirestoreStore.client",
        property(lambda self: fs_client),
    )
    monkeypatch.setattr("rca_agent.api.app.VertexLLM", lambda **kwargs: MockLLM())
    app = create_live_app(
        project="test-project",
        region="us-central1",
        dataset="rca_agent",
        traces_dataset="rca_ops",
        firestore_database="rca-agent",
        vertex_model_id="gemini-3.8-flash",
    )
    return app, bq_client, fs_client


def test_live_app_health_and_investigation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app, bq_client, fs_client = _live_app(monkeypatch, tmp_path)
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    response = client.post(
        "/investigations", json={"question": "Why did brake complaints spike for the 2021 F-150?"}
    )
    assert response.status_code == 202
    investigation_id = response.json()["investigation_id"]
    state = None
    for _ in range(100):
        state = client.get(f"/investigations/{investigation_id}").json()
        if state["status"] == "done":
            break
        time.sleep(0.1)
    assert state is not None and state["status"] == "done", state
    assert "Riverside" in state["answer"]
    assert state["verdict"]["entity"] == "acme-brakes"
    # The verdict is ungrounded (the plant-rate query has no
    # supplier column) — the grounding check marks it.
    assert state["verdict_unverified"] is True
    # Traces went to the (fake) BigQuery traces table.
    assert bq_client.inserted, "no traces were written"
    assert all(row["investigation_id"] == investigation_id for row in bq_client.inserted)
    # The investigation was persisted in the (fake) Firestore store.
    assert ("investigations", investigation_id) in fs_client.docs
