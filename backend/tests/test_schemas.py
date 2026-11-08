"""Tests for the schema DDL: both dialects parse, and they agree.

The BigQuery DDL is validated here by parsing, not by executing —
there is no GCP project yet. Execution happens in Phase 8.
"""

import re
from pathlib import Path

import sqlglot
from sqlglot import exp

from rca_agent.graph.nodes import SCHEMA_DOC

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


def test_schema_doc_matches_the_real_ddl() -> None:
    """The schema the model sees must be the schema that exists.

    The first live run invented plant_name, supplier_name,
    component, and build_month — and the old SCHEMA_DOC listed
    plant_name, supplier_name, and build_month itself, so the
    model was following its instructions. This test parses the
    DDL and the schema document and requires the column lists to
    match exactly, table by table.
    """
    ddl_tables = _tables(BQ_DDL, "bigquery")
    doc_tables = {
        match.group(1): [c.strip() for c in match.group(2).split(",")]
        for match in re.finditer(r"^- (\w+)\(([^)]*)\)", SCHEMA_DOC, re.MULTILINE)
    }
    assert set(doc_tables) == set(ddl_tables)
    for table, columns in ddl_tables.items():
        assert doc_tables[table] == columns, f"{table}: doc lists {doc_tables[table]}"
    # The invented columns, stated plainly so a regression names
    # them. The names appear in the prose warning above the lists,
    # so this checks the parsed lists, not the whole string.
    for invented in ("plant_name", "supplier_name", "component", "build_month"):
        assert all(invented not in cols for cols in doc_tables.values())
