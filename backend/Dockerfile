# RCA agent API image. Build context is the repo root:
#   gcloud builds submit --config cloudbuild.yaml .
# or via docker (see infra/terraform/README.md).
#
# The entrypoint (serve.py) picks the wiring from the env vars:
# with GCP_PROJECT and VERTEX_MODEL_ID set it serves the live app
# (BigQuery warehouse, Firestore store, BigQuery traces, Vertex
# LLM); without them it serves the offline app (DuckDB warehouse,
# FileStore, DuckDB traces). The gcp extra is installed so the
# live wiring imports. The offline warehouse is copied in so the
# container is self-contained for the offline path and the smoke
# test.
FROM python:3.12-slim
WORKDIR /app

COPY backend/pyproject.toml ./
COPY backend/src/ ./src/
RUN pip install --no-cache-dir ".[gcp]"

COPY data/offline.duckdb ./data/offline.duckdb

ENV RCA_DB=/app/data/offline.duckdb \
    RCA_STORE_DIR=/app/data/api-store \
    RCA_TRACES_DB=/app/data/traces.duckdb

EXPOSE 8000
CMD ["python", "-m", "rca_agent.serve"]
