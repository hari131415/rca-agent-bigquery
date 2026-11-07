"""Run the RCA agent API offline (no GCP, no credentials).

Usage (from the repo root):
    backend/.venv/bin/python scripts/run_api.py [--port 8000]

Paths default to the repo's offline warehouse and to data/api-store/
and data/traces.duckdb (both gitignored). Env overrides: RCA_DB,
RCA_STORE_DIR, RCA_TRACES_DB, RCA_PORT.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the RCA agent API offline.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("RCA_PORT", "8000")))
    args = parser.parse_args()
    import uvicorn

    from rca_agent.api.app import create_offline_app

    app = create_offline_app(
        os.environ.get("RCA_DB", str(ROOT / "data" / "offline.duckdb")),
        os.environ.get("RCA_STORE_DIR", str(ROOT / "data" / "api-store")),
        os.environ.get("RCA_TRACES_DB", str(ROOT / "data" / "traces.duckdb")),
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
