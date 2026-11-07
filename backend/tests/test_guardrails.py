"""Tests for the guardrail gate: dry-run caps, budgets, verdicts."""

from pathlib import Path

import pytest
from data.loaders import load_duckdb

from rca_agent.config import GuardrailSettings
from rca_agent.sql.duckdb import DuckDBWarehouse
from rca_agent.sql.guardrails import Guardrails
from rca_agent.sql.warehouse import DryRunResult


class StubWarehouse:
    """A Warehouse that reports a fixed dry-run byte count."""

    name = "stub"

    def __init__(self, bytes_processed: int | None = 100, valid: bool = True):
        self.bytes_processed = bytes_processed
        self.valid = valid
        self.executed: list[str] = []
        self.dry_run_sql: list[str] = []

    def dry_run(self, sql: str) -> DryRunResult:
        self.dry_run_sql.append(sql)
        return DryRunResult(
            bytes_processed=self.bytes_processed,
            valid=self.valid,
            error=None if self.valid else "boom",
        )

    def execute(self, sql: str) -> list[dict[str, object]]:
        self.executed.append(sql)
        return [{"make": "FORD"}]


def _settings(
    max_result_rows: int = 1000,
    max_bytes_per_query: int = 1_000_000_000,
    max_queries_per_investigation: int = 20,
    max_bytes_per_day: int = 10_000_000_000,
) -> GuardrailSettings:
    return GuardrailSettings(
        max_result_rows=max_result_rows,
        max_bytes_per_query=max_bytes_per_query,
        max_queries_per_investigation=max_queries_per_investigation,
        max_bytes_per_day=max_bytes_per_day,
    )


def test_limit_does_not_protect_cost() -> None:
    """Requirement: a LIMIT-ed query over the byte cap is still refused.

    BigQuery bills bytes scanned, not rows returned, so LIMIT 10
    over the whole table still scans the whole table.
    """
    guardrails = Guardrails(_settings())
    warehouse = StubWarehouse(bytes_processed=2_000_000_000)
    verdict = guardrails.check("SELECT * FROM rca_agent.complaints LIMIT 10", warehouse, "inv-1")
    assert not verdict.allowed
    assert verdict.rule == "byte-cap"
    assert "2 GB" in verdict.reason
    assert "LIMIT bounds rows returned, not bytes scanned" in verdict.reason
    assert warehouse.executed == []


def test_executed_sql_is_the_checked_ast() -> None:
    """What runs is the serialized, checked AST — never the original string.

    The original has a comment and a LIMIT 5000; the dry run and the
    execution both receive the rewritten SQL with LIMIT 1000. (sqlglot
    preserves comments in serialization, so the proof is the LIMIT
    value and equality with rewritten_sql, not comment absence.)
    """
    guardrails = Guardrails(_settings())
    warehouse = StubWarehouse()
    original = "/* a comment */ SELECT * FROM rca_agent.complaints LIMIT 5000"
    verdict, rows = guardrails.execute(original, warehouse, "inv-1")
    assert verdict.allowed
    assert verdict.rewritten_sql is not None
    assert "LIMIT 1000" in verdict.rewritten_sql
    assert "LIMIT 5000" not in verdict.rewritten_sql
    assert warehouse.executed == [verdict.rewritten_sql]
    assert warehouse.dry_run_sql == [verdict.rewritten_sql]
    assert warehouse.executed[0] != original
    assert rows == [{"make": "FORD"}]


def test_allowed_query_records_verdict_and_bytes() -> None:
    guardrails = Guardrails(_settings())
    warehouse = StubWarehouse(bytes_processed=500)
    verdict = guardrails.check("SELECT * FROM rca_agent.complaints", warehouse, "inv-1")
    assert verdict.allowed
    assert verdict.rule == "allow"
    assert verdict.bytes_scanned == 500
    assert verdict.limit_applied == 1000
    assert verdict.attempt == 1
    assert guardrails.attempts == [verdict]


def test_verdict_recorded_for_every_attempt() -> None:
    guardrails = Guardrails(_settings())
    warehouse = StubWarehouse()
    guardrails.check("SELECT * FROM rca_agent.complaints", warehouse, "inv-1")
    guardrails.check("DROP TABLE rca_agent.complaints", warehouse, "inv-1")
    guardrails.check("SELECT * FROM other_dataset.secret", warehouse, "inv-1")
    guardrails.check("SELECT * FROM rca_agent.recalls", warehouse, "inv-2")
    assert [v.rule for v in guardrails.attempts] == [
        "allow",
        "not-select",
        "cross-dataset",
        "allow",
    ]
    assert [v.attempt for v in guardrails.attempts] == [1, 2, 3, 1]
    assert [v.investigation_id for v in guardrails.attempts] == [
        "inv-1",
        "inv-1",
        "inv-1",
        "inv-2",
    ]
    assert len(guardrails.attempts_for("inv-1")) == 3
    assert all(v.reason for v in guardrails.attempts)


def test_refused_query_counts_toward_query_cap() -> None:
    guardrails = Guardrails(_settings(max_queries_per_investigation=2))
    warehouse = StubWarehouse()
    guardrails.check("SELECT FROM WHERE", warehouse, "inv-1")  # parse error
    guardrails.check("DROP TABLE rca_agent.complaints", warehouse, "inv-1")
    verdict = guardrails.check("SELECT * FROM rca_agent.complaints", warehouse, "inv-1")
    assert not verdict.allowed
    assert verdict.rule == "query-cap"
    assert "2 of 2" in verdict.reason


def test_daily_byte_cap() -> None:
    guardrails = Guardrails(_settings(max_bytes_per_day=1000))
    warehouse = StubWarehouse(bytes_processed=600)
    first = guardrails.check("SELECT * FROM rca_agent.complaints", warehouse, "inv-1")
    assert first.allowed
    second = guardrails.check("SELECT * FROM rca_agent.recalls", warehouse, "inv-1")
    assert not second.allowed
    assert second.rule == "daily-byte-cap"
    assert "1,200" in second.reason


def test_dry_run_failure_refused() -> None:
    guardrails = Guardrails(_settings())
    warehouse = StubWarehouse(valid=False)
    verdict = guardrails.check("SELECT * FROM rca_agent.complaints", warehouse, "inv-1")
    assert not verdict.allowed
    assert verdict.rule == "dry-run"
    assert "boom" in verdict.reason


def _seed_warehouse(tmp_path: Path) -> DuckDBWarehouse:
    db_path = tmp_path / "offline.duckdb"
    import duckdb

    con = duckdb.connect(str(db_path))
    load_duckdb.create_tables(con)
    load_duckdb.load_rows(
        con,
        "complaints",
        load_duckdb.COMPLAINT_COLUMNS,
        [
            {
                "cmplid": "1",
                "make": "FORD",
                "model": "F-150",
                "model_year": 2021,
                "compdesc": "SERVICE BRAKES",
                "is_synthetic": False,
            },
            {
                "cmplid": "2",
                "make": "HONDA",
                "model": "PILOT",
                "model_year": 2021,
                "compdesc": "STEERING",
                "is_synthetic": False,
            },
        ],
    )
    con.close()
    return DuckDBWarehouse(db_path)


def test_duckdb_execute_end_to_end(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings())
    warehouse = _seed_warehouse(tmp_path)
    verdict, rows = guardrails.execute(
        "SELECT make FROM rca_agent.complaints WHERE model_year = 2021",
        warehouse,
        "inv-1",
    )
    assert verdict.allowed
    assert verdict.bytes_scanned is None  # DuckDB reports no bytes
    assert verdict.limit_applied == 1000
    assert [row["make"] for row in rows] == ["FORD", "HONDA"]


def test_duckdb_bad_column_refused_at_dry_run(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings())
    warehouse = _seed_warehouse(tmp_path)
    verdict, rows = guardrails.execute(
        "SELECT nosuchcolumn FROM rca_agent.complaints", warehouse, "inv-1"
    )
    assert not verdict.allowed
    assert verdict.rule == "dry-run"
    assert rows == []


def test_duckdb_refused_query_never_executes(tmp_path: Path) -> None:
    guardrails = Guardrails(_settings())
    warehouse = _seed_warehouse(tmp_path)
    verdict, rows = guardrails.execute(
        "DELETE FROM rca_agent.complaints WHERE TRUE", warehouse, "inv-1"
    )
    assert not verdict.allowed
    assert verdict.rule == "not-select"
    assert rows == []
    assert warehouse.dry_run("SELECT COUNT(*) FROM complaints").bytes_processed is None


@pytest.mark.parametrize(
    ("sql", "rule"),
    [
        ("DECLARE x INT64 DEFAULT 1", "not-select"),
        ("BEGIN SELECT 1; END", "multi-statement"),
        ("SELECT * FROM rca_agent.complaints; SELECT 1", "multi-statement"),
        ("CREATE TEMP FUNCTION f(x INT64) AS (x + 1)", "not-select"),
        (
            'CREATE FUNCTION f(x INT64) RETURNS INT64 LANGUAGE js AS "return x;"',
            "not-select",
        ),
        (
            "EXPORT DATA OPTIONS (uri='gs://b/f.csv') AS SELECT * FROM rca_agent.complaints",
            "not-select",
        ),
        ("CALL my_proc()", "not-select"),
        ("INSERT INTO rca_agent.complaints VALUES (1)", "not-select"),
        ("UPDATE rca_agent.complaints SET make = 'X' WHERE TRUE", "not-select"),
        ("DELETE FROM rca_agent.complaints WHERE TRUE", "not-select"),
        (
            "MERGE INTO rca_agent.complaints USING rca_agent.recalls "
            "ON FALSE WHEN MATCHED THEN DELETE",
            "not-select",
        ),
        ("CREATE TABLE rca_agent.t AS SELECT 1", "not-select"),
        ("DROP TABLE rca_agent.complaints", "not-select"),
        ("TRUNCATE TABLE rca_agent.complaints", "not-select"),
        ("SELECT * FROM rca_agent.*", "wildcard-table"),
        ("SELECT * FROM rca_agent.complaints_*", "wildcard-table"),
        ("SELECT * FROM rca_agent.INFORMATION_SCHEMA.TABLES", "system-table"),
        ("SELECT * FROM INFORMATION_SCHEMA.TABLES", "system-table"),
        ("SELECT * FROM other_project.rca_agent.complaints", "cross-project"),
        ("SELECT * FROM other_dataset.complaints", "cross-dataset"),
        (
            "SELECT * FROM rca_agent.complaints FOR SYSTEM_TIME AS OF '2024-01-01'",
            "system-time",
        ),
        ("SELECT * FROM EXTERNAL_QUERY('conn', 'SELECT 1')", "table-function"),
        (
            "WITH x AS (SELECT * FROM other_dataset.secret) SELECT * FROM x",
            "cross-dataset",
        ),
        (
            "SELECT * FROM (SELECT * FROM other_dataset.secret) t",
            "cross-dataset",
        ),
        (
            "SELECT * FROM rca_agent.complaints UNION ALL SELECT * FROM other_dataset.secret",
            "cross-dataset",
        ),
        (
            "SELECT * FROM ML.PREDICT(MODEL `p.d.m`, (SELECT 1 AS x))",
            "disallowed-function",
        ),
        ("SELECT AI.GENERATE_BOOL('is this a brake issue?')", "disallowed-function"),
        ("SELECT AI.GENERATE('hello')", "disallowed-function"),
        (
            "SELECT rca_agent.my_remote_fn(make) FROM rca_agent.complaints",
            "disallowed-function",
        ),
        ("SELECT * FROM rca_agent.complaints LIMIT -1", "invalid-limit"),
    ],
)
def test_bypass_routes_refused_end_to_end(sql: str, rule: str) -> None:
    guardrails = Guardrails(_settings())
    warehouse = StubWarehouse()
    verdict = guardrails.check(sql, warehouse, "inv-1")
    assert not verdict.allowed, f"{sql!r} was allowed"
    assert verdict.rule == rule, f"{sql!r}: got {verdict.rule}, want {rule}"
    assert verdict.reason
    assert verdict in guardrails.attempts  # every refusal is recorded
    assert warehouse.executed == []
