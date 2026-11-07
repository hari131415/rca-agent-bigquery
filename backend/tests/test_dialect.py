"""Tests for the BigQuery <-> DuckDB dialect boundary."""

import datetime
from pathlib import Path

import duckdb
from data.loaders import load_duckdb

from rca_agent.sql.dialect import to_duckdb


def test_backticks_and_safe_divide_transpile_and_run(tmp_path: Path) -> None:
    db_path = tmp_path / "offline.duckdb"
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
            }
        ],
    )
    bigquery_sql = "SELECT `make`, SAFE_DIVIDE(1, 2) AS half FROM rca_agent.complaints LIMIT 5"
    duckdb_sql = to_duckdb(bigquery_sql)
    rows = con.execute(duckdb_sql).fetchall()
    assert rows == [("FORD", 0.5)]
    con.close()


def test_date_trunc_argument_order(tmp_path: Path) -> None:
    db_path = tmp_path / "offline.duckdb"
    con = duckdb.connect(str(db_path))
    rows = con.execute(to_duckdb("SELECT DATE_TRUNC(DATE '2021-01-15', MONTH) AS m")).fetchall()
    # DuckDB returns a TIMESTAMP where BigQuery returns a DATE; the
    # value is the same, the type is not (a dialect difference).
    assert rows[0][0].date() == datetime.date(2021, 1, 1)
    con.close()
