"""Render eval/benchmark/README.md from eval/benchmark.json, so the
review document cannot drift from the machine-readable benchmark.

Run from the repo root: `backend/.venv/bin/python scripts/render_benchmark_readme.py`
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK = ROOT / "eval" / "benchmark.json"
OUT = ROOT / "eval" / "benchmark" / "README.md"


def main() -> int:
    data = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    questions = data["questions"]
    lines = [
        "# Benchmark (version 1.0, frozen 2026-11-07)",
        "",
        "The eval benchmark: 34 investigation questions with known",
        "answers. Frozen 2026-11-07 at version 1.0; changes after this",
        "need a new version and a re-run of the baseline.",
        "",
        "## Verdict schema",
        "",
        f"- cause_type: `{data['verdict_schema']['cause_type']}`",
        f"- entity: {data['verdict_schema']['entity']}",
        f"- supporting_queries: {data['verdict_schema']['supporting_queries']}",
        "",
        f"Grading: {data['verdict_schema']['grading']}",
        "",
        f"Cause-type rule: {data['verdict_schema']['cause_type_rule']}",
        "",
        "## Split",
        "",
        f"{data['split']['rule']}",
        "",
        f"- dev: {', '.join(data['split']['dev'])}",
        f"- test: {', '.join(data['split']['test'])}",
        "",
        "## Questions",
        "",
    ]
    for question in questions:
        expected = question["expected"]
        lines.append(f"### {question['id']} ({question['set']}, {question['pattern']})")
        lines.append("")
        lines.append(f"Q: {question['question']}")
        lines.append("")
        lines.append(
            f"Expected verdict: `{expected['cause_type']}`"
            + (f" / `{expected['entity']}`" if expected["entity"] else " / null")
        )
        lines.append("")
        lines.append(question["notes"])
        lines.append("")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({len(questions)} questions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
