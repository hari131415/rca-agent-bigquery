"""Run the eval benchmark and report the metrics.

Usage (from the repo root):
    backend/.venv/bin/python eval/run.py [--set dev|test|all] [--out eval/results]

The harness builds the graph with the deterministic mock LLM and a
copy of the offline warehouse (the real warehouse is never opened
for writing), runs each benchmark question, grades the structured
verdict exactly — (cause_type, entity) — with no LLM judging, and
writes a JSON result file plus a Markdown report. The mock baseline
is the score of a scripted stand-in: its canned answers are written
for the benchmark's patterns, so it is a harness smoke test and an
upper bound on the graph's mechanics, not a prediction of a real
model's score (that is Phase 8).
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from rca_agent.config import GuardrailSettings  # noqa: E402
from rca_agent.graph.graph import run_investigation  # noqa: E402
from rca_agent.llm.mock import MockLLM  # noqa: E402
from rca_agent.services.budgets import BudgetTracker  # noqa: E402
from rca_agent.services.traces import DuckDBTraceStore  # noqa: E402
from rca_agent.sql.duckdb import DuckDBWarehouse  # noqa: E402
from rca_agent.sql.guardrails import Guardrails  # noqa: E402


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the RCA eval benchmark.")
    parser.add_argument("--set", choices=["dev", "test", "all"], default="all")
    parser.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = parser.parse_args()

    benchmark = json.loads((ROOT / "eval" / "benchmark.json").read_text(encoding="utf-8"))
    questions = benchmark["questions"]
    if args.set != "all":
        questions = [q for q in questions if q["set"] == args.set]

    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "offline.duckdb"
    shutil.copy(ROOT / "data" / "offline.duckdb", db_path)
    trace_store = DuckDBTraceStore(str(tmp / "traces.duckdb"))
    warehouse = DuckDBWarehouse(str(db_path))
    settings = GuardrailSettings()
    guardrails = Guardrails(
        settings,
        BudgetTracker(
            max_queries_per_investigation=settings.max_queries_per_investigation,
            max_bytes_per_day=settings.max_bytes_per_day,
        ),
    )
    from rca_agent.graph.graph import build_graph

    graph = build_graph(MockLLM(), warehouse, guardrails, trace_store=trace_store)

    results = []
    for question in questions:
        investigation_id = f"eval-{question['id']}"
        started = time.monotonic()
        state = run_investigation(graph, question["question"], investigation_id)
        latency_ms = (time.monotonic() - started) * 1000
        verdict = state.get("verdict") or {}
        expected = question["expected"]
        correct = (
            verdict.get("cause_type") == expected["cause_type"]
            and verdict.get("entity") == expected["entity"]
        )
        supporting = verdict.get("supporting_queries") or []
        evidence_count = len(state.get("evidence", []))
        supporting_valid = bool(supporting) and all(
            isinstance(i, int) and 0 <= i < evidence_count for i in supporting
        )
        traces = trace_store.list_traces(investigation_id)
        cost = sum(row.get("cost_usd") or 0.0 for row in traces)
        tokens = sum((row.get("tokens_in") or 0) + (row.get("tokens_out") or 0) for row in traces)
        attempts = guardrails.attempts_for(investigation_id)
        first_pass = bool(attempts) and attempts[0].rule == "allow"
        had_refusal = any(not attempt.allowed for attempt in attempts)
        results.append(
            {
                "id": question["id"],
                "set": question["set"],
                "pattern": question["pattern"],
                "question": question["question"],
                "expected": expected,
                "verdict": verdict,
                "correct": correct,
                "supporting_queries_valid": supporting_valid,
                "status": state.get("status"),
                "queries": len(state.get("queries", [])),
                "first_pass_sql": first_pass,
                "had_refusal": had_refusal,
                "unverified_claims": state.get("unverified_claims", 0),
                "warnings": state.get("warnings", []),
                "latency_ms": round(latency_ms, 1),
                "tokens": tokens,
                "cost_usd": cost,
            }
        )
        mark = "OK " if correct else "MISS"
        print(f"{mark} {question['id']} ({question['pattern']}): "
              f"expected {expected['cause_type']}/{expected['entity']}, "
              f"got {verdict.get('cause_type')}/{verdict.get('entity')}")

    # The adversarial set: every entry must be refused.
    adversarial = json.loads((ROOT / "eval" / "adversarial.json").read_text(encoding="utf-8"))
    blocked = []
    for i, entry in enumerate(adversarial["queries"]):
        verdict = guardrails.check(entry["sql"], warehouse, f"adv-{i}")
        blocked.append(
            {
                "sql": entry["sql"],
                "expected_rule": entry["expected_rule"],
                "refused": not verdict.allowed,
                "rule": verdict.rule,
                "rule_matches": verdict.rule == entry["expected_rule"],
            }
        )
    block_rate = sum(1 for b in blocked if b["refused"]) / len(blocked) if blocked else 0.0

    per_pattern = {}
    for pattern in sorted({r["pattern"] for r in results}):
        group = [r for r in results if r["pattern"] == pattern]
        group_correct = sum(1 for r in group if r["correct"])
        per_pattern[pattern] = {
            "questions": len(group),
            "correct": group_correct,
            "accuracy": round(group_correct / len(group), 4) if group else 0.0,
        }

    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    non_none = [r for r in results if r["expected"]["cause_type"] != "none"]
    non_none_correct = sum(1 for r in non_none if r["correct"])
    negatives = [r for r in results if r["pattern"].startswith("N")]
    false_discoveries = sum(1 for r in negatives if r["verdict"].get("cause_type") != "none")
    refused = [r for r in results if r["had_refusal"]]
    recovered = sum(1 for r in refused if r["status"] == "done")
    latencies = [r["latency_ms"] for r in results]
    metrics = {
        "set": args.set,
        "questions": total,
        "verdict_accuracy": round(correct / total, 4) if total else 0.0,
        "verdict_accuracy_non_none": (
            round(non_none_correct / len(non_none), 4) if non_none else 0.0
        ),
        "false_discovery_rate_negative_controls": (
            round(false_discoveries / len(negatives), 4) if negatives else 0.0
        ),
        "first_pass_sql_success": (
            round(sum(1 for r in results if r["first_pass_sql"]) / total, 4) if total else 0.0
        ),
        "self_correction_recovery": (
            round(recovered / len(refused), 4) if refused else 1.0
        ),
        "unverified_claim_rate": (
            round(sum(1 for r in results if r["unverified_claims"] > 0) / total, 4)
            if total
            else 0.0
        ),
        "unverified_claims_total": sum(r["unverified_claims"] for r in results),
        "rate_rule_warnings": sum(1 for r in results if r["warnings"]),
        "guardrail_block_rate_adversarial": round(block_rate, 4),
        "guardrail_rule_mismatches": sum(1 for b in blocked if not b["rule_matches"]),
        "cost_usd_total": round(sum(r["cost_usd"] for r in results), 6),
        "cost_usd_per_investigation": (
            round(sum(r["cost_usd"] for r in results) / total, 6) if total else 0.0
        ),
        "per_pattern": per_pattern,
        "latency_ms_median": round(_median(latencies), 1),
        "latency_ms_p95": round(_p95(latencies), 1),
        "tokens_total": sum(r["tokens"] for r in results),
    }

    stamp = time.strftime("%Y-%m-%d")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / f"{stamp}-{args.set}.json"
    result_path.write_text(
        json.dumps({"metrics": metrics, "results": results, "adversarial": blocked}, indent=2),
        encoding="utf-8",
    )
    report = _render_report(metrics, results, blocked)
    report_path = out_dir / f"{stamp}-{args.set}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n{report}")
    print(f"results: {result_path}")
    print(f"report: {report_path}")
    return 0


def _render_report(metrics: dict, results: list[dict], blocked: list[dict]) -> str:
    lines = [
        f"# Eval results ({metrics['set']} set, mock baseline)",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Verdict accuracy | {metrics['verdict_accuracy']:.1%} |",
        f"| Verdict accuracy, non-none verdicts | {metrics['verdict_accuracy_non_none']:.1%} |",
        f"| False-discovery rate (negative controls) | {metrics['false_discovery_rate_negative_controls']:.1%} |",
        f"| First-pass SQL success | {metrics['first_pass_sql_success']:.1%} |",
        f"| Self-correction recovery | {metrics['self_correction_recovery']:.1%} |",
        f"| Unverified-claim rate | {metrics['unverified_claim_rate']:.1%} ({metrics['unverified_claims_total']} claims) |",
        f"| Rate-rule warnings | {metrics['rate_rule_warnings']} investigations |",
        f"| Guardrail block rate (adversarial, {len(blocked)} queries) | {metrics['guardrail_block_rate_adversarial']:.1%} |",
        f"| Guardrail rule mismatches | {metrics['guardrail_rule_mismatches']} |",
        f"| Cost per investigation | ${metrics['cost_usd_per_investigation']:.4f} |",
        f"| Latency median / p95 | {metrics['latency_ms_median']} ms / {metrics['latency_ms_p95']} ms |",
        "",
        "## Per pattern",
        "",
        "| Pattern | Questions | Correct | Accuracy |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {pattern} | {m['questions']} | {m['correct']} | {m['accuracy']:.1%} |"
        for pattern, m in sorted(metrics["per_pattern"].items())
    ]
    lines += [
        "",
        "## Per-question",
        "",
        "| ID | Pattern | Expected | Got | Correct | Queries | First-pass | Unverified |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['pattern']} | {r['expected']['cause_type']}/{r['expected']['entity']} "
            f"| {r['verdict'].get('cause_type')}/{r['verdict'].get('entity')} "
            f"| {'yes' if r['correct'] else 'NO'} | {r['queries']} "
            f"| {'yes' if r['first_pass_sql'] else 'no'} | {r['unverified_claims']} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
