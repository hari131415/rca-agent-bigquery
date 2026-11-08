"""The uniformity check, the confound check, and derived-number
validation — the three deterministic rules added after the second
live run (2026-11-08), which over-attributed the fleet-wide P3
rise to a supplier, a plant, and a volume.

The uniformity threshold (max/min rate ratio 2.0) is justified
from the warehouse itself: the localized spikes are 3.6x (P1
brake by plant, Riverside 4.14 vs 1.14) and 4.3x (P2 steering by
plant, Lakeside 3.21 vs 0.74), while the fleet-wide rise is
1.02x (P3 airbag by plant, 1.80–1.83) and 1.05x (P3 airbag by
line, 1.77–1.86).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from rca_agent.graph.validate import (
    UNIFORMITY_MAX_MIN_RATIO,
    check_confound,
    check_rate_uniformity,
    validate_claims,
)
from rca_agent.sql.duckdb import DuckDBWarehouse

ROOT = Path(__file__).resolve().parent.parent.parent
OFFLINE_DB = ROOT / "data" / "offline.duckdb"

RATE_BY_PLANT_SQL = """WITH built AS (
  SELECT plant_id, model_year, SUM(vehicles_built) AS vehicles
  FROM rca_agent.builds GROUP BY plant_id, model_year
), complaints AS (
  SELECT plant_id, model_year, COUNT(*) AS n
  FROM rca_agent.complaints
  WHERE is_synthetic AND compdesc = '{component}' AND model_year = {year}
  GROUP BY plant_id, model_year
)
SELECT c.plant_id, c.model_year, c.n * 1000.0 / b.vehicles AS rate_per_1000
FROM complaints c JOIN built b
  ON b.plant_id = c.plant_id AND b.model_year = c.model_year
ORDER BY rate_per_1000 DESC"""

RATE_BY_SUPPLIER_SQL = """WITH built AS (
  SELECT supplier_brake AS s, SUM(vehicles_built) AS vehicles
  FROM rca_agent.builds WHERE plant_id = 'riverside' AND model_year = 2021
  GROUP BY supplier_brake
), complaints AS (
  SELECT supplier_id, COUNT(*) AS n
  FROM rca_agent.complaints
  WHERE is_synthetic AND compdesc = 'SERVICE BRAKES' AND model_year = 2021
    AND plant_id = 'riverside'
  GROUP BY supplier_id
)
SELECT c.supplier_id, c.n * 1000.0 / b.vehicles AS rate_per_1000
FROM complaints c JOIN built b ON b.s = c.supplier_id
ORDER BY rate_per_1000 DESC"""


def _evidence(warehouse: DuckDBWarehouse, sql: str) -> list[dict[str, Any]]:
    return [{"sql": sql, "rows": warehouse.execute(sql)}]


def test_uniformity_threshold_sits_between_the_patterns() -> None:
    """The threshold itself: above the uniform rises, below the
    localized spikes, with margin on both sides."""
    assert 1.05 < UNIFORMITY_MAX_MIN_RATIO < 3.6


def test_p3_airbag_rise_is_uniform_across_plants(tmp_path: Path) -> None:
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(str(db_path))
    evidence = _evidence(warehouse, RATE_BY_PLANT_SQL.format(component="AIR BAGS", year=2020))
    result = check_rate_uniformity(evidence, "plant")
    assert result["checked"] is True
    assert result["uniform"] is True
    assert result["ratio"] == pytest.approx(1.83 / 1.80, rel=0.01)
    assert result["entities"] == 4


def test_p1_brake_spike_is_not_uniform(tmp_path: Path) -> None:
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(str(db_path))
    evidence = _evidence(warehouse, RATE_BY_PLANT_SQL.format(component="SERVICE BRAKES", year=2021))
    result = check_rate_uniformity(evidence, "plant")
    assert result["checked"] is True
    assert result["uniform"] is False
    assert result["ratio"] > UNIFORMITY_MAX_MIN_RATIO


def test_supplier_verdict_falls_back_to_plants_and_lines(tmp_path: Path) -> None:
    """Airbags have a single supplier (meridian-airbags), so the
    supplier dimension cannot be compared. The check falls back to
    the plant rates, which are uniform — and the verdict is
    overridden, with the fallback stated in the note."""
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(str(db_path))
    evidence = _evidence(warehouse, RATE_BY_PLANT_SQL.format(component="AIR BAGS", year=2020))
    result = check_rate_uniformity(evidence, "supplier")
    assert result["checked"] is True
    assert result["uniform"] is True
    assert "single entity" in result["note"]


def test_no_rate_rows_is_inconclusive_not_uniform() -> None:
    evidence = [
        {"sql": "SELECT make, COUNT(*) AS n FROM t GROUP BY 1", "rows": [{"make": "FORD", "n": 10}]}
    ]
    result = check_rate_uniformity(evidence, "plant")
    assert result["checked"] is False
    assert result["uniform"] is False


def test_confound_fires_when_both_dimensions_compared(tmp_path: Path) -> None:
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(str(db_path))
    evidence = _evidence(
        warehouse, RATE_BY_PLANT_SQL.format(component="SERVICE BRAKES", year=2021)
    ) + _evidence(warehouse, RATE_BY_SUPPLIER_SQL)
    follow_up = check_confound(evidence, {"cause_type": "plant", "entity": "riverside"})
    assert follow_up is not None
    assert "supplier within plant riverside" in follow_up["description"]


def test_confound_fires_when_other_dimension_never_examined(tmp_path: Path) -> None:
    """A plant verdict from plant rates alone, with a real
    concentration (4.14 vs 1.22), still has to check the supplier
    split before deciding — that is the live T01 failure."""
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(str(db_path))
    evidence = _evidence(warehouse, RATE_BY_PLANT_SQL.format(component="SERVICE BRAKES", year=2021))
    follow_up = check_confound(evidence, {"cause_type": "plant", "entity": "riverside"})
    assert follow_up is not None
    assert "supplier within plant riverside" in follow_up["description"]


def test_confound_does_not_fire_without_a_concentration() -> None:
    """Flat rates in the verdict dimension and no look at the
    other dimension: there is no concentration to disentangle."""
    evidence = [
        {
            "sql": "SELECT plant_id, rate_per_1000 FROM t",
            "rows": [
                {"plant_id": "riverside", "model_year": 2020, "rate_per_1000": 1.83},
                {"plant_id": "lakeside", "model_year": 2020, "rate_per_1000": 1.81},
            ],
        }
    ]
    assert check_confound(evidence, {"cause_type": "plant", "entity": "riverside"}) is None


def test_confound_does_not_fire_for_line_or_fleet_wide() -> None:
    evidence = [
        {
            "sql": "SELECT plant_id, rate_per_1000 FROM t",
            "rows": [
                {"plant_id": "riverside", "model_year": 2021, "rate_per_1000": 4.14},
                {"plant_id": "lakeside", "model_year": 2021, "rate_per_1000": 1.22},
            ],
        }
    ]
    assert check_confound(evidence, {"cause_type": "line", "entity": "riverside-2"}) is None
    assert check_confound(evidence, {"cause_type": "fleet_wide", "entity": None}) is None


def test_validator_accepts_a_ratio_of_two_rates() -> None:
    """'4.14 vs 1.22, about 3.4 times higher': the 3.4 is the
    ratio of two row values in the same column, so it verifies."""
    evidence = [
        {
            "sql": "SELECT plant_id, rate_per_1000 FROM t",
            "rows": [
                {"plant_id": "riverside", "rate_per_1000": 4.14},
                {"plant_id": "lakeside", "rate_per_1000": 1.22},
            ],
        }
    ]
    analysis = {
        "summary": "Riverside 4.14 vs 1.22, about 3.4 times higher.",
        "ranked_hypotheses": [],
    }
    _, unverified = validate_claims(analysis, evidence)
    assert unverified == 0


def test_validator_accepts_a_difference_and_a_share() -> None:
    evidence = [
        {
            "sql": "SELECT model_year, n FROM t",
            "rows": [
                {"model_year": 2021, "n": 53},
                {"model_year": 2020, "n": 36},
            ],
        }
    ]
    analysis = {
        "summary": "53 covered of 89 total, about 59.6% covered; 17 more than in 2020.",
        "ranked_hypotheses": [],
    }
    _, unverified = validate_claims(analysis, evidence)
    assert unverified == 0


def test_validator_still_flags_a_number_with_no_basis() -> None:
    evidence = [
        {
            "sql": "SELECT plant_id, rate_per_1000 FROM t",
            "rows": [
                {"plant_id": "riverside", "rate_per_1000": 4.14},
                {"plant_id": "lakeside", "rate_per_1000": 1.22},
            ],
        }
    ]
    analysis = {
        "summary": "Riverside 4.14 vs 1.22, about 9.7 times higher.",
        "ranked_hypotheses": [],
    }
    _, unverified = validate_claims(analysis, evidence)
    assert unverified == 1
