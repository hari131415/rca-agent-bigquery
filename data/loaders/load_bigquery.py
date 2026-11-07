"""Load data into BigQuery.

NOT EXECUTED in Phase 2: there is no GCP project or credential yet.
The DDL in data/schemas/bigquery.sql is validated offline by parsing
it with sqlglot (see backend/tests/test_schemas.py); this module runs
for the first time in Phase 8, after the user sets up the project and
approves the cost estimate.

Loads are full refreshes (WRITE_TRUNCATE), not incremental: the NHTSA
files are re-downloaded per load and the synthetic data is
regenerated from a fixed seed, so a re-run reproduces the same
tables. The loader never invents credentials; it uses Application
Default Credentials from the environment.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "bigquery.sql"
DEFAULT_DATASET = "rca_agent"


def render_ddl(dataset: str) -> str:
    """Return the DDL with the dataset name substituted."""
    return SCHEMA_PATH.read_text().replace(f"`{DEFAULT_DATASET}.", f"`{dataset}.")


def apply_ddl(project: str, dataset: str) -> None:
    """Create the dataset and tables. Requires ADC with BigQuery access."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    client.create_dataset(dataset, exists_ok=True)
    for statement in render_ddl(dataset).split(";"):
        statement = statement.strip()
        if statement:
            client.query(statement).result()


def load_jsonl(project: str, dataset: str, table: str, rows: list[dict[str, object]]) -> int:
    """Load rows into a table via a temporary JSONL file (full refresh)."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    table_ref = f"{project}.{dataset}.{table}"
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str) + "\n")
        tmp_path = handle.name
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    with Path(tmp_path).open("rb") as source:
        job = client.load_table_from_file(source, table_ref, job_config=job_config)
    job.result()
    Path(tmp_path).unlink()
    return len(rows)
