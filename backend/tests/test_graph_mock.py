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


def test_corrections_exhausted_abstains(tmp_path: Path) -> None:
    """Corrections spent, no evidence: the investigation ends done
    with an explicit insufficient_evidence verdict, not failed.
    There are no dead ends — a refused query goes to analyze."""

    class AlwaysBad(MockLLM):
        """A different bad query each time, so the identical-SQL
        stop never triggers and all three corrections are spent."""

        def __init__(self) -> None:
            self.n = 0

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
                self.n += 1
                return json.dumps({"sql": f"SELECT * FROM other_dataset.secret_{self.n}"})
            return super()._reply(
                step, kind, step_id, is_follow_up, has_supplier_evidence, question
            )

    graph, guardrails = _setup(tmp_path, AlwaysBad())
    result = run_investigation(
        graph, "Why did brake complaints spike for the 2021 F-150?", "inv-bad"
    )
    assert result["status"] == "done", result.get("error")
    assert result["verdict"] == {
        "cause_type": "insufficient_evidence",
        "entity": None,
        "supporting_queries": [],
    }
    assert result["attempts"] == 3
    assert len(guardrails.attempts_for("inv-bad")) == 4
    answer = result["answer"]
    assert answer is not None
    assert "No supporting evidence found." in answer
    assert "insufficient_evidence" in answer


def test_identical_correction_goes_to_analyze(tmp_path: Path) -> None:
    """A correction that returns the same SQL is not a correction:
    the graph routes to analyze after one attempt instead of
    burning all three."""

    class RepeatsBad(MockLLM):
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

    graph, guardrails = _setup(tmp_path, RepeatsBad())
    result = run_investigation(
        graph, "Why did brake complaints spike for the 2021 F-150?", "inv-repeat"
    )
    assert result["status"] == "done", result.get("error")
    assert result["verdict"] is not None
    assert result["verdict"]["cause_type"] == "insufficient_evidence"
    assert result["attempts"] == 1
    assert len(guardrails.attempts_for("inv-repeat")) == 1


def test_string_steps_and_hypotheses_are_normalized(tmp_path: Path) -> None:
    """A plan that returns steps and hypotheses as bare strings
    (as the first live run did) is normalized instead of crashing
    build_sql_prompt on step.get."""

    class StringPlan(MockLLM):
        def _reply(
            self,
            step: str,
            kind: str,
            step_id: str,
            is_follow_up: bool,
            has_supplier_evidence: bool,
            question: str = "",
        ) -> str:
            if step == "plan":
                return json.dumps(
                    {
                        "steps": ["Compare brake complaint rates by plant for 2021."],
                        "hypotheses": ["Riverside has the highest brake rate."],
                    }
                )
            return super()._reply(
                step, kind, step_id, is_follow_up, has_supplier_evidence, question
            )

    graph, _ = _setup(tmp_path, StringPlan())
    result = run_investigation(
        graph, "Why did brake complaints spike for the 2021 F-150?", "inv-str"
    )
    assert result["status"] == "done", result.get("error")
    assert result["steps"] == [
        {
            "id": "S1",
            "description": "Compare brake complaint rates by plant for 2021.",
            "hypotheses": [],
        }
    ]
    assert result["hypotheses"] == [
        {"id": "H1", "text": "Riverside has the highest brake rate.", "test": ""}
    ]
    assert "Riverside" in (result["answer"] or "")
