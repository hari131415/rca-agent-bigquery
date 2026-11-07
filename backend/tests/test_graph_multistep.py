"""Multi-step investigations, the claim validator, and execution-error
self-correction — all through the graph with the mock LLM, against
a copy of the real offline warehouse."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from langgraph.graph import StateGraph

from rca_agent.config import GuardrailSettings
from rca_agent.graph.graph import build_graph, run_investigation
from rca_agent.graph.state import InvestigationState
from rca_agent.graph.validate import validate_claims
from rca_agent.llm.mock import MockLLM
from rca_agent.sql.duckdb import DuckDBWarehouse
from rca_agent.sql.guardrails import Guardrails
from rca_agent.sql.warehouse import DryRunResult, Warehouse

ROOT = Path(__file__).resolve().parent.parent.parent
OFFLINE_DB = ROOT / "data" / "offline.duckdb"


def _setup(
    tmp_path: Path, llm: MockLLM | None = None, settings: GuardrailSettings | None = None
) -> tuple[StateGraph[InvestigationState], Guardrails]:
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(db_path)
    guardrails = Guardrails(settings or GuardrailSettings())
    graph = build_graph(llm or MockLLM(), warehouse, guardrails)
    return graph, guardrails


def test_pilot_volume_decoy(tmp_path: Path) -> None:
    """The P4 demo: a raw count spike, then a volume check, and the
    agent concludes the rate is flat."""
    graph, _ = _setup(tmp_path)
    result = run_investigation(
        graph, "Why did total complaints rise for the Honda Pilot in 2021?", "inv-p4"
    )
    assert result["status"] == "done", result.get("error")
    assert len(result["queries"]) == 2
    assert len(result["evidence"]) == 2
    counts = result["evidence"][0]["rows"]
    assert [row["n"] for row in counts] == [1047, 1056, 2284]
    rates = result["evidence"][1]["rows"]
    assert abs(rates[1]["rate_per_1000"] - 7.55) < 0.05
    assert abs(rates[2]["rate_per_1000"] - 7.93) < 0.05
    answer = result["answer"]
    assert answer is not None
    assert "7.55" in answer and "7.93" in answer
    assert "(synthetic)" in answer
    assert result["unverified_claims"] == 0


def test_follow_up_supplier(tmp_path: Path) -> None:
    """The analyze step asks for one follow-up query when the
    evidence cannot distinguish two hypotheses."""
    graph, _ = _setup(tmp_path)
    result = run_investigation(
        graph, "Is the brake spike at Riverside a supplier issue?", "inv-sup"
    )
    assert result["status"] == "done", result.get("error")
    assert result["follow_up_done"] is True
    assert len(result["queries"]) == 2
    assert "SERVICE BRAKES" in result["queries"][0]["sql"]
    assert "plant_id" in result["queries"][0]["sql"]
    assert "rate_per_1000" in result["queries"][1]["sql"]
    rates = {row["supplier_id"]: row["rate_per_1000"] for row in result["evidence"][1]["rows"]}
    assert abs(rates["acme-brakes"] - 7.20) < 0.05
    assert abs(rates["beacon-brakes"] - 1.17) < 0.05
    answer = result["answer"]
    assert answer is not None
    assert "7.20" in answer and "1.17" in answer
    assert result["unverified_claims"] == 0
    assert result.get("warnings") == []


def test_query_cap_shared_across_steps(tmp_path: Path) -> None:
    """All steps share one per-investigation query cap: with a cap of
    1, the second step's query is refused and the investigation fails."""
    settings = GuardrailSettings(max_queries_per_investigation=1)
    graph, guardrails = _setup(tmp_path, settings=settings)
    result = run_investigation(
        graph, "Why did total complaints rise for the Honda Pilot in 2021?", "inv-cap"
    )
    assert result["status"] == "failed"
    assert result["error"] is not None and "has used 1 of 1 allowed queries" in result["error"]
    assert len(result["queries"]) == 1
    assert [v.rule for v in guardrails.attempts_for("inv-cap")] == ["allow", "query-cap"]


def test_max_steps_limits_plan(tmp_path: Path) -> None:
    """A two-step plan with max_steps=1 runs only the first step."""
    graph, _ = _setup(tmp_path)
    result = run_investigation(
        graph,
        "Why did total complaints rise for the Honda Pilot in 2021?",
        "inv-max",
        max_steps=1,
    )
    assert result["status"] == "done", result.get("error")
    assert len(result["queries"]) == 1


def test_campaign_number_is_not_split_into_numbers() -> None:
    """A recall campaign number is an identifier, not a measurement:
    citing "26V104000" must not be read as the numbers 26 and 104000."""
    analysis = {
        "summary": "Recall 26V104000 covers the 2021 F-150.",
        "ranked_hypotheses": [
            {
                "id": "H1",
                "rank": 1,
                "verdict": "supported",
                "evidence": "Recall 26V104000 covers the 2021-2026 F-150, "
                "4,381,878 units affected.",
            }
        ],
    }
    evidence = [
        {
            "sql": "SELECT campno, potaff FROM rca_agent.recalls",
            "rows": [{"campno": "26V104000", "potaff": 4381878, "model_year": 2021}],
        }
    ]
    validated, unverified = validate_claims(analysis, evidence)
    assert unverified == 0
    assert validated["summary_verified"] is True
    assert validated["ranked_hypotheses"][0]["verified"] is True


def test_claim_validator_fabricated_number(tmp_path: Path) -> None:
    """A mock that returns a fabricated rate is caught: the claim is
    marked unverified and counted."""

    class Fabricating(MockLLM):
        def _reply(
            self,
            step: str,
            kind: str,
            step_id: str,
            is_follow_up: bool,
            has_supplier_evidence: bool,
            question: str = "",
        ) -> str:
            if step == "analyze" and kind == "brake2021":
                return json.dumps(
                    {
                        "summary": "Brake complaints for model year 2021 concentrate at Riverside.",
                        "ranked_hypotheses": [
                            {
                                "id": "H1",
                                "rank": 1,
                                "verdict": "supported",
                                "evidence": "Riverside brake rate 9.99 per 1,000, "
                                "against 1.22 at the next plant.",
                            }
                        ],
                    }
                )
            return super()._reply(
                step, kind, step_id, is_follow_up, has_supplier_evidence, question
            )

    graph, _ = _setup(tmp_path, Fabricating())
    result = run_investigation(
        graph, "Why did brake complaints spike for the 2021 F-150?", "inv-fab"
    )
    assert result["status"] == "done", result.get("error")
    assert result["unverified_claims"] == 1
    answer = result["answer"]
    assert answer is not None
    assert "(unverified)" in answer
    assert "Unverified claims: 1" in answer


def test_count_only_comparison_flagged(tmp_path: Path) -> None:
    """A group comparison that cites counts with no rate or volume in
    the evidence is flagged — the P4 trap, applied to suppliers."""

    class CountOnly(MockLLM):
        def _reply(
            self,
            step: str,
            kind: str,
            step_id: str,
            is_follow_up: bool,
            has_supplier_evidence: bool,
            question: str = "",
        ) -> str:
            if step == "sql" and is_follow_up:
                return json.dumps(
                    {
                        "sql": "SELECT supplier_id, COUNT(*) AS n "
                        "FROM rca_agent.complaints "
                        "WHERE is_synthetic AND compdesc = 'SERVICE BRAKES' "
                        "AND model_year = 2021 AND plant_id = 'riverside' "
                        "GROUP BY supplier_id ORDER BY n DESC"
                    }
                )
            if step == "analyze" and has_supplier_evidence:
                return json.dumps(
                    {
                        "summary": "The brake spike at Riverside is a supplier effect: Acme pads.",
                        "ranked_hypotheses": [
                            {
                                "id": "H1",
                                "rank": 1,
                                "verdict": "supported",
                                "evidence": "Acme supplied 956 of 1,116 Riverside brake "
                                "complaints, against 160 from Beacon.",
                            }
                        ],
                    }
                )
            return super()._reply(
                step, kind, step_id, is_follow_up, has_supplier_evidence, question
            )

    graph, _ = _setup(tmp_path, CountOnly())
    result = run_investigation(
        graph, "Is the brake spike at Riverside a supplier issue?", "inv-count"
    )
    assert result["status"] == "done", result.get("error")
    assert result["unverified_claims"] == 0  # the counts are real; the comparison is the flaw
    assert len(result["warnings"]) == 1
    assert "rate per 1,000" in result["warnings"][0]
    answer = result["answer"]
    assert answer is not None
    assert "Warnings" in answer and "Counts alone cannot tell a defect apart" in answer


def test_execution_error_recovers(tmp_path: Path) -> None:
    """A warehouse failure on the first execute self-corrects within
    the same attempt limit instead of crashing the investigation."""

    class FlakyWarehouse(Warehouse):
        def __init__(self, inner: Warehouse) -> None:
            self.inner = inner
            self.name = inner.name
            self.calls = 0

        def dry_run(self, sql: str) -> DryRunResult:
            return self.inner.dry_run(sql)

        def execute(self, sql: str) -> list[dict[str, Any]]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("connection reset by peer")
            return self.inner.execute(sql)

    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    inner = DuckDBWarehouse(db_path)
    warehouse = FlakyWarehouse(inner)
    guardrails = Guardrails(GuardrailSettings())
    graph = build_graph(MockLLM(), warehouse, guardrails)
    result = run_investigation(
        graph, "Why did brake complaints spike for the 2021 F-150?", "inv-flaky"
    )
    assert result["status"] == "done", result.get("error")
    assert result["attempts"] == 1
    assert [q["rule"] for q in result["queries"]] == ["execution-error", "allow"]
    assert "connection reset" in result["queries"][0]["error"]
    answer = result["answer"]
    assert answer is not None and "Riverside" in answer
