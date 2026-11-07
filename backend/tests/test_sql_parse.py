"""Tests for sqlglot AST checks: what the agent's SQL may and may not do."""

import pytest

from rca_agent.sql.parse import SqlCheck, check_sql

DATASET = "rca_agent"
ALLOWED = frozenset({"complaints", "recalls", "plants", "lines", "suppliers", "builds"})
MAX_ROWS = 1000


def check(sql: str) -> SqlCheck:
    return check_sql(sql, dataset=DATASET, allowed_tables=ALLOWED, max_rows=MAX_ROWS)


def test_simple_select_allowed() -> None:
    result = check("SELECT make, COUNT(*) FROM rca_agent.complaints GROUP BY 1")
    assert result.allowed
    assert result.rule == "allow"
    assert result.limit_applied == MAX_ROWS
    assert result.limit_injected is True
    assert "LIMIT 1000" in (result.rewritten_sql or "")


def test_join_of_allowed_tables() -> None:
    result = check(
        "SELECT c.make FROM rca_agent.complaints c "
        "JOIN rca_agent.recalls r ON r.make = c.make AND r.model_year = c.model_year"
    )
    assert result.allowed


def test_cte_over_allowed_table() -> None:
    result = check(
        "WITH recent AS (SELECT * FROM rca_agent.complaints WHERE model_year = 2021) "
        "SELECT make, COUNT(*) FROM recent GROUP BY 1"
    )
    assert result.allowed


def test_subquery_over_allowed_table() -> None:
    result = check(
        "SELECT * FROM (SELECT make FROM rca_agent.complaints WHERE model_year = 2021) t"
    )
    assert result.allowed


def test_union_of_allowed_tables() -> None:
    result = check(
        "SELECT make FROM rca_agent.complaints UNION ALL SELECT make FROM rca_agent.recalls"
    )
    assert result.allowed


def test_select_without_from() -> None:
    assert check("SELECT 1").allowed


def test_limit_under_cap_kept() -> None:
    result = check("SELECT * FROM rca_agent.complaints LIMIT 10")
    assert result.allowed
    assert result.limit_applied == 10
    assert result.limit_injected is False


def test_limit_over_cap_lowered() -> None:
    result = check("SELECT * FROM rca_agent.complaints LIMIT 5000")
    assert result.allowed
    assert result.limit_applied == MAX_ROWS
    assert "LIMIT 1000" in (result.rewritten_sql or "")


def test_case_tricks_allowed() -> None:
    assert check("SeLeCt * FrOm rca_agent.complaints").allowed


def test_comments_around_select_allowed() -> None:
    assert check("/* DROP TABLE */ SELECT * FROM rca_agent.complaints").allowed
    assert check("-- a comment\nSELECT * FROM rca_agent.complaints").allowed


def test_forbidden_word_inside_string_literal_allowed() -> None:
    assert check("SELECT * FROM rca_agent.complaints WHERE compdesc LIKE '%DROP TABLE%'").allowed


@pytest.mark.parametrize(
    ("sql", "rule"),
    [
        ("SELECT * FROM rca_agent.complaints; SELECT 1", "multi-statement"),
        ("BEGIN SELECT 1; END", "multi-statement"),
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
        ("DECLARE x INT64 DEFAULT 1", "not-select"),
        ("SET x = 1", "not-select"),
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
        ("/* comment */ DROP TABLE rca_agent.complaints", "not-select"),
        ("SELECT * FROM rca_agent.*", "wildcard-table"),
        ("SELECT * FROM rca_agent.complaints_*", "wildcard-table"),
        ("SELECT * FROM rca_agent.INFORMATION_SCHEMA.TABLES", "system-table"),
        ("SELECT * FROM INFORMATION_SCHEMA.TABLES", "system-table"),
        ("SELECT * FROM rca_agent.__TABLES__", "system-table"),
        ("SELECT * FROM other_project.rca_agent.complaints", "cross-project"),
        ("SELECT * FROM other_dataset.complaints", "cross-dataset"),
        ("SELECT * FROM complaints", "unqualified-table"),
        ("SELECT * FROM rca_agent.secret", "disallowed-table"),
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
        ("SELECT FROM WHERE", "parse-error"),
        # Fail closed: unparsable SQL is refused, never run.
        ("SELECT * FROM", "parse-error"),
        ("SELECT (", "parse-error"),
        ("SELECT * FROM rca_agent.complaints WHERE", "parse-error"),
        ("SELECT 'unclosed", "parse-error"),
        ("SELECT * FROM rca_agent.complaints WHERE x = ", "parse-error"),
        ("", "parse-error"),
        ("   ", "parse-error"),
        ("garbage", "not-select"),
        ("SELECT * FROM rca_agent.complaints LIMIT -1", "invalid-limit"),
        # ML/AI + remote functions: cost not visible in dry-run bytes.
        ("SELECT * FROM ML.PREDICT(MODEL `p.d.m`, (SELECT 1 AS x))", "disallowed-function"),
        ("SELECT * FROM ML.FORECAST(MODEL `p.d.m`, STRUCT(3 AS horizon))", "disallowed-function"),
        ("SELECT AI.GENERATE_BOOL('is this a brake issue?')", "disallowed-function"),
        ("SELECT AI.GENERATE('hello')", "disallowed-function"),
        ("SELECT AI.GENERATE_EMBEDDING('hello')", "parse-error"),
        (
            "SELECT rca_agent.my_remote_fn(make) FROM rca_agent.complaints",
            "disallowed-function",
        ),
        (
            "SELECT my_project.rca_agent.my_remote_fn(make) FROM rca_agent.complaints",
            "disallowed-function",
        ),
    ],
)
def test_refused(sql: str, rule: str) -> None:
    result = check(sql)
    assert not result.allowed, f"{sql!r} was allowed"
    assert result.rule == rule, f"{sql!r}: got rule {result.rule}, want {rule}"
    assert result.reason, "refusal must carry a reason"


def test_cheap_builtins_allowed() -> None:
    assert check("SELECT SAFE_DIVIDE(1, 2) FROM rca_agent.complaints").allowed
    assert check("SELECT COUNT(*) FROM rca_agent.complaints").allowed
    assert check("SELECT GENERATE_SERIES(1, 5) AS n").allowed
    assert check(
        "SELECT DATE_TRUNC(DATE '2021-01-15', MONTH) AS m FROM rca_agent.complaints"
    ).allowed
