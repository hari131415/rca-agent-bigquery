"""Full investigations through the graph with the mock LLM, against
a copy of the real offline warehouse (the original is never opened
for writing)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from langgraph.graph import StateGraph

from rca_agent.config import GuardrailSettings
from rca_agent.graph.graph import build_graph, run_investigation
from rca_agent.graph.state import InvestigationState
from rca_agent.llm.mock import MockLLM
from rca_agent.sql.duckdb import DuckDBWarehouse
from rca_agent.sql.guardrails import Guardrails

ROOT = Path(__file__).resolve().parent.parent.parent
OFFLINE_DB = ROOT / "data" / "offline.duckdb"


def _setup(
    tmp_path: Path, llm: MockLLM | None = None
) -> tuple[StateGraph[InvestigationState], Guardrails]:
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(db_path)
    guardrails = Guardrails(GuardrailSettings())
    graph = build_graph(llm or MockLLM(), warehouse, guardrails)
    return graph, guardrails


def test_brake_spike_investigation(tmp_path: Path) -> None:
    graph, guardrails = _setup(tmp_path)
    result = run_investigation(
        graph, "Why did brake complaints spike for the 2021 F-150?", "inv-p1"
    )
    assert result["status"] == "done", result.get("error")
    answer = result["answer"]
    assert answer is not None
    assert "Riverside" in answer
    assert "4.14" in answer
    assert "(synthetic)" in answer
    assert len(result["queries"]) == 1
    assert result["queries"][0]["rule"] == "allow"
    rows = result["evidence"][0]["rows"]
    assert rows[0]["plant_id"] == "riverside"
    assert abs(rows[0]["rate_per_1000"] - 4.14) < 0.05
    assert rows[0]["n"] == 1116
    assert "rca_agent.complaints" in result["queries"][0]["sql"]
    verdicts = guardrails.attempts_for("inv-p1")
    assert len(verdicts) == 1 and verdicts[0].allowed


def test_self_correction_recovers(tmp_path: Path) -> None:
    graph, guardrails = _setup(tmp_path)
    result = run_investigation(
        graph, "Why are steering complaints elevated for the 2019 F-150?", "inv-p2"
    )
    assert result["status"] == "done", result.get("error")
    assert result["attempts"] == 1
    answer = result["answer"]
    assert answer is not None
    assert "Lakeside" in answer
    assert "(synthetic)" in answer
    verdicts = guardrails.attempts_for("inv-p2")
    assert [v.rule for v in verdicts] == ["cross-dataset", "allow"]
    assert not verdicts[0].allowed and verdicts[1].allowed


def test_negative_control_finds_nothing(tmp_path: Path) -> None:
    graph, _ = _setup(tmp_path)
    result = run_investigation(
        graph, "Why did engine complaints spike for the 2019 F-150?", "inv-neg"
    )
    assert result["status"] == "done", result.get("error")
    answer = result["answer"]
    assert answer is not None
    assert "No supporting evidence found." in answer


def test_corrections_exhausted_fails(tmp_path: Path) -> None:
    class AlwaysBad(MockLLM):
        def _reply(
            self,
            step: str,
            kind: str,
            step_id: str,
            is_follow_up: bool,
            has_supplier_evidence: bool,
            question: str = "",
        ) -> str:
            if step in ("sql", "correct"):
                return json.dumps({"sql": "SELECT * FROM other_dataset.secret"})
            return super()._reply(
                step, kind, step_id, is_follow_up, has_supplier_evidence, question
            )

    graph, guardrails = _setup(tmp_path, AlwaysBad())
    result = run_investigation(
        graph, "Why did brake complaints spike for the 2021 F-150?", "inv-bad"
    )
    assert result["status"] == "failed"
    error = result["error"]
    assert error is not None
    assert "other_dataset" in error
    assert result["attempts"] == 3
    assert len(guardrails.attempts_for("inv-bad")) == 4
