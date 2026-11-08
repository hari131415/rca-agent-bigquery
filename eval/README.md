# Eval

The benchmark and the harness for Phase 6.

## Files

- `benchmark.json` — the 34 questions, the expected verdicts, and
  the dev/test assignment. **Frozen as v1.0, 2026-11-07.** The mock
  baseline is measured against it, and it does not change.
- `benchmark-v1.1.json` — v1.1. The only change from v1.0 is the
  recall ambiguity below (T14/T15/T17). Questions unchanged.
- `benchmark/README.md` — the same list rendered for reading
  (generated from the JSON by
  `scripts/render_benchmark_readme.py`, so it cannot drift).
- `adversarial.json` — 33 SQL strings that must all be refused by
  the guardrails (taken from the guardrail test suite).
- `run.py` — the harness. `--benchmark` selects the version
  (default v1.0); `--regrade <results.json>` re-grades a saved run
  under the selected version with no model calls.
- `results/` — one JSON + one Markdown report per run, named
  `<date>-<set>.json` / `.md`. `results/live/` holds the live
  runs: the 2026-11-08 test-set run (JSON, Markdown report, and
  the console log) and its v1.1 regrade.

## Benchmark versions

### v1.0 (frozen 2026-11-07)

The benchmark as reviewed and frozen in Phase 6. The mock
baseline (test 82.3%, dev 100%, all 91.2%) is measured against
it, and it stays the CI baseline: nothing in v1.0 changes.

### v1.1 (2026-11-08) — the 22V193000 ambiguity

The second live run answered T15 and T17 with recall **22V193000**
instead of the expected 26V104000. That is not a model error: it
is a second real answer. 22V193000 is a real Ford recall for the
same component — ELECTRICAL SYSTEM: INTEGRATED TRAILER BRAKE
CONTROL — covering the 2021 F-150: recalls sample record 227416,
received 2022-03-28, 391,836 units affected, manufacturer campaign
22S17, "a towed trailer equipped with an electric or electric-over
hydraulic brake system may not brake." It is in the warehouse
(recalls table, 2 rows). So "Is the 2021 F-150 covered by a
trailer brake recall?" (T17) and "What share of the 2021 F-150
brake complaints is covered by the trailer brake recall?" (T15)
each have two valid answers, and "Is there a recall covering the
2021 F-150 brake complaints?" (T14) does too — the same
make/model/year/component match.

v1.1 does not reword the questions and does not change v1.0. It
defines the expected verdict for T14/T15/T17 as an accepted set,
`{26V104000, 22V193000}`: naming any campaign in the set is
correct, and the answer should list all matching recalls (the
analyze prompt now says so). The rule is documented in
`benchmark-v1.1.json` (the `verdict_schema.grading` field and the
question notes) and in the grading code (`_grade` in `run.py`).

The 2026-11-08 live run is reported under v1.0 (official) and
under v1.1 (labeled as a post-hoc regrade) — see "Live results"
below. The live rerun after the fixes runs under v1.1.

## Live results (2026-11-08, test set, gemini-3.8-flash, global)

Run on a GCE VM as rca-agent-runner, $4.00 cap, `--delay-seconds
2`. Full files in `results/live/`.

| Metric | v1.0 (official) | v1.1 (post-hoc regrade) |
|---|---|---|
| Verdict accuracy | 52.9% (9/17) | 64.7% (11/17) |
| Verdict accuracy, strict | 47.1% (8/17) | 58.8% (10/17) |
| Verdict accuracy, non-none | 42.9% (6/14) | 57.1% (8/14) |
| False-discovery rate | 0.0% | 0.0% |
| False-attribution rate | 4/8 (50.0%) | 4/8 (50.0%) |
| First-pass SQL | 100.0% | 100.0% |
| Unverified-claim rate | 35.3% (8 claims) | 35.3% (8 claims) |
| Unverified verdicts | 3 (17.6%) | 3 (17.6%) |
| Abstentions / errors | 0 / 0 | 0 / 0 |
| Cost per investigation | $0.1006 ($1.71 total) | same |
| Latency median / p95 | 163 s / 353 s | same |

Per pattern (v1.0): P1 5/6 (83.3%, beats the mock's 50% — the
line and plant drill-downs are right), P3 1/5 (20.0%), P5 0/2,
recall lookups 1/2, negative controls 2/2. Under v1.1, P5 becomes
1/2 and the recall lookups 2/2.

Failure analysis (what the fixes after this run address):

- **Over-attribution (P3).** T07 supplier/meridian-airbags, T09
  plant/riverside, T10 supplier/meridian-airbags, T11
  volume/HONDA PILOT — four entity verdicts for a rise that is
  flat across plants (1.80–1.83 per 1,000), lines (1.77–1.86),
  and years. The agent now has a deterministic uniformity check
  (max/min rate ratio under 2.0) that overrides such a verdict to
  fleet_wide.
- **Confound (T01).** The model said plant/riverside; the cause
  is supplier/acme-brakes. Riverside is both the plant with the
  spike and the plant where Acme brakes were introduced, and the
  model never stratified one within the other. The agent now
  requests a follow-up query (rate by supplier within the plant,
  or by plant within the supplier) before deciding.
- **Ungrounded verdicts (T02, T10, T11).** Correct (T02) or not,
  the verdict entity appears in no supporting row. Strict
  accuracy counts those as abstentions.
- **Unverified claims (35.3%, 8 claims).** The answers are not in
  the attached files, so the specific numbers could not be
  inspected; the diagnosis is from the validator's rules. The
  validator only accepted row values and column sums, so a ratio
  ("3.4 times higher"), a difference, or a share was flagged even
  when it was computed from the rows. Those derivations now
  verify when they come from one query's rows within one column.
- **Recall ambiguity (T15, T17).** See v1.1 above. T14 (none)
  stays a miss under both versions: the model found no recall at
  all.
- **Latency.** Median 163 s per investigation (p95 353 s) — the
  model makes up to 4 calls plus a follow-up, each with thinking
  time. The question latencies sum to about 51 minutes, which is
  why Cloud Shell kept killing it and the rerun uses a VM (see
  the runbook).

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

- **Verdict accuracy** — exact (cause_type, entity) match, or any
  campaign in the accepted set where the benchmark defines one
  (v1.1). Reported overall, strict (below), and for non-none
  verdicts only (the "none" verdict is the mock's default, so it
  inflates the overall number).
- **Strict verdict accuracy** — correct *and* grounded: a verdict
  whose entity appears in no supporting query's result rows does
  not count as correct, even when it names the expected entity.
  Strict abstentions are insufficient_evidence verdicts plus
  ungrounded verdicts.
- **False-discovery rate (negative controls)** — the fraction of
  negative-control questions (patterns N1–N4) where the verdict is
  not "none" (the agent claimed a cause where there is none).
- **False-attribution rate** — the fraction of no-entity questions
  (expected cause none or fleet_wide: T04, T07–T13 on the test
  set) where the verdict names an entity. FDR hides these: a
  fleet_wide question is not a negative control, so naming a
  supplier for the P3 rise does not move FDR at all. The second
  live run scored 4/8 (50.0%) here while FDR was 0.0%.
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
