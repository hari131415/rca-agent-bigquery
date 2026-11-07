# RCA agent API image. Build context is the repo root:
#   docker build -f backend/Dockerfile -t <image> .
# or via Cloud Build (see infra/terraform/README.md).
#
# The image runs the offline API (DuckDB warehouse, FileStore,
# DuckDB traces) — the wiring scripts/run_api.py and serve.py share.
# The warehouse is copied in so the container is self-contained.
# Phase 8 replaces the entrypoint with the live wiring (BigQuery,
# Firestore, Vertex LLM); the image stays the same.
FROM python:3.12-slim
WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/src/ ./src/
RUN pip install --no-cache-dir .

COPY data/offline.duckdb ./data/offline.duckdb

ENV RCA_DB=/app/data/offline.duckdb \
    RCA_STORE_DIR=/app/data/api-store \
    RCA_TRACES_DB=/app/data/traces.duckdb

EXPOSE 8000
CMD ["python", "-m", "rca_agent.serve"]
