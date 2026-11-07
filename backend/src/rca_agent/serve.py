"""Container entrypoint: run the RCA agent API.

Reads the same env vars as scripts/run_api.py (RCA_DB, RCA_STORE_DIR,
RCA_TRACES_DB, RCA_PORT) and serves the offline app (DuckDB warehouse,
FileStore, DuckDB traces). Phase 8 extends this to build the live app
(BigQuery warehouse, Firestore store, BigQuery traces, Vertex LLM)
when the GCP env vars are set — the image and the entrypoint stay
the same, only the wiring changes.
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from rca_agent.api.app import create_offline_app

ROOT = Path(__file__).resolve().parent.parent.parent.parent


def main() -> None:
    app = create_offline_app(
        os.environ.get("RCA_DB", str(ROOT / "data" / "offline.duckdb")),
        os.environ.get("RCA_STORE_DIR", str(ROOT / "data" / "api-store")),
        os.environ.get("RCA_TRACES_DB", str(ROOT / "data" / "traces.duckdb")),
    )
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("RCA_PORT", "8000")))


if __name__ == "__main__":
    main()
