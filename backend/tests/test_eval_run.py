"""The eval harness: a question that raises is recorded, not fatal.

The first live run died on a 429 eight questions in. _run_one_question
catches per-question exceptions, records them with the cost already
spent (from the traces), and the run continues.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
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


def test_grade_normalizes_entity_case_spacing_hyphens() -> None:
    expected: dict[str, object] = {"cause_type": "volume", "entity": "honda-pilot"}
    for entity in ("HONDA PILOT", "Honda Pilot", "honda-pilot"):
        assert _eval_run._grade(expected, {"cause_type": "volume", "entity": entity})
    expected = {
        "cause_type": "recall",
        "entity": "26V104000",
        "accepted_entities": ["26V104000", "22V193000"],
    }
    assert _eval_run._grade(expected, {"cause_type": "recall", "entity": "22v193000"})


def test_grade_multi_recall_entity_matches_nothing() -> None:
    expected: dict[str, object] = {
        "cause_type": "recall",
        "entity": "26V104000",
        "accepted_entities": ["26V104000", "22V193000"],
    }
    assert not _eval_run._grade(
        expected,
        {
            "cause_type": "recall",
            "entity": "21V533000, 22V193000, 23V509000, 25V132000, 26V104000",
        },
    )
    assert not _eval_run._grade(
        expected, {"cause_type": "recall", "entity": "23V858000, 21V215000"}
    )


def _saved_result(
    question_id: str,
    pattern: str,
    verdict: dict[str, object],
    *,
    unverified: bool,
) -> dict[str, object]:
    return {
        "id": question_id,
        "set": "dev",
        "pattern": pattern,
        "question": "Why?",
        "expected": {"cause_type": "none", "entity": None},
        "verdict": verdict,
        "correct": False,
        "supporting_queries_valid": True,
        "status": "done",
        "queries": 4,
        "first_pass_sql": True,
        "had_refusal": False,
        "unverified_claims": 0,
        "verdict_unverified": unverified,
        "warnings": [],
        "latency_ms": 1000.0,
        "tokens": 100,
        "cost_usd": 0.05,
    }


def test_regrade_applies_canonicalization_and_keeps_multi_recall_miss(
    tmp_path: Path,
) -> None:
    # The dev live run, in miniature: D06 is a format miss (the
    # right entity in the wrong case and spacing, flagged
    # ungrounded for the same reason); D13 names two campaigns
    # and stays a miss, grounded or not.
    saved = {
        "metrics": {
            "set": "dev",
            "llm": "vertex",
            "warehouse": "bigquery",
            "max_cost_usd": 4.0,
            "cost_cap_reached": False,
            "questions_skipped": [],
        },
        "results": [
            _saved_result(
                "D06",
                "P4",
                {"cause_type": "volume", "entity": "HONDA PILOT", "supporting_queries": [2]},
                unverified=True,
            ),
            _saved_result(
                "D13",
                "23V858000",
                {
                    "cause_type": "recall",
                    "entity": "23V858000, 21V215000",
                    "supporting_queries": [0],
                },
                unverified=True,
            ),
        ],
        "adversarial": [],
    }
    saved_path = tmp_path / "2026-11-08-dev.json"
    saved_path.write_text(json.dumps(saved), encoding="utf-8")
    benchmark = {
        "version": "1.1",
        "questions": [
            {
                "id": "D06",
                "set": "dev",
                "pattern": "P4",
                "question": "Why?",
                "expected": {"cause_type": "volume", "entity": "honda-pilot"},
            },
            {
                "id": "D13",
                "set": "dev",
                "pattern": "23V858000",
                "question": "Why?",
                "expected": {"cause_type": "recall", "entity": "23V858000"},
            },
        ],
    }
    args = argparse.Namespace(regrade=str(saved_path), out=str(tmp_path))
    assert _eval_run._regrade(args, benchmark, "1.1") == 0
    regraded = json.loads(
        (tmp_path / "2026-11-08-dev-v1.1-regrade.json").read_text(encoding="utf-8")
    )
    assert regraded["metrics"]["regrade"] is True
    assert regraded["metrics"]["verdict_accuracy"] == 0.5
    assert regraded["metrics"]["verdict_accuracy_strict"] == 0.5
    assert regraded["metrics"]["unverified_verdicts"] == 1
    by_id = {r["id"]: r for r in regraded["results"]}
    assert by_id["D06"]["correct"] is True
    assert by_id["D06"]["verdict_unverified"] is False
    assert by_id["D13"]["correct"] is False
    assert by_id["D13"]["verdict_unverified"] is True
    report = (tmp_path / "2026-11-08-dev-v1.1-regrade.md").read_text(encoding="utf-8")
    assert "post-hoc regrade" in report
    assert "canonicalization" in report
