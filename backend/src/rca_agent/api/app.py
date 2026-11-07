"""FastAPI service: start an investigation, poll its status, read
its results, and list past investigations.

An investigation runs in the background (the graph streams its
state after each node, and each intermediate state is saved to
the Store), so a poll can watch it progress and a restart can
recover it. The service never touches GCP unless it is wired
with the Firestore store and the BigQuery trace store; the
offline wiring (DuckDB + mock LLM + file store + DuckDB traces)
needs no credentials.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException
from langgraph.graph import StateGraph
from pydantic import BaseModel, Field

from ..config import GuardrailSettings
from ..graph.graph import build_graph, run_investigation_stream
from ..graph.state import InvestigationState
from ..llm.mock import MockLLM
from ..llm.vertex import VertexLLM
from ..services.budgets import BudgetTracker, StoreLedger
from ..services.recovery import investigation_to_dict, recover_interrupted
from ..services.store import FileStore, FirestoreStore, Store
from ..services.traces import BigQueryTraceStore, DuckDBTraceStore
from ..sql.bigquery import BigQueryWarehouse
from ..sql.duckdb import DuckDBWarehouse
from ..sql.guardrails import Guardrails


def create_offline_app(db_path: str, store_dir: str, traces_path: str) -> FastAPI:
    """The offline wiring: DuckDB warehouse, mock LLM, a file store,
    and DuckDB traces. No GCP, no credentials."""
    settings = GuardrailSettings()
    store = FileStore(store_dir)
    budget = BudgetTracker(
        settings.max_queries_per_investigation,
        settings.max_bytes_per_day,
        ledger=StoreLedger(store),
    )
    guardrails = Guardrails(settings, budget)
    graph = build_graph(
        MockLLM(),
        DuckDBWarehouse(db_path),
        guardrails,
        trace_store=DuckDBTraceStore(traces_path),
    )
    return create_app(graph, store)


def create_live_app(
    project: str,
    region: str = "us-central1",
    dataset: str = "rca_agent",
    traces_dataset: str = "rca_ops",
    firestore_database: str = "rca-agent",
    vertex_model_id: str = "gemini-3.8-flash",
) -> FastAPI:
    """The live wiring: BigQuery warehouse, Vertex AI LLM, a
    Firestore store (investigations + the daily byte ledger, so a
    restart does not reset the day's spend), and BigQuery traces
    in rca_ops. All GCP access is through Application Default
    Credentials — the Cloud Run runner service account, or an
    impersonated one (see docs/phase8-runbook.md)."""
    settings = GuardrailSettings.from_env()
    store = FirestoreStore(firestore_database)
    budget = BudgetTracker(
        settings.max_queries_per_investigation,
        settings.max_bytes_per_day,
        ledger=StoreLedger(store),
    )
    guardrails = Guardrails(settings, budget)
    graph = build_graph(
        VertexLLM(project=project, location=region, model_id=vertex_model_id),
        BigQueryWarehouse(project, dataset),
        guardrails,
        trace_store=BigQueryTraceStore(project, traces_dataset),
    )
    return create_app(graph, store)


class StartRequest(BaseModel):
    question: str = Field(min_length=1)
    max_steps: int = Field(default=4, ge=1, le=10)


class StartResponse(BaseModel):
    investigation_id: str
    status: str


class InvestigationSummary(BaseModel):
    investigation_id: str
    question: str | None
    status: str | None
    created_at: str | None
    updated_at: str | None


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _run_and_save(
    graph: StateGraph[InvestigationState],
    question: str,
    investigation_id: str,
    max_steps: int,
    store: Store,
) -> None:
    """Run the investigation, saving each intermediate state."""
    created_at = _now()
    try:
        for state in run_investigation_stream(graph, question, investigation_id, max_steps):
            record = dict(state)
            record["created_at"] = created_at
            record["updated_at"] = _now()
            store.save_investigation(record)
    except Exception as exc:  # noqa: BLE001 - a failed run must be recorded, not lost
        record = store.get_investigation(investigation_id) or {}
        record.update(
            {
                "investigation_id": investigation_id,
                "question": question,
                "status": "failed",
                "error": str(exc),
                "created_at": created_at,
                "updated_at": _now(),
            }
        )
        store.save_investigation(record)


def create_app(graph: StateGraph[InvestigationState], store: Store) -> FastAPI:
    """Build the app over one (stateless) graph and one store."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
        recover_interrupted(store)
        yield

    app = FastAPI(title="RCA Agent", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/investigations", status_code=202, response_model=StartResponse)
    def start_investigation(request: StartRequest, background: BackgroundTasks) -> StartResponse:
        investigation_id = uuid.uuid4().hex
        now = _now()
        store.save_investigation(
            {
                "investigation_id": investigation_id,
                "question": request.question,
                "status": "running",
                "created_at": now,
                "updated_at": now,
            }
        )
        background.add_task(
            _run_and_save, graph, request.question, investigation_id, request.max_steps, store
        )
        return StartResponse(investigation_id=investigation_id, status="running")

    @app.get("/investigations/{investigation_id}")
    def get_investigation(investigation_id: str) -> dict[str, Any]:
        state = store.get_investigation(investigation_id)
        if state is None:
            raise HTTPException(status_code=404, detail="investigation not found")
        return investigation_to_dict(state)

    @app.get("/investigations")
    def list_investigations() -> dict[str, list[InvestigationSummary]]:
        return {
            "investigations": [
                InvestigationSummary(
                    investigation_id=str(state.get("investigation_id")),
                    question=state.get("question"),
                    status=state.get("status"),
                    created_at=state.get("created_at"),
                    updated_at=state.get("updated_at"),
                )
                for state in store.list_investigations()
            ]
        }

    return app
