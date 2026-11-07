"""Tests for the DuckDB loader and the planted-pattern checks."""

import duckdb
import pytest
from data.loaders import load_duckdb, synthetic
from data.loaders.synthetic import VehicleScope

SCOPE = [
    VehicleScope(
        make="FORD", model="F-150", vehicles_by_year={2019: 100_000, 2020: 100_000, 2021: 100_000}
    ),
    VehicleScope(
        make="TOYOTA", model="RAV4", vehicles_by_year={2019: 100_000, 2020: 100_000, 2021: 200_000}
    ),
]


@pytest.fixture
def warehouse() -> duckdb.DuckDBPyConnection:
    generated = synthetic.generate(SCOPE)
    con = duckdb.connect()
    load_duckdb.create_tables(con)
    load_duckdb.load_rows(con, "complaints", load_duckdb.COMPLAINT_COLUMNS, generated.complaints)
    load_duckdb.load_rows(con, "builds", load_duckdb.BUILD_COLUMNS, generated.builds)
    load_duckdb.load_rows(
        con,
        "plants",
        ["plant_id", "name", "state"],
        [
            {"plant_id": p["plant_id"], "name": p["name"], "state": p["state"]}
            for p in synthetic.PLANTS
        ],
    )
    load_duckdb.load_rows(
        con,
        "lines",
        ["line_id", "plant_id", "product"],
        [
            {"line_id": item["line_id"], "plant_id": item["plant_id"], "product": item["product"]}
            for item in synthetic.LINES
        ],
    )
    load_duckdb.load_rows(
        con,
        "suppliers",
        ["supplier_id", "name", "part"],
        [
            {"supplier_id": s["supplier_id"], "name": s["name"], "part": s["part"]}
            for s in synthetic.SUPPLIERS
        ],
    )
    return con


def test_verify_patterns_all_pass(warehouse: duckdb.DuckDBPyConnection) -> None:
    results = load_duckdb.verify_patterns(warehouse)
    assert results.keys() == {"P1", "P2", "P3", "P4"}
    failed = {name: res["detail"] for name, res in results.items() if not res["passed"]}
    assert not failed, failed


def test_rate_by_plant_orders_highest_first(warehouse: duckdb.DuckDBPyConnection) -> None:
    rates = load_duckdb.rate_by_plant(warehouse, "SERVICE BRAKES", 2021)
    assert rates[0][0] == "riverside"
    assert [rate for _, rate, _ in rates] == sorted([rate for _, rate, _ in rates], reverse=True)
