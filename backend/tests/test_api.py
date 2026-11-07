"""The FastAPI service: start, poll, results, list, and startup
recovery — against a copy of the real offline warehouse."""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rca_agent.api.app import create_offline_app
from rca_agent.services.recall import NoopRecall
from rca_agent.services.store import FileStore

ROOT = Path(__file__).resolve().parent.parent.parent
OFFLINE_DB = ROOT / "data" / "offline.duckdb"


def _app(tmp_path: Path) -> FastAPI:
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    return create_offline_app(
        str(db_path), str(tmp_path / "store"), str(tmp_path / "traces.duckdb")
    )


def _poll_until_done(
    client: TestClient, investigation_id: str, timeout_s: float = 20.0
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while True:
        response = client.get(f"/investigations/{investigation_id}")
        assert response.status_code == 200
        body = cast(dict[str, Any], response.json())
        if body["status"] in ("done", "failed"):
            return body
        assert time.monotonic() < deadline, f"investigation {investigation_id} did not finish"
        time.sleep(0.2)


def test_health_and_full_flow(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        started = client.post(
            "/investigations",
            json={"question": "Why did brake complaints spike for the 2021 F-150?"},
        )
        assert started.status_code == 202
        investigation_id = started.json()["investigation_id"]
        assert started.json()["status"] == "running"
        # A poll right after the start returns the investigation (it may
        # already be done: the mock is fast and the background task runs inline).
        polled = client.get(f"/investigations/{investigation_id}")
        assert polled.status_code == 200
        assert polled.json()["status"] in ("running", "done")
        body = _poll_until_done(client, investigation_id)
        assert body["status"] == "done", body.get("error")
        assert "Riverside" in body["answer"]
        assert len(body["evidence"]) == 1
        assert body["queries"][0]["rule"] == "allow"
        listed = client.get("/investigations").json()["investigations"]
        assert [item["investigation_id"] for item in listed] == [investigation_id]
        assert client.get("/investigations/nope").status_code == 404


def test_startup_recovery(tmp_path: Path) -> None:
    store = FileStore(tmp_path / "store")
    store.save_investigation(
        {"investigation_id": "stuck", "question": "Why?", "status": "running", "updated_at": "1"}
    )
    app = _app(tmp_path)
    with TestClient(app) as client:
        body = client.get("/investigations/stuck").json()
        assert body["status"] == "failed"
        assert body["error"] == "interrupted by server restart"


def test_noop_recall_returns_nothing() -> None:
    assert NoopRecall().find_similar("Why did brake complaints spike?") == []
