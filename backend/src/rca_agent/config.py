"""Settings for the RCA agent, read from the environment.

Only the guardrail settings live here for now; the model ID, prices,
and GCP project arrive in Phase 5/Phase 8. Every setting has a
default so the offline mode works with no environment at all.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

DEFAULT_DATASET = "rca_agent"

# Tables the agent is allowed to read. The traces table is written
# by the tracer, never read by the agent, so it is not on this list.
DEFAULT_ALLOWED_TABLES: frozenset[str] = frozenset(
    {"complaints", "recalls", "plants", "lines", "suppliers", "builds"}
)


@dataclass(frozen=True)
class VertexSettings:
    """Where the Vertex AI model runs and what it costs to think.

    The model is available only on the `global` location for this
    project (us-central1 returned 404 in the first live run,
    2026-11-08), so the default is global, not a region. The
    location is separate from the Cloud Run region: the service
    runs in us-central1 and calls the global model endpoint.
    """

    location: str = "global"
    model_id: str = "gemini-3.8-flash"
    thinking_budget: int | None = None

    @classmethod
    def from_env(cls) -> VertexSettings:
        budget = os.environ.get("VERTEX_THINKING_BUDGET")
        return cls(
            location=os.environ.get("VERTEX_LOCATION", "global"),
            model_id=os.environ.get("VERTEX_MODEL_ID", "gemini-3.8-flash"),
            thinking_budget=int(budget) if budget else None,
        )


@dataclass(frozen=True)
class GuardrailSettings:
    """Caps and allow-lists for SQL the agent may run.

    max_result_rows is a RESULT-SIZE cap: it bounds the rows a query
    may return. It does NOT bound cost — BigQuery bills bytes
    scanned, and a LIMIT 10 query over the whole complaints table
    still scans the whole table. max_bytes_per_query (enforced on
    the dry run, before the job starts) is the real cost control.
    See docs/guardrails.md.
    """

    dataset: str = DEFAULT_DATASET
    allowed_tables: frozenset[str] = field(default=DEFAULT_ALLOWED_TABLES)
    max_result_rows: int = 1_000
    max_bytes_per_query: int = 1_000_000_000  # 1 GB scanned per query
    max_queries_per_investigation: int = 20
    max_bytes_per_day: int = 10_000_000_000  # 10 GB scanned per day

    @classmethod
    def from_env(cls) -> GuardrailSettings:
        # The Terraform sets the caps in GB (RCA_MAX_BYTES_PER_QUERY_GB
        # / RCA_MAX_BYTES_PER_DAY_GB); a local run may set them in
        # bytes (RCA_MAX_BYTES_PER_QUERY / RCA_MAX_BYTES_PER_DAY).
        # The GB vars win when both are set.
        max_bytes_per_query = int(os.environ.get("RCA_MAX_BYTES_PER_QUERY", "1000000000"))
        if "RCA_MAX_BYTES_PER_QUERY_GB" in os.environ:
            max_bytes_per_query = int(
                float(os.environ["RCA_MAX_BYTES_PER_QUERY_GB"]) * 1_000_000_000
            )
        max_bytes_per_day = int(os.environ.get("RCA_MAX_BYTES_PER_DAY", "10000000000"))
        if "RCA_MAX_BYTES_PER_DAY_GB" in os.environ:
            max_bytes_per_day = int(float(os.environ["RCA_MAX_BYTES_PER_DAY_GB"]) * 1_000_000_000)
        return cls(
            dataset=os.environ.get("RCA_DATASET", DEFAULT_DATASET),
            allowed_tables=frozenset(
                table.strip().lower()
                for table in os.environ.get(
                    "RCA_ALLOWED_TABLES", ",".join(sorted(DEFAULT_ALLOWED_TABLES))
                ).split(",")
                if table.strip()
            ),
            max_result_rows=int(os.environ.get("RCA_MAX_RESULT_ROWS", "1000")),
            max_bytes_per_query=max_bytes_per_query,
            max_queries_per_investigation=int(
                os.environ.get("RCA_MAX_QUERIES_PER_INVESTIGATION", "20")
            ),
            max_bytes_per_day=max_bytes_per_day,
        )
