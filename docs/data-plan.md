# Data plan

## Real data: NHTSA complaints and recalls

Source: National Highway Traffic Safety Administration (NHTSA),
Office of Defects Investigation (ODI), bulk flat files. No API key,
no account, public downloads.

| File | Download URL | Catalog entry |
| --- | --- | --- |
| Complaints | `http://www-odi.nhtsa.dot.gov/downloads/folders/Complaints/FLAT_CMPL.zip` | data.transportation.gov, "NHTSA's Office of Defects Investigation (ODI) - Complaints" (DOT-81) |
| Recalls | `http://www-odi.nhtsa.dot.gov/downloads/folders/Recalls/FLAT_RCL_POST_2010.zip` | data.transportation.gov, "NHTSA's Office of Defects Investigation (ODI) - Recalls" (DOT-83) |

License/terms: U.S. Government work, public domain under 17 U.S.C.
105. The catalog lists Rights: public. No attribution is legally
required; the README and schema docs will cite NHTSA ODI as the
source anyway, with the download date recorded in Phase 2.

What the files are, as documented by the catalog and by third-party
measurements of the same files:

- **Complaints (`FLAT_CMPL.zip`)**: one row per component a complaint
  names, so one complaint can appear on several rows. Fields include
  ODI number, make, model, model year, component, complaint text,
  incident date, received date, and crash/fire/injury/death flags.
  A third-party measurement of the July 2026 file counted ~2.23M
  rows; the Phase 2 loader re-measures the actual download and
  records the count.
- **Recalls (`FLAT_RCL_POST_2010.zip`)**: recall campaigns from 2010
  onward, with campaign number, make, model, model year, component,
  and recall dates.

Known data-quality issues to handle in the loader (Phase 2), not to
discover again later:

- Complaints are unverified owner reports. They are a signal, not
  proof of a defect. The agent's answers must say that where it
  matters, and the eval's ground truth never treats a complaint
  count as proof of a defect on its own.
- Dates carry blanks and placeholder values (e.g., 1901, 1969-12-31)
  in the raw file. The loader parses defensively and records how many
  rows were dropped or repaired, per field.
- Complaint text is free-form, user-written, and untrusted. It is
  stored as data, displayed as data, and never executed or followed
  as instruction (see PLAN.md; tested in Phase 4).
- The file is large (millions of rows). The loader streams it and
  the BigQuery load is a bulk load job, not row-by-row inserts. The
  offline DuckDB sample is a bounded subset (see below), never the
  full file.

Phase 2 records in `data/schemas/README.md`: the exact download
date, the file sizes and row counts, the exact column list from the
real headers, and the load job IDs.

## Synthetic plant/production data

Purpose: vehicle complaints alone can show *what* spiked, not *why*.
Plant and production data lets the agent test hypotheses like "the
spike follows one plant, one line, one supplier" — and the eval needs
questions whose answers are certain, which only planted correlations
can give.

### The join problem, stated plainly

Real NHTSA complaint rows carry make, model, model year, and
component — no VIN, no plant, no supplier. So no plant-level finding
can ever come from real rows alone. The design below is the honest
consequence of that fact, and the README must say so (see "README
requirement" at the end of this file): **plant-level findings are
synthetic by design.**

### Tables

All synthetic, labeled synthetic in the schema docs and README, and
generated with a fixed seed so anyone can reproduce them exactly.
Every complaint row carries `is_synthetic`: `FALSE` for real NHTSA
rows, `TRUE` for synthetic rows (which also have `plant_id`,
`line_id`, and `supplier_id` populated; real rows have those columns
NULL).

- `plants`: plant_id, plant name, state, lines operated.
- `lines`: line_id, plant_id, product (e.g., brake assembly, steering
  rack).
- `suppliers`: supplier_id, supplier name, part (e.g., brake pads,
  tie rods, airbag inflators).
- `builds`: build_id, make, model, model_year, plant_id, line_id,
  build_date, supplier per part. This is the denominator table: it
  says how many vehicles of each make/model/year each plant, line,
  and supplier produced, so complaint *rates* (per 1,000 vehicles
  built) can be computed instead of raw counts. All synthetic.
- `complaints` (synthetic rows only): additional rows in the same
  `complaints` table as the real NHTSA rows, with the same columns
  plus `plant_id`, `line_id`, `supplier_id` populated and
  `is_synthetic = TRUE`. Real rows have those columns NULL and
  `is_synthetic = FALSE`.

### How many synthetic rows, and why

The synthetic volume is set by the rates, not by a target count.
Baseline rates sum to 6.4 complaints per 1,000 vehicles built,
calibrated to the real data: the scope vehicles have 7,117 real
complaint rows for ~1.15M fictional vehicles built, about 6.2 per
1,000 across all components. An earlier draft summed to 32 per 1,000
and produced 43,693 synthetic rows against 7,117 real ones — the
synthetic data drowned the real data, so the rates were cut 5x. The
planted multipliers (x6/x4/x3) still leave large, detectable rate
gaps at the lower volume. If the scope or the real density changes,
the rates are re-calibrated, not the row count patched.

### How synthetic rows are generated and joined

Generator: `data/loaders/synthetic.py`, fixed seed 42, deterministic
(no wall-clock or random-device input). It runs in two passes:

1. **Builds pass.** For each make/model/model_year in scope, the
   generator emits a production mix: how many vehicles each plant
   and line built, and which supplier's part each batch carries. The
   mix is mostly stable across years, with one deliberate skew per
   planted pattern (e.g., for P1, Riverside line 2 builds a large
   share of model-year 2021 vehicles and fits Acme brake pads).
   Every build row records its plant, line, and supplier, so the
   mix is auditable from the table itself.
2. **Complaints pass.** For each (make, model, model_year,
   component) the generator emits synthetic complaint rows at a
   baseline rate per 1,000 vehicles built, then applies the planted
   multipliers (see P1–P5). Each synthetic complaint row draws its
   plant/line/supplier from that make/model/year's builds
   distribution — weighted by vehicles built — so the plant
   assignment of a complaint is a draw from the production mix, not
   an arbitrary label. Where a pattern is planted, the multiplier
   applies only to the rows drawn from the implicated plant/line/
   supplier, which is what makes the pattern detectable as a rate
   difference in the data rather than a label in the generator.

Joins, and what each one can and cannot prove:

- Synthetic complaint row → `builds` on (make, model, model_year,
  plant_id, line_id): exact, per-row. This is the only join that can
  support a plant-level claim, and it only ever involves synthetic
  rows.
- Real complaint row → `builds` on (make, model, model_year) only:
  production mix for that vehicle, never a specific plant. A real
  row can support "brake complaints rose for model year 2021" and
  "Riverside built a large share of 2021 vehicles", but not "the
  2021 brake complaints came from Riverside".
- The agent's schema docs state both joins. Any plant-level claim
  in an answer must trace to synthetic rows; the eval checks this.

### Answer labeling rule (implemented in Phase 4)

The agent's answer builder labels evidence by origin. Any claim
whose supporting rows are synthetic is marked "(synthetic)" in the
answer, and every plant-level claim carries that label, because
plant-level findings are synthetic by design (see below). A claim
supported only by real rows carries no label. The eval checks the
label is present where it belongs and absent where it does not.

### What "synthetic" covers, and what it does not

Synthetic: plants, lines, suppliers, builds, and the synthetic
complaint rows. Real: every NHTSA complaint and recall row, which
are loaded verbatim apart from defensive date parsing (recorded per
field in Phase 2). The generator never modifies a real row, and no
real row is ever relabeled synthetic.

## Planted correlations (ground truth for the eval)

These are deliberately planted and documented here so the eval knows
the true answers. The agent never reads this section at runtime; it
must discover each correlation from the data. The eval's expected
answers cite these by ID. The signal for each pattern lives in the
synthetic complaint rows (see above); the real rows are untouched.

- **P1 — Brake spike, one plant, one supplier (headline case).**
  Brake complaints spike for model year 2021, concentrated at plant
  "Riverside" line 2, which fitted brake pads from supplier "Acme
  Brakes" in that period. Other plants and other model years do not
  show the spike. Expected answer: the spike follows Riverside line 2
  + Acme pads, not the model year as a whole.
- **P2 — Steering, one plant, no supplier link.** Steering complaints
  are elevated for model year 2019 at plant "Lakeside" across all
  suppliers on that line. Expected answer: plant/line effect, no
  supplier to blame.
- **P3 — Airbag, no plant correlation.** Airbag complaints rise for
  model year 2020 at every plant equally. Expected answer: no plant
  or line explains it; the agent must say so rather than force a
  plant story.
- **P4 — Volume confounder (decoy).** Total complaints rise for one
  make because more vehicles were built, not because the defect rate
  rose. Expected answer: the rate per vehicle built is flat; raw
  counts alone would mislead.
- **P5 — Recall explains part of a spike.** A recall campaign covers
  the same make/model/year/component as part of a complaint spike.
  Expected answer: the recall is found and cited, with the share of
  complaints it covers and the share it does not.

## Eval split: by planted pattern, not by question

The benchmark is split so that tuning on dev cannot leak into the
held-out set. Each planted pattern belongs entirely to dev or
entirely to test — never both. A question about P1 never appears in
dev if P1 is assigned to test, and no dev question shares a
pattern's plant, supplier, or model year with a test question about
a different pattern in a way that would teach the answer. (The
generator's parameters are the same code for both sets; the split is
enforced at the question level in `eval/benchmark/`, and the
assignment below is recorded there per question in Phase 6.)

| Pattern | Set | Why here |
| --- | --- | --- |
| P1 brake / Riverside / Acme | **test** | Headline case in the held-out set, so the final report demonstrates the flagship finding on unseen data |
| P2 steering / Lakeside | **dev** | A plant effect to tune against, with no supplier to blame |
| P3 airbag / no plant | **test** | Tests restraint: the correct answer is "no plant explains it" |
| P4 volume decoy | **dev** | The rate-vs-count trap is available while tuning |
| P5 recall explains part | **test** | Tests recall lookup plus honest partial attribution |

Negative controls (below) are split 2 dev / 2 test at minimum, more
if Phase 6 adds them. Real-recall questions (below) are split by
campaign: no recall campaign appears in both sets; the per-question
assignment is recorded in `eval/benchmark/README.md` in Phase 6.

## Negative controls

Questions where no real pattern exists in the data. The correct
answer is "no supporting evidence found" — not a hedged guess, not
the nearest plausible cause. Examples (final wording in Phase 6,
reviewed by the user):

- N1 (dev): a component/model-year pair with no elevation anywhere.
- N2 (dev): a plant accused of a spike that the rates do not support.
- N3 (test): a supplier accused of a defect with no rate difference
  across suppliers.
- N4 (test): a model year whose raw complaint count rose only
  because production rose (distinct from P4's make-level version).

Metric: **false-discovery rate** — on negative-control questions,
the share where the agent claims a cause instead of reporting no
supporting evidence. Reported separately from the
hallucinated-evidence rate (claims not supported by returned rows),
which applies across all questions. A good agent scores low on both;
they measure different failures (inventing a cause vs. citing rows
that do not support the claim).

## Real ground truth from recalls

Part of the benchmark tests the agent on real-world causes, not only
planted ones. These questions are answerable from the real NHTSA
recalls file and verified against the recall records themselves —
not against any model's opinion and not against the planted data.

How they are built (Phase 6, from the downloaded file): pick
campaigns with a clear make/model/model-year/component and a
documented defect (e.g., a campaign whose recall notice states the
defect plainly), then write questions whose expected answers quote
the campaign number and the defect as recorded. Each question's
expected answer cites the exact recall rows (campaign number, make,
model, model year, component). Questions are split by campaign
across dev and test as above. Real-recall questions use only real
rows: the recall side is the recalls table (all real), and any
complaint-side corroboration must filter `is_synthetic = FALSE`.
A recall question whose expected answer depends on a synthetic row
is a benchmark bug, caught by the Phase 6 review.

Verified campaigns from the downloaded file (2026-11-07, read
directly from `FLAT_RCL_POST_2010.txt`; the defect quotes below are
the records' own words, truncated):

- **26V104000** (Ford, 2021–2026 F-150 and others): integrated
  trailer brake control module may lose communication when towing,
  "possibly causing a loss of brake and turn signal lights".
  4,381,878 units. A brake-system recall on a high-volume truck —
  pairs with brake-complaint questions.
- **24V653000** (Ram 1500, 2019/2021–2024): "Due to a software
  malfunction, the anti-lock brake system (ABS) control module may
  disable the electronic stability control system", failing FMVSS
  126. 1,025,432 units. A software-cause recall, useful where the
  correct answer is not a part failure.
- **13E017000** (Takata, equipment recall): passenger-side air bag
  inflators "may produce excessive internal pressure causing the
  inflators to rupture upon deployment". The canonical airbag
  defect, usable for questions about a defect that spans many makes.

What this does not claim: a recall existing does not prove it caused
a complaint spike. Questions are phrased so the correct answer is
what the records support ("campaign X covers this make/model/year/
component and states defect Y"), and the agent is marked wrong if it
over-claims causation the records do not support.

## Offline warehouse (DuckDB)

Same table names as BigQuery, same columns where the dialect allows.
Loaded by `data/loaders/load_duckdb.py` from a bounded sample:

- Complaints: real rows filtered to the makes/model years the
  planted patterns use, plus all synthetic rows for those vehicles,
  capped (target ≤ 20k rows). The sample is chosen so P1–P5 are all
  still detectable in it — the loader verifies that after sampling
  and fails loudly if a planted correlation drops out.
- Recalls: all campaigns for the sampled makes/model years.
- Synthetic plant data: complete, not sampled.

The sample is regenerable (`scripts/seed_offline.py`); it is never
hand-edited. If the sample changes, the eval's expected answers are
re-checked, not silently kept.

## SQL dialect differences (BigQuery vs DuckDB)

Kept out of the agent's core logic; handled in `sql/dialect.py`
(Phase 3) and documented here as the list grows. Known differences
to handle from the start:

- Quoting: BigQuery uses backticks for identifiers; DuckDB accepts
  backticks too, so generated SQL uses backticks throughout.
- `SAFE_DIVIDE(x, y)`: BigQuery-only; transpile to
  `CASE WHEN y = 0 THEN NULL ELSE x / y END` (or DuckDB's
  `x / NULLIF(y, 0)`).
- `DATE_TRUNC(date, MONTH)`: same name, different argument order in
  DuckDB (`DATE_TRUNC('month', date)`). Transpile.
- `UNNEST` / arrays: BigQuery arrays have no direct DuckDB
  equivalent; the agent's SQL avoids arrays (schema is flat), so this
  is a documented non-goal, not a transpiler case.
- `QUALIFY`: BigQuery-only; the agent does not generate it (window
  functions go in a subquery instead).
- String/date functions: `EXTRACT`, `FORMAT_DATE`, `PARSE_DATE`
  differ; the transpiler covers the small set the agent uses, and the
  dialect test pins each one.
- Dry run: BigQuery dry run returns bytes processed; DuckDB has no
  byte billing, so its dry run returns estimated rows via `EXPLAIN`
  and the caps are row-based offline.

Rule: if a construct cannot be transpiled, the agent must not
generate it. The guardrail rejects SQL the transpiler cannot handle
in offline mode, and the dialect test proves the agent's generated
SQL runs on both warehouses.

## README requirement

The README (Phase 7) must state, in the data section and wherever
plant-level findings are shown, that plant-level findings are
synthetic by design: plants, lines, suppliers, builds, and the
plant-linked complaint rows are generated data; only the NHTSA
complaint and recall rows are real. The statement must name the
reason in one line — real complaint records carry no plant or
supplier information — so a reader never mistakes a plant finding
for a claim about a real factory.

It must also state the real vs synthetic counts plainly, in the same
place (e.g., "7,108 real NHTSA complaints; 8,706 synthetic
complaints; 841 recalls"), updated from the seed output whenever
the sample is regenerated, so the ratio is visible and not buried
in a schema file.
