# Eval

The benchmark and the harness for Phase 6.

## Files

- `benchmark.json` — the 34 questions, the expected verdicts, and
  the dev/test assignment. **Draft for review, not frozen.** The
  user reviews the question list, the expected verdicts, and the
  assignment before it is frozen.
- `benchmark/README.md` — the same list rendered for reading
  (generated from the JSON by
  `scripts/render_benchmark_readme.py`, so it cannot drift).
- `adversarial.json` — 33 SQL strings that must all be refused by
  the guardrails (taken from the guardrail test suite).
- `run.py` — the harness.
- `results/` — one JSON + one Markdown report per run, named
  `<date>-<set>.json` / `.md`.

## Reading the numbers

The test set has about five independent patterns (P1, P3, P5, the
negative controls, and the recall lookups), so the overall accuracy
is coarse: the questions inside one pattern test the same underlying
effect, and missing that effect costs several questions at once.
Read the per-pattern table first — it shows which effects the agent
got and which it missed — and treat the overall number as a
summary, not a precise measurement. The recall questions are mostly
lookups (can the agent find a campaign in the recalls table and
report its coverage), not root-cause analysis, so they measure
retrieval more than reasoning.

From the repo root, with the venv Python:

```
backend/.venv/bin/python eval/run.py --set test   # held-out set only
backend/.venv/bin/python eval/run.py --set dev    # dev set only
backend/.venv/bin/python eval/run.py --set all    # both sets
```

The harness builds the graph with the deterministic mock LLM and a
copy of the offline warehouse (the real warehouse is never opened
for writing), runs each question, and grades the structured verdict
exactly — (cause_type, entity) — with no LLM judging. The mock
baseline is the score of a scripted stand-in: its canned answers
are written for the benchmark's patterns, so it is a harness smoke
test and an upper bound on the graph's mechanics, not a prediction
of a real model's score (that is Phase 8).

## Metrics (all on the chosen set)

- **Verdict accuracy** — exact (cause_type, entity) match. Reported
  overall and for non-none verdicts only (the "none" verdict is the
  mock's default, so it inflates the overall number).
- **False-discovery rate (negative controls)** — the fraction of
  negative-control questions (patterns N1–N4) where the verdict is
  not "none" (the agent claimed a cause where there is none).
- **First-pass SQL success** — the fraction of investigations whose
  first SQL passed the guardrail on the first attempt.
- **Self-correction recovery** — of the investigations that had a
  refusal or an execution error, the fraction that still finished
  "done". (Vacuous — reported as 100% — when a set has no refusals.)
- **Unverified-claim rate** — the fraction of investigations with
  at least one claim whose numbers match no evidence row, and the
  total count.
- **Rate-rule warnings** — the number of investigations the
  rate-comparison rule flagged. A warning is a heuristic flag, not
  a failure: T15 (the recall-share question) is a known false
  positive — its counts split covered from uncovered complaints by
  year, which is a coverage split, not a defect comparison.
- **Guardrail block rate (adversarial)** — the fraction of the 33
  adversarial SQL strings refused, and how many fired a different
  rule than the one expected.
- **Cost and latency per investigation** — the mock's cost is $0.00;
  latency is wall-clock per investigation (median and p95).

## Split

Each planted pattern and each recall campaign belongs entirely to
dev or entirely to test (see `benchmark.json`):

- dev: P2 (steering/Lakeside), P4 (volume decoy), N1, N2, and the
  recall campaigns 20V439000, 20V440000, 23V858000.
- test: P1 (brake/Riverside/Acme), P3 (airbag/no-plant), P5 (recall
  explains part), N3, N4, and the recall campaign 26V104000.

The recall questions use only real rows: the recalls table is all
real data, and the complaint counts for the P5 share question filter
`is_synthetic = FALSE`. The Ram (24V653000) and Takata (13E017000)
campaigns from the Phase 1 plan are **not** in the benchmark: they
are real recalls, but they are not in the warehouse (the warehouse
is scoped to FORD F-150 + HONDA PILOT, 2019–2021), so no question
about them is answerable from the agent's data. The dev recall
questions use the Pilot campaigns that are in the warehouse.
