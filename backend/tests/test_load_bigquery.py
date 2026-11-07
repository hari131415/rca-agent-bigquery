"""Tests for the BigQuery load driver (scripts/load_to_bigquery.py).

The actual BigQuery load is not exercised here (no GCP in CI); the
tests cover the row extraction from the offline warehouse and the
driver's wiring against a fake load function.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import scripts.load_to_bigquery as driver  # noqa: E402


@pytest.fixture()
def warehouse(tmp_path: Path) -> Path:
    copy = tmp_path / "offline.duckdb"
    shutil.copy(ROOT / "data" / "offline.duckdb", copy)
    return copy


def test_read_table_counts(warehouse: Path) -> None:
    rows = driver.read_table(warehouse, "complaints")
    assert len(rows) == 15814
    assert set(rows[0]) == {
        "cmplid",
        "odino",
        "mfr_name",
        "make",
        "model",
        "model_year",
        "crash",
        "faildate",
        "fire",
        "injured",
        "deaths",
        "compdesc",
        "state",
        "datea",
        "ldate",
        "miles",
        "occurrences",
        "cdescr",
        "cmpl_type",
        "prod_type",
        "medical_attn",
        "vehicles_towed_yn",
        "state_of_incident",
        "is_synthetic",
        "plant_id",
        "line_id",
        "supplier_id",
    }
    assert driver.read_table(warehouse, "recalls").__len__() == 841
    assert driver.read_table(warehouse, "builds").__len__() == 576


def test_driver_loads_all_tables(
    monkeypatch: pytest.MonkeyPatch, warehouse: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, str, int]] = []

    def fake_load(project: str, dataset: str, table: str, rows: list[dict[str, object]]) -> int:
        calls.append((project, dataset, table, len(rows)))
        return len(rows)

    monkeypatch.setattr("data.loaders.load_bigquery.load_jsonl", fake_load)
    monkeypatch.setattr(
        sys, "argv", ["load_to_bigquery.py", "--project", "test-project", "--db", str(warehouse)]
    )
    driver.main()

    assert [c[2] for c in calls] == driver.TABLES
    assert [c[3] for c in calls] == [15814, 841, 4, 8, 4, 576]
    assert all(c[0] == "test-project" and c[1] == "rca_agent" for c in calls)
    out = capsys.readouterr().out
    assert "complaints: 15814 rows" in out
    assert "total: 17247 rows" in out


def test_driver_missing_warehouse(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys, "argv", ["load_to_bigquery.py", "--project", "test-project", "--db", "/nonexistent.db"]
    )
    with pytest.raises(SystemExit):
        driver.main()
