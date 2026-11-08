"""Run the eval benchmark and report the metrics.

Usage (from the repo root):
    backend/.venv/bin/python eval/run.py [--set dev|test|all] [--out eval/results]

Live mode (Phase 8, against real BigQuery + Vertex AI):
    backend/.venv/bin/python eval/run.py --set test --llm vertex --warehouse bigquery \
        --project rca-agent-portfolio [--max-cost-usd 3.00]

The harness builds the graph, runs each benchmark question, grades
the structured verdict exactly — (cause_type, entity) — with no
LLM judging, and writes a JSON result file plus a Markdown report.
The mock baseline is the score of a scripted stand-in: its canned
answers are written for the benchmark's patterns, so it is a
harness smoke test and an upper bound on the graph's mechanics,
not a prediction of a real model's score (that is Phase 8).

The cost cap (--max-cost-usd, default $3.00, the approved Phase 8
hard cap) stops the run cleanly when the cumulative model cost
reaches it: the questions already run are graded and reported, the
rest are listed as skipped, and the exit code is 0. The cap is
checked after every investigation, so the overshoot is at most one
investigation's cost.

A question that raises (a 429 that survived the client's retries,
a node failure, a warehouse error) does not crash the run: it is
recorded with status "error" and the cost already spent on it
(from the traces), and the run continues with the next question.
Abstentions — verdict insufficient_evidence, the agent declining
to name a cause — are counted separately from wrong answers, and
the strict accuracy counts ungrounded verdicts as abstentions
too. The false-attribution rate counts verdicts that name an
entity on questions whose expected cause has none (none or
fleet_wide): the false-discovery rate hides those, because it
only counts negative controls.

Benchmark versions: v1.0 is frozen. v1.1 (eval/benchmark-v1.1.json)
changes only the recall questions T14/T15/T17, whose expected
verdict is the set of campaigns matching the make/model/year/
component — naming any of them is correct. A saved run can be
regraded under another version with --regrade <results.json>
--benchmark <file>: the verdicts are re-graded and the metrics
recomputed, with no model calls.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))

from langgraph.graph import StateGraph  # noqa: E402

from rca_agent.config import GuardrailSettings, VertexSettings  # noqa: E402
from rca_agent.graph.graph import run_investigation  # noqa: E402
from rca_agent.graph.state import InvestigationState  # noqa: E402
from rca_agent.llm.mock import MockLLM  # noqa: E402
from rca_agent.services.budgets import BudgetTracker, StoreLedger  # noqa: E402
from rca_agent.services.store import FirestoreStore  # noqa: E402
from rca_agent.services.traces import BigQueryTraceStore, DuckDBTraceStore  # noqa: E402
from rca_agent.sql.bigquery import BigQueryWarehouse  # noqa: E402
from rca_agent.sql.duckdb import DuckDBWarehouse  # noqa: E402
from rca_agent.sql.guardrails import Guardrails  # noqa: E402


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]


def _trace_cost(trace_store: object, investigation_id: str) -> tuple[float, int]:
    """The cost and tokens already spent on one investigation,
    from its traces. Works for both stores — this is what the
    $3 cap is accounted from, so a question that errors still
    counts what it spent."""
    traces = trace_store.list_traces(investigation_id)  # type: ignore[attr-defined]
    cost = sum(row.get("cost_usd") or 0.0 for row in traces)
    tokens = sum((row.get("tokens_in") or 0) + (row.get("tokens_out") or 0) for row in traces)
    return cost, tokens


def _grade(expected: dict, verdict: dict) -> bool:
    """Exact verdict grading, with an accepted set for recall
    questions where several campaigns match (benchmark v1.1: the
    expected entity is the set of campaigns covering the
    make/model/year/component, and naming any of them is
    correct)."""
    if verdict.get("cause_type") != expected.get("cause_type"):
        return False
    accepted = expected.get("accepted_entities")
    if accepted is not None:
        return verdict.get("entity") in accepted
    return verdict.get("entity") == expected.get("entity")


def _expected_display(expected: dict) -> str:
    accepted = expected.get("accepted_entities")
    if accepted is not None:
        return f"{expected['cause_type']}/{' or '.join(accepted)}"
    return f"{expected['cause_type']}/{expected['entity']}"


def _adc_identity() -> str:
    """The ADC identity, resolved to a real email. On a GCE VM the
    credentials report the service account email as "default";
    the metadata server gives the real address (the second live
    run printed "running as: default")."""
    try:
        import google.auth

        creds, _ = google.auth.default()
        email = getattr(creds, "service_account_email", None)
    except Exception as exc:  # noqa: BLE001 - identity is a check, not a gate
        return f"unknown ({exc})"
    if email and email != "default":
        return str(email)
    if email == "default":
        try:
            import urllib.request

            req = urllib.request.Request(
                "http://metadata.google.internal/computeMetadata/v1/"
                "instance/service-accounts/default/email",
                headers={"Metadata-Flavor": "Google"},
            )
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception:  # noqa: BLE001 - the VM metadata server is optional
            return "default (metadata server unreachable)"
    return "unknown"


def _run_one_question(
    graph: StateGraph[InvestigationState],
    question: dict,
    trace_store: object,
    guardrails: Guardrails,
    run_stamp: str = "",
) -> dict:
    """Run one benchmark question and grade it. Never raises: a
    question that crashes is recorded with status "error" and the
    cost already spent, so one bad question cannot kill the run
    (the first live run died on a 429 eight questions in). The
    investigation id carries the run timestamp (eval-<id>-<stamp>):
    reusing the bare question id across runs made list_traces mix
    costs from different runs, so a stamp is always generated when
    the caller does not pass one."""
    stamp = run_stamp or time.strftime("%Y%m%d-%H%M%S")
    investigation_id = f"eval-{question['id']}-{stamp}"
    started = time.monotonic()
    try:
        state = run_investigation(graph, question["question"], investigation_id)
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        latency_ms = (time.monotonic() - started) * 1000
        cost, tokens = _trace_cost(trace_store, investigation_id)
        attempts = guardrails.attempts_for(investigation_id)
        return {
            "id": question["id"],
            "set": question["set"],
            "pattern": question["pattern"],
            "question": question["question"],
            "expected": question["expected"],
            "verdict": {},
            "correct": False,
            "supporting_queries_valid": False,
            "status": "error",
            "error": str(exc),
            "queries": len(attempts),
            "first_pass_sql": False,
            "had_refusal": any(not attempt.allowed for attempt in attempts),
            "unverified_claims": 0,
            "verdict_unverified": False,
            "warnings": [],
            "latency_ms": round(latency_ms, 1),
            "tokens": tokens,
            "cost_usd": cost,
        }
    latency_ms = (time.monotonic() - started) * 1000
    verdict = state.get("verdict") or {}
    expected = question["expected"]
    correct = _grade(expected, verdict)
    supporting = verdict.get("supporting_queries") or []
    evidence_count = len(state.get("evidence", []))
    supporting_valid = bool(supporting) and all(
        isinstance(i, int) and 0 <= i < evidence_count for i in supporting
    )
    cost, tokens = _trace_cost(trace_store, investigation_id)
    attempts = guardrails.attempts_for(investigation_id)
    first_pass = bool(attempts) and attempts[0].rule == "allow"
    had_refusal = any(not attempt.allowed for attempt in attempts)
    return {
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
        "verdict_unverified": state.get("verdict_unverified", False),
        "warnings": state.get("warnings", []),
        "latency_ms": round(latency_ms, 1),
        "tokens": tokens,
        "cost_usd": cost,
    }


def _compute_metrics(
    results: list[dict],
    blocked: list[dict],
    *,
    set_name: str,
    llm: str,
    warehouse: str,
    max_cost_usd: float,
    cap_reached: bool,
    skipped: list[str],
    benchmark_version: str,
    regrade: bool = False,
) -> dict:
    """All metrics for a run, from its per-question results. Used
    by a fresh run and by a regrade of saved results, so the two
    can never disagree about what a number means."""
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
    # False attribution: the question's expected cause names no
    # entity (none or fleet_wide), but the verdict names one. The
    # false-discovery rate hides this — it only counts negative
    # controls, and a fleet_wide question is not a negative
    # control — so it is reported alongside FDR. The second live
    # run scored 4/8 here (T07, T09, T10, T11) while FDR was 0%.
    no_entity = [
        r for r in results if r["expected"]["cause_type"] in ("none", "fleet_wide")
    ]
    false_attributions = sum(
        1 for r in no_entity if r["verdict"].get("entity") is not None
    )
    refused = [r for r in results if r["had_refusal"]]
    recovered = sum(1 for r in refused if r["status"] == "done")
    latencies = [r["latency_ms"] for r in results]
    abstentions = sum(
        1 for r in results if r["verdict"].get("cause_type") == "insufficient_evidence"
    )
    ungrounded = sum(1 for r in results if r["verdict_unverified"])
    strict_correct = sum(
        1 for r in results if r["correct"] and not r["verdict_unverified"]
    )
    block_rate = sum(1 for b in blocked if b["refused"]) / len(blocked) if blocked else 0.0
    return {
        "set": set_name,
        "questions": total,
        "llm": llm,
        "warehouse": warehouse,
        "benchmark_version": benchmark_version,
        "regrade": regrade,
        "max_cost_usd": max_cost_usd,
        "cost_cap_reached": cap_reached,
        "questions_skipped": skipped,
        "verdict_accuracy": round(correct / total, 4) if total else 0.0,
        "verdict_accuracy_non_none": (
            round(non_none_correct / len(non_none), 4) if non_none else 0.0
        ),
        "false_discovery_rate_negative_controls": (
            round(false_discoveries / len(negatives), 4) if negatives else 0.0
        ),
        "false_attributions": false_attributions,
        "false_attribution_total": len(no_entity),
        "false_attribution_rate": (
            round(false_attributions / len(no_entity), 4) if no_entity else 0.0
        ),
        "first_pass_sql_success": (
            round(sum(1 for r in results if r["first_pass_sql"]) / total, 4) if total else 0.0
        ),
        "self_correction_recovery": (round(recovered / len(refused), 4) if refused else 1.0),
        "unverified_claim_rate": (
            round(sum(1 for r in results if r["unverified_claims"] > 0) / total, 4)
            if total
            else 0.0
        ),
        "unverified_claims_total": sum(r["unverified_claims"] for r in results),
        # A verdict whose entity appears in no supporting query's
        # result rows for its dimension. The verdict may still be
        # the expected one (the mock's P1 verdict is right but
        # ungrounded); this metric counts the grounding gap
        # separately from verdict accuracy.
        "unverified_verdicts": ungrounded,
        "unverified_verdict_rate": (
            round(ungrounded / total, 4) if total else 0.0
        ),
        # Abstentions are not wrong answers: the agent declined to
        # name a cause (verdict insufficient_evidence) instead of
        # guessing. They count as misses in accuracy, and are
        # reported separately here.
        "abstentions": abstentions,
        "abstention_rate": (
            round(abstentions / total, 4) if total else 0.0
        ),
        # Strict accuracy treats an ungrounded verdict as an
        # abstention: right answer, no supporting row, does not
        # count as correct. The lenient accuracy is the original
        # metric; both are reported.
        "verdict_accuracy_strict": (
            round(strict_correct / total, 4) if total else 0.0
        ),
        "strict_abstentions": abstentions + ungrounded,
        "strict_abstention_rate": (
            round((abstentions + ungrounded) / total, 4) if total else 0.0
        ),
        # Questions that raised (a 429, a node failure) instead of
        # completing. Their cost is still counted.
        "errors": sum(1 for r in results if r["status"] == "error"),
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


def _regrade(args: argparse.Namespace, benchmark: dict, benchmark_version: str) -> int:
    """Regrade a saved results file under a different benchmark
    version, without re-running the model. The verdicts are
    re-graded (the v1.1 change is the recall accepted set), the
    metrics are recomputed through the same _compute_metrics as a
    fresh run, and a <name>-v<version>-regrade.json/.md pair is
    written to --out. Costs, latencies, and the adversarial
    results come from the saved file unchanged."""
    saved = json.loads(Path(args.regrade).read_text(encoding="utf-8"))
    saved_metrics = saved["metrics"]
    by_id = {q["id"]: q for q in benchmark["questions"]}
    results = []
    for saved_result in saved["results"]:
        question = by_id.get(saved_result["id"])
        if question is None:
            continue
        result = dict(saved_result)
        result["expected"] = question["expected"]
        result["correct"] = _grade(question["expected"], result.get("verdict") or {})
        results.append(result)
    blocked = saved.get("adversarial", [])
    metrics = _compute_metrics(
        results,
        blocked,
        set_name=saved_metrics["set"],
        llm=saved_metrics["llm"],
        warehouse=saved_metrics.get("warehouse", "duckdb"),
        max_cost_usd=saved_metrics["max_cost_usd"],
        cap_reached=saved_metrics["cost_cap_reached"],
        skipped=saved_metrics.get("questions_skipped", []),
        benchmark_version=benchmark_version,
        regrade=True,
    )
    stem = Path(args.regrade).stem
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / f"{stem}-v{benchmark_version}-regrade.json"
    result_path.write_text(
        json.dumps({"metrics": metrics, "results": results, "adversarial": blocked}, indent=2),
        encoding="utf-8",
    )
    report = _render_report(metrics, results, blocked)
    report_path = out_dir / f"{stem}-v{benchmark_version}-regrade.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n{report}")
    print(f"results: {result_path}")
    print(f"report: {report_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the RCA eval benchmark.")
    parser.add_argument("--set", choices=["dev", "test", "all"], default="all")
    parser.add_argument("--out", default=str(ROOT / "eval" / "results"))
    parser.add_argument("--llm", choices=["mock", "vertex"], default="mock")
    parser.add_argument("--warehouse", choices=["duckdb", "bigquery"], default="duckdb")
    parser.add_argument("--project", default=os.environ.get("GCP_PROJECT", ""))
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=3.00,
        help="Hard cap on total model cost (USD). The run stops cleanly when reached.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=0.0,
        help="Seconds to wait between questions (live runs pass 2, to stay under the rate limit).",
    )
    parser.add_argument(
        "--benchmark",
        default=str(ROOT / "eval" / "benchmark.json"),
        help="Benchmark file to run (v1.0 is the frozen default; v1.1 is eval/benchmark-v1.1.json).",
    )
    parser.add_argument(
        "--regrade",
        default=None,
        help="Regrade a saved results JSON under --benchmark instead of running: "
        "the verdicts are re-graded, the metrics recomputed, and a "
        "<name>-v<version>-regrade.json/.md pair written to --out.",
    )
    args = parser.parse_args()

    benchmark = json.loads(Path(args.benchmark).read_text(encoding="utf-8"))
    benchmark_version = benchmark.get("version", "1.0")

    if args.regrade:
        return _regrade(args, benchmark, benchmark_version)

    questions = benchmark["questions"]
    if args.set != "all":
        questions = [q for q in questions if q["set"] == args.set]

    live = args.llm == "vertex" or args.warehouse == "bigquery"
    if live and not args.project:
        print("error: --project (or GCP_PROJECT) is required for the live eval", file=sys.stderr)
        return 2
    if args.llm == "vertex" and args.warehouse == "duckdb":
        print(
            "error: --llm vertex needs --warehouse bigquery (the live model "
            "queries the live warehouse)",
            file=sys.stderr,
        )
        return 2

    settings = GuardrailSettings.from_env()
    if live:
        # Print the identity the eval runs as, so the impersonation
        # (runbook step: live 4) is verifiable from the output: it
        # must be the runner service account, not the owner's
        # account, or the read-only IAM boundary is not exercised.
        # On a VM the credentials report "default"; the metadata
        # server resolves that to the real service account email.
        print(f"running as: {_adc_identity()}")
        # The live eval runs against the real BigQuery warehouse and
        # the Vertex LLM, with traces in rca_ops and the daily byte
        # ledger in Firestore (so the cap survives a restart).
        warehouse = BigQueryWarehouse(args.project, os.environ.get("BIGQUERY_DATASET", "rca_agent"))
        trace_store = BigQueryTraceStore(
            args.project, os.environ.get("BIGQUERY_TRACES_DATASET", "rca_ops")
        )
        store = FirestoreStore(os.environ.get("FIRESTORE_DATABASE", "rca-agent"))
        budget = BudgetTracker(
            settings.max_queries_per_investigation,
            settings.max_bytes_per_day,
            ledger=StoreLedger(store),
        )
        from rca_agent.llm.vertex import VertexLLM

        vertex = VertexSettings.from_env()
        llm = VertexLLM(
            project=args.project,
            location=vertex.location,
            model_id=vertex.model_id,
            thinking_budget=vertex.thinking_budget,
        )
    else:
        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "offline.duckdb"
        shutil.copy(ROOT / "data" / "offline.duckdb", db_path)
        trace_store = DuckDBTraceStore(str(tmp / "traces.duckdb"))
        warehouse = DuckDBWarehouse(str(db_path))
        budget = BudgetTracker(
            max_queries_per_investigation=settings.max_queries_per_investigation,
            max_bytes_per_day=settings.max_bytes_per_day,
        )
        llm = MockLLM()
    guardrails = Guardrails(settings, budget)
    from rca_agent.graph.graph import build_graph

    graph = build_graph(llm, warehouse, guardrails, trace_store=trace_store)

    results = []
    skipped: list[str] = []
    total_cost = 0.0
    # One stamp for the whole run: the investigation id is
    # eval-<question>-<stamp>, unique per run.
    run_stamp = time.strftime("%Y%m%d-%H%M%S")
    for index, question in enumerate(questions):
        if total_cost >= args.max_cost_usd:
            skipped.append(question["id"])
            continue
        if index > 0 and args.delay_seconds > 0:
            time.sleep(args.delay_seconds)
        result = _run_one_question(graph, question, trace_store, guardrails, run_stamp)
        total_cost += result["cost_usd"]
        results.append(result)
        if result["status"] == "error":
            print(
                f"ERROR {question['id']} ({question['pattern']}): {result.get('error')} "
                f"(${result['cost_usd']:.4f}, total ${total_cost:.4f})"
            )
            continue
        verdict = result["verdict"]
        mark = "OK " if result["correct"] else "MISS"
        if verdict.get("cause_type") == "insufficient_evidence":
            mark = "ABSTAIN"
        print(
            f"{mark} {question['id']} ({question['pattern']}): "
            f"expected {_expected_display(question['expected'])}, "
            f"got {verdict.get('cause_type')}/{verdict.get('entity')} "
            f"(${result['cost_usd']:.4f}, total ${total_cost:.4f})"
        )
    cap_reached = bool(skipped)
    if cap_reached:
        print(
            f"\nSTOPPED at the ${args.max_cost_usd:.2f} cost cap after {len(results)} "
            f"questions; skipped: {', '.join(skipped)}. The questions already run "
            "are graded and reported below."
        )

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
    metrics = _compute_metrics(
        results,
        blocked,
        set_name=args.set,
        llm=args.llm,
        warehouse=args.warehouse,
        max_cost_usd=args.max_cost_usd,
        cap_reached=cap_reached,
        skipped=skipped,
        benchmark_version=benchmark_version,
    )

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
    mode = "live (Vertex AI)" if metrics["llm"] == "vertex" else "mock baseline"
    title = f"# Eval results ({metrics['set']} set, {mode}, benchmark v{metrics['benchmark_version']})"
    if metrics.get("regrade"):
        title += " — post-hoc regrade"
    lines = [
        title,
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Verdict accuracy | {metrics['verdict_accuracy']:.1%} |",
        f"| Verdict accuracy, strict (ungrounded verdicts count as abstentions) | {metrics['verdict_accuracy_strict']:.1%} |",
        f"| Verdict accuracy, non-none verdicts | {metrics['verdict_accuracy_non_none']:.1%} |",
        f"| False-discovery rate (negative controls) | {metrics['false_discovery_rate_negative_controls']:.1%} |",
        f"| False-attribution rate (no-entity questions) | {metrics['false_attributions']}/{metrics['false_attribution_total']} ({metrics['false_attribution_rate']:.1%}) |",
        f"| First-pass SQL success | {metrics['first_pass_sql_success']:.1%} |",
        f"| Self-correction recovery | {metrics['self_correction_recovery']:.1%} |",
        f"| Unverified-claim rate | {metrics['unverified_claim_rate']:.1%} ({metrics['unverified_claims_total']} claims) |",
        f"| Unverified verdicts | {metrics['unverified_verdicts']} "
        f"({metrics['unverified_verdict_rate']:.1%}) |",
        f"| Abstentions (insufficient_evidence) | {metrics['abstentions']} "
        f"({metrics['abstention_rate']:.1%}) |",
        f"| Strict abstentions (insufficient_evidence + ungrounded) | {metrics['strict_abstentions']} "
        f"({metrics['strict_abstention_rate']:.1%}) |",
        f"| Errors (question raised) | {metrics['errors']} |",
        f"| Rate-rule warnings | {metrics['rate_rule_warnings']} investigations |",
        f"| Guardrail block rate (adversarial, {len(blocked)} queries) | {metrics['guardrail_block_rate_adversarial']:.1%} |",
        f"| Guardrail rule mismatches | {metrics['guardrail_rule_mismatches']} |",
        f"| Cost per investigation | ${metrics['cost_usd_per_investigation']:.4f} |",
        f"| Cost cap | ${metrics['max_cost_usd']:.2f} "
        f"({'reached' if metrics['cost_cap_reached'] else 'not reached'}) |",
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
        "| ID | Pattern | Expected | Got | Correct | Status | Queries | "
        "1st-pass | Unverif | Ungrounded |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['id']} | {r['pattern']} | {_expected_display(r['expected'])} "
            f"| {r['verdict'].get('cause_type')}/{r['verdict'].get('entity')} "
            f"| {'yes' if r['correct'] else 'NO'} | {r['status']} | {r['queries']} "
            f"| {'yes' if r['first_pass_sql'] else 'no'} | {r['unverified_claims']} "
            f"| {'yes' if r['verdict_unverified'] else 'no'} |"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    sys.exit(main())
