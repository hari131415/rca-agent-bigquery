"""Tests for the schema DDL: both dialects parse, and they agree.

The BigQuery DDL is validated here by parsing, not by executing —
there is no GCP project yet. Execution happens in Phase 8.
"""

from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parent.parent.parent
BQ_DDL = (ROOT / "data" / "schemas" / "bigquery.sql").read_text()
DUCK_DDL = (ROOT / "data" / "schemas" / "duckdb.sql").read_text()


def _tables(sql: str, dialect: str) -> dict[str, list[str]]:
    tables: dict[str, list[str]] = {}
    for statement in sqlglot.parse(sql, read=dialect):
        assert statement is not None
        create = statement.find(exp.Create)
        assert create is not None
        table = create.find(exp.Table)
        assert table is not None
        columns = [col.name for col in create.find_all(exp.ColumnDef)]
        tables[table.name] = columns
    return tables


def test_bigquery_ddl_parses() -> None:
    tables = _tables(BQ_DDL, "bigquery")
    assert set(tables) == {"complaints", "recalls", "plants", "lines", "suppliers", "builds"}
    assert "is_synthetic" in tables["complaints"]
    assert "source" not in tables["complaints"]
    assert "plant_id" in tables["complaints"]
    assert "supplier_id" in tables["complaints"]


def test_duckdb_ddl_parses() -> None:
    tables = _tables(DUCK_DDL, "duckdb")
    assert set(tables) == {"complaints", "recalls", "plants", "lines", "suppliers", "builds"}


def test_both_dialects_agree_on_tables_and_columns() -> None:
    assert _tables(BQ_DDL, "bigquery") == _tables(DUCK_DDL, "duckdb")


def test_bigquery_complaints_is_partitioned_and_clustered() -> None:
    parsed = sqlglot.parse_one(
        [s for s in BQ_DDL.split(";") if "complaints" in s and "CREATE" in s][0],
        read="bigquery",
    )
    sql = parsed.sql(dialect="bigquery").upper()
    assert "PARTITION BY" in sql and "CLUSTER BY" in sql
