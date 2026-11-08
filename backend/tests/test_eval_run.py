"""The eval harness: a question that raises is recorded, not fatal.

The first live run died on a 429 eight questions in. _run_one_question
catches per-question exceptions, records them with the cost already
spent (from the traces), and the run continues.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

from rca_agent.config import GuardrailSettings
from rca_agent.graph.graph import build_graph
from rca_agent.llm.base import LLMResponse
from rca_agent.llm.mock import MockLLM
from rca_agent.services.traces import DuckDBTraceStore, new_trace
from rca_agent.sql.duckdb import DuckDBWarehouse
from rca_agent.sql.guardrails import Guardrails

ROOT = Path(__file__).resolve().parent.parent.parent
OFFLINE_DB = ROOT / "data" / "offline.duckdb"

_spec = importlib.util.spec_from_file_location("eval_run", ROOT / "eval" / "run.py")
assert _spec is not None and _spec.loader is not None
_eval_run = importlib.util.module_from_spec(_spec)
sys.modules["eval_run"] = _eval_run
_spec.loader.exec_module(_eval_run)

QUESTION = {
    "id": "T01",
    "set": "test",
    "pattern": "P1",
    "question": "Why did brake complaints spike for the 2021 F-150?",
    "expected": {"cause_type": "supplier", "entity": "acme-brakes"},
}


class _RaisingLLM(MockLLM):
    def complete(self, prompt: str) -> LLMResponse:
        if "[STEP analyze]" in prompt:
            raise RuntimeError("HTTP 429: quota exceeded")
        response = super().complete(prompt)
        # Zero out the mock's own token accounting: the only spend
        # this test measures is the trace logged before the run.
        return LLMResponse(text=response.text, tokens_in=0, tokens_out=0, cost_usd=0.0)


def test_run_one_question_records_error_and_spent_cost(tmp_path: Path) -> None:
    db_path = tmp_path / "offline.duckdb"
    shutil.copy(OFFLINE_DB, db_path)
    warehouse = DuckDBWarehouse(str(db_path))
    trace_store = DuckDBTraceStore(str(tmp_path / "traces.duckdb"))
    # Cost already spent on this investigation before it raised.
    # The investigation id carries the run stamp, so the pre-logged
    # trace uses the stamped id too.
    spent = new_trace("eval-T01-20261108-120000", "plan")
    spent.update({"tokens_in": 1200, "tokens_out": 292, "cost_usd": 0.12})
    trace_store.log(spent)
    guardrails = Guardrails(GuardrailSettings())
    graph = build_graph(_RaisingLLM(), warehouse, guardrails, trace_store=trace_store)
    result = _eval_run._run_one_question(
        graph, QUESTION, trace_store, guardrails, "20261108-120000"
    )
    assert result["status"] == "error"
    assert "429" in result["error"]
    assert result["correct"] is False
    assert result["cost_usd"] == 0.12
    assert result["tokens"] == 1492


def test_render_report_shows_abstentions_and_errors() -> None:
    metrics = {
        "set": "test",
        "llm": "vertex",
        "benchmark_version": "1.0",
        "regrade": False,
        "verdict_accuracy": 0.5,
        "verdict_accuracy_strict": 0.5,
        "verdict_accuracy_non_none": 0.5,
        "false_discovery_rate_negative_controls": 0.0,
        "false_attributions": 0,
        "false_attribution_total": 2,
        "false_attribution_rate": 0.0,
        "first_pass_sql_success": 1.0,
        "self_correction_recovery": 1.0,
        "unverified_claim_rate": 0.0,
        "unverified_claims_total": 0,
        "unverified_verdicts": 0,
        "unverified_verdict_rate": 0.0,
        "abstentions": 2,
        "abstention_rate": 0.25,
        "strict_abstentions": 2,
        "strict_abstention_rate": 0.25,
        "errors": 1,
        "rate_rule_warnings": 0,
        "guardrail_block_rate_adversarial": 1.0,
        "guardrail_rule_mismatches": 0,
        "cost_usd_per_investigation": 0.12,
        "max_cost_usd": 3.0,
        "cost_cap_reached": False,
        "latency_ms_median": 1000.0,
        "latency_ms_p95": 2000.0,
        "per_pattern": {"P1": {"questions": 8, "correct": 4, "accuracy": 0.5}},
    }
    results = [
        {
            "id": "T01",
            "pattern": "P1",
            "expected": {"cause_type": "supplier", "entity": "acme-brakes"},
            "verdict": {"cause_type": "insufficient_evidence", "entity": None},
            "correct": False,
            "status": "done",
            "queries": 0,
            "first_pass_sql": False,
            "unverified_claims": 0,
            "verdict_unverified": False,
        }
    ]
    report = _eval_run._render_report(metrics, results, [])
    assert "| Abstentions (insufficient_evidence) | 2 (25.0%) |" in report
    assert "| Errors (question raised) | 1 |" in report
    assert "insufficient_evidence" in report
    assert "| done |" in report
