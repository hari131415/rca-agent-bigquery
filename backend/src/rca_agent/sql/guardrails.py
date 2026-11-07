"""Guardrails: the single gate between the agent's SQL and a warehouse.

Every query attempt goes through `Guardrails.check` (or `execute`,
which checks then runs). The checks run in order and the first
failure is the verdict:

1. Parse + allow-list (sql/parse.py): one SELECT, allow-listed
   tables, LIMIT enforced.
2. Per-investigation query cap (services/budgets.py).
3. Dry run on the warehouse: the query must plan, and (live only)
   report the bytes it would scan.
4. Per-query byte cap: the dry-run bytes must fit. LIMIT does not
   help here — a LIMIT 10 query over the whole complaints table
   still scans the whole table, because BigQuery bills bytes
   scanned, not rows returned. The byte cap is the cost control.
5. Daily byte cap: the day's ledger must cover the query.

The verdict is a structured object (Verdict) and every attempt —
allowed or refused — is appended to `attempts`, in order. Phase 5
persists those records to the traces table; until then the list is
the record. Nothing here prompts the model or trusts it: the code
decides whether the SQL runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..config import GuardrailSettings
from ..services.budgets import BudgetTracker, format_bytes
from .parse import check_sql
from .warehouse import Warehouse


@dataclass(frozen=True)
class Verdict:
    """One guardrail decision, recorded with the query attempt."""

    allowed: bool
    rule: str  # stable id: allow, parse-error, not-select, byte-cap, ...
    reason: str  # human-readable, names the construct or the numbers
    sql: str  # the SQL as submitted
    rewritten_sql: str | None = None  # after LIMIT injection/capping
    bytes_scanned: int | None = None  # dry-run bytes, None offline
    limit_applied: int | None = None
    investigation_id: str | None = None
    attempt: int = 0  # per-investigation attempt number


class Guardrails:
    """Stateless on the warehouse, stateful on budgets and attempts."""

    def __init__(
        self,
        settings: GuardrailSettings | None = None,
        budget: BudgetTracker | None = None,
    ):
        self.settings = settings or GuardrailSettings()
        self.budget = budget or BudgetTracker(
            max_queries_per_investigation=self.settings.max_queries_per_investigation,
            max_bytes_per_day=self.settings.max_bytes_per_day,
        )
        self.attempts: list[Verdict] = []
        self._adhoc_attempts = 0

    def _next_attempt(self, investigation_id: str | None) -> int:
        if investigation_id is None:
            self._adhoc_attempts += 1
            return self._adhoc_attempts
        return self.budget.note_attempt(investigation_id)

    def _record(self, verdict: Verdict) -> Verdict:
        self.attempts.append(verdict)
        return verdict

    def check(
        self,
        sql: str,
        warehouse: Warehouse,
        investigation_id: str | None = None,
    ) -> Verdict:
        """Decide whether one query may run; records the verdict."""
        if investigation_id is not None:
            if reason := self.budget.check_query_cap(investigation_id):
                return self._record(
                    Verdict(
                        allowed=False,
                        rule="query-cap",
                        reason=reason,
                        sql=sql,
                        investigation_id=investigation_id,
                        attempt=self.budget.query_count(investigation_id),
                    )
                )
        attempt = self._next_attempt(investigation_id)

        def refuse(rule: str, reason: str) -> Verdict:
            return self._record(
                Verdict(
                    allowed=False,
                    rule=rule,
                    reason=reason,
                    sql=sql,
                    investigation_id=investigation_id,
                    attempt=attempt,
                )
            )

        parsed = check_sql(
            sql,
            dataset=self.settings.dataset,
            allowed_tables=self.settings.allowed_tables,
            max_rows=self.settings.max_result_rows,
        )
        if not parsed.allowed:
            return refuse(parsed.rule, parsed.reason)
        assert parsed.rewritten_sql is not None  # noqa: S101 - allowed implies rewritten

        dry_run = warehouse.dry_run(parsed.rewritten_sql)
        if not dry_run.valid:
            return refuse(
                "dry-run",
                f"dry run failed: {dry_run.error or 'unknown error'}",
            )

        if dry_run.bytes_processed is not None:
            if dry_run.bytes_processed > self.settings.max_bytes_per_query:
                return refuse(
                    "byte-cap",
                    f"dry run would scan {format_bytes(dry_run.bytes_processed)}, over the "
                    f"{format_bytes(self.settings.max_bytes_per_query)} per-query cap; "
                    f"LIMIT bounds rows returned, not bytes scanned",
                )
            if reason := self.budget.check_daily_cap(dry_run.bytes_processed):
                return refuse("daily-byte-cap", reason)

        if dry_run.bytes_processed is not None:
            self.budget.reserve_bytes(dry_run.bytes_processed)
        return self._record(
            Verdict(
                allowed=True,
                rule="allow",
                reason="allowed",
                sql=sql,
                rewritten_sql=parsed.rewritten_sql,
                bytes_scanned=dry_run.bytes_processed,
                limit_applied=parsed.limit_applied,
                investigation_id=investigation_id,
                attempt=attempt,
            )
        )

    def execute(
        self,
        sql: str,
        warehouse: Warehouse,
        investigation_id: str | None = None,
    ) -> tuple[Verdict, list[dict[str, Any]]]:
        """Check a query and, if allowed, run the rewritten SQL."""
        verdict = self.check(sql, warehouse, investigation_id)
        if not verdict.allowed or verdict.rewritten_sql is None:
            return verdict, []
        return verdict, warehouse.execute(verdict.rewritten_sql)

    def attempts_for(self, investigation_id: str | None) -> list[Verdict]:
        return [v for v in self.attempts if v.investigation_id == investigation_id]
