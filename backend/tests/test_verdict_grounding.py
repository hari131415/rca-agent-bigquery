"""The verdict-grounding check: a verdict's entity must appear in a
supporting query's result rows for the matching dimension, or the
verdict is marked "(unverified)".

The smoke test caught this: the verdict said supplier/acme-brakes,
but the only supporting query grouped by plant, so no supporting
row contained the supplier. The check is unit-tested directly and
end to end through the graph with the mock LLM.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from langgraph.graph import StateGraph

from rca_agent.config import GuardrailSettings
from rca_agent.graph.graph import build_graph, run_investigation
from rca_agent.graph.state import InvestigationState
from rca_agent.graph.validate import check_verdict_grounding
from rca_agent.llm.mock import MockLLM
from rca_agent.sql.duckdb import DuckDBWarehouse
from rca_agent.sql.guardrails import Guardrails

ROOT = Path(__file__).resolve().parent.parent.parent
OFFLINE_DB = ROOT / "data" / "offline.duckdb"

PLANT_RATE = [
    {"plant_id": "riverside", "n": 1116, "rate_per_1000": 4.14},
    {"plant_id": "lakeside", "n": 268, "rate_per_1000": 1.22},
]
SUPPLIER_RATE = [
    {"supplier_id": "acme-brakes", "n": 956, "rate_per_1000": 7.20},
    {"supplier_id": "beacon-brakes", "n": 160, "rate_per_1000": 1.17},
]
RECALL_ROWS = [{"campno": "26V104000", "potaff": 4381878}]
VOLUME_ROWS = [{"make": "HONDA", "model": "PILOT", "n": 2284, "rate_per_1000": 7.93}]


def _evidence(*blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"sql": "SELECT ...", "rows": rows} for rows in blocks]


def test_plant_verdict_grounded_by_plant_rows() -> None:
    verdict = {"cause_type": "plant", "entity": "riverside", "supporting_queries": [0]}
    assert check_verdict_grounding(verdict, _evidence(PLANT_RATE)) is True


def test_supplier_verdict_not_grounded_by_plant_rows() -> None:
    # The smoke-test bug: the verdict names a supplier, but the
    # only supporting query groups by plant.
    verdict = {"cause_type": "supplier", "entity": "acme-brakes", "supporting_queries": [0]}
    assert check_verdict_grounding(verdict, _evidence(PLANT_RATE)) is False


def test_supplier_verdict_grounded_by_supplier_rows() -> None:
    verdict = {"cause_type": "supplier", "entity": "acme-brakes", "supporting_queries": [0, 1]}
    assert check_verdict_grounding(verdict, _evidence(PLANT_RATE, SUPPLIER_RATE)) is True


def test_line_verdict_grounded_by_line_rows() -> None:
    rows = [{"line_id": "riverside-2", "n": 900, "rate_per_1000": 5.0}]
    verdict = {"cause_type": "line", "entity": "riverside-2", "supporting_queries": [0]}
    assert check_verdict_grounding(verdict, _evidence(rows)) is True


def test_volume_verdict_grounded_by_make_and_model() -> None:
    verdict = {"cause_type": "volume", "entity": "honda-pilot", "supporting_queries": [0]}
    assert check_verdict_grounding(verdict, _evidence(VOLUME_ROWS)) is True


def test_recall_verdict_grounded_by_campno() -> None:
    verdict = {"cause_type": "recall", "entity": "26V104000", "supporting_queries": [0]}
    assert check_verdict_grounding(verdict, _evidence(RECALL_ROWS)) is True


def test_fleet_wide_and_none_need_no_entity() -> None:
    for cause_type in ("fleet_wide", "none"):
        verdict = {"cause_type": cause_type, "entity": None, "supporting_queries": [0]}
        assert check_verdict_grounding(verdict, _evidence(PLANT_RATE)) is True


def test_no_supporting_queries_is_not_grounded() -> None:
    verdict = {"cause_type": "plant", "entity": "riverside", "supporting_queries": []}
    assert check_verdict_grounding(verdict, _evidence(PLANT_RATE)) is False


def test_out_of_range_supporting_index_is_not_grounded() -> None:
    verdict = {"cause_type": "plant", "entity": "riverside", "supporting_queries": [3]}
    assert check_verdict_grounding(verdict, _evidence(PLANT_RATE)) is False


def test_matching_is_case_insensitive() -> None:
    verdict = {"cause_type": "plant", "entity": "Riverside", "supporting_queries": [0]}
    assert check_verdict_grounding(verdict, _evidence(PLANT_RATE)) is True


def _setup(tmp_path: Path) -> StateGraph[InvestigationState]:
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(db_path)
    guardrails = Guardrails(GuardrailSettings())
    return build_graph(MockLLM(), warehouse, guardrails)


def test_p1_verdict_is_unverified_without_supplier_evidence(tmp_path: Path) -> None:
    """The smoke-test bug end to end: the P1 question's verdict is
    supplier/acme-brakes, but its only query groups by plant."""
    graph = _setup(tmp_path)
    result = run_investigation(
        graph, "Why did brake complaints spike for the 2021 F-150?", "inv-grounding-p1"
    )
    assert result["status"] == "done", result.get("error")
    verdict = result.get("verdict") or {}
    assert verdict["cause_type"] == "supplier"
    assert verdict["entity"] == "acme-brakes"
    assert result["verdict_unverified"] is True
    answer = result["answer"]
    assert answer is not None
    assert "Verdict (unverified)" in answer
    assert "does not appear in any supporting query" in answer


def test_supplier_follow_up_verdict_is_grounded(tmp_path: Path) -> None:
    """The follow-up runs the supplier-rate query, so the same
    supplier verdict is grounded there."""
    graph = _setup(tmp_path)
    result = run_investigation(
        graph, "Is the brake spike at Riverside a supplier issue?", "inv-grounding-sup"
    )
    assert result["status"] == "done", result.get("error")
    verdict = result.get("verdict") or {}
    assert verdict["entity"] == "acme-brakes"
    assert result["verdict_unverified"] is False
    answer = result["answer"]
    assert answer is not None
    assert "Verdict (unverified)" not in answer


def test_plant_and_volume_verdicts_are_grounded(tmp_path: Path) -> None:
    graph = _setup(tmp_path)
    steering = run_investigation(
        graph, "Why are steering complaints elevated for the 2019 F-150?", "inv-grounding-st"
    )
    steering_verdict = steering.get("verdict") or {}
    assert steering_verdict["entity"] == "lakeside"
    assert steering["verdict_unverified"] is False
    pilot = run_investigation(
        graph, "Why did total complaints rise for the Honda Pilot in 2021?", "inv-grounding-p4"
    )
    pilot_verdict = pilot.get("verdict") or {}
    assert pilot_verdict["entity"] == "honda-pilot"
    assert pilot["verdict_unverified"] is False
