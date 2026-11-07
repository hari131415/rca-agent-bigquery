"""Container entrypoint: run the RCA agent API.

Reads the same env vars as scripts/run_api.py (RCA_DB, RCA_STORE_DIR,
RCA_TRACES_DB, RCA_PORT) and serves the offline app (DuckDB warehouse,
FileStore, DuckDB traces) by default. When the GCP env vars are set
(GCP_PROJECT, VERTEX_MODEL_ID), it serves the live app instead
(BigQuery warehouse, Firestore store, BigQuery traces, Vertex LLM) —
the image and the entrypoint stay the same, only the wiring changes.

Port: Cloud Run sets PORT and defaults to 8080 when the service
does not declare a port, so PORT wins, then RCA_PORT (local), then
8000. The Terraform sets container_port = 8000 to match.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from rca_agent.api.app import create_live_app, create_offline_app

ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _port() -> int:
    # Cloud Run sets PORT; a local run sets RCA_PORT; 8000 is the default.
    return int(os.environ.get("PORT", os.environ.get("RCA_PORT", "8000")))


def main() -> None:
    if os.environ.get("GCP_PROJECT") and os.environ.get("VERTEX_MODEL_ID"):
        app = create_live_app(
            project=os.environ["GCP_PROJECT"],
            region=os.environ.get("GCP_REGION", "us-central1"),
            dataset=os.environ.get("BIGQUERY_DATASET", "rca_agent"),
            traces_dataset=os.environ.get("BIGQUERY_TRACES_DATASET", "rca_ops"),
            firestore_database=os.environ.get("FIRESTORE_DATABASE", "rca-agent"),
            vertex_model_id=os.environ["VERTEX_MODEL_ID"],
        )
    else:
        app = create_offline_app(
            os.environ.get("RCA_DB", str(ROOT / "data" / "offline.duckdb")),
            os.environ.get("RCA_STORE_DIR", str(ROOT / "data" / "api-store")),
            os.environ.get("RCA_TRACES_DB", str(ROOT / "data" / "traces.duckdb")),
        )
    uvicorn.run(app, host="0.0.0.0", port=_port())


if __name__ == "__main__":
    main()
