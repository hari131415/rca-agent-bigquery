"""BigQuery warehouse: the live implementation.

WRITTEN BUT NOT EXECUTED: there is no GCP project or credential
until Phase 8, so no method here has ever run against real
BigQuery. The dry run is a real BigQuery dry-run job (no data is
read, no cost is incurred beyond the free dry-run itself) and its
total_bytes_processed is what the guardrails enforce the byte cap
against. Imports are lazy so importing this module never requires
google-cloud-bigquery to be installed.
"""

from __future__ import annotations

from typing import Any

from .warehouse import DryRunResult


class BigQueryWarehouse:
    """A Warehouse backed by BigQuery (live mode, Phase 8)."""

    name = "bigquery"

    def __init__(self, project: str, dataset: str):
        self.project = project
        self.dataset = dataset

    def _client(self) -> Any:
        from google.cloud import bigquery  # noqa: PLC0415 - lazy: no GCP until Phase 8

        return bigquery.Client(project=self.project)

    def dry_run(self, sql: str) -> DryRunResult:
        from google.cloud import bigquery  # noqa: PLC0415 - lazy: no GCP until Phase 8

        try:
            job = self._client().query(sql, job_config=bigquery.QueryJobConfig(dry_run=True))
        except Exception as exc:  # noqa: BLE001 - surfaced as the verdict reason
            return DryRunResult(bytes_processed=None, valid=False, error=str(exc))
        total = job.total_bytes_processed
        return DryRunResult(bytes_processed=int(total) if total is not None else None, valid=True)

    def execute(self, sql: str) -> list[dict[str, Any]]:
        rows = self._client().query(sql).result()
        return [dict(row) for row in rows]
