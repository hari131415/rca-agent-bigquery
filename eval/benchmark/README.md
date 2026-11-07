# Benchmark (version 1.0, frozen 2026-11-07)

The eval benchmark: 34 investigation questions with known
answers. Frozen 2026-11-07 at version 1.0; changes after this
need a new version and a re-run of the baseline.

## Verdict schema

- cause_type: `plant | supplier | line | volume | recall | fleet_wide | none`
- entity: a plant id (e.g. riverside), a supplier id (e.g. acme-brakes), a line id (e.g. riverside-2), a make/model for volume (e.g. honda-pilot), a recall campaign number (e.g. 26V104000), or null for none
- supporting_queries: indices of the evidence blocks the verdict rests on (0-based)

Grading: exact match on (cause_type, entity). supporting_queries is checked separately: every id must be a real evidence index and at least one must be present. Grading uses no LLM judging.

Cause-type rule: The verdict answers the question as asked. A 'why' question gets the root cause (the supplier behind the spike); a 'which plant/line/supplier' question gets the location the question asks about (the plant, even when the deeper cause is a supplier). fleet_wide is a real rise that is not concentrated in any plant, line, or supplier (P3: the airbag rise is uniform across plants) — it is not 'none'. none means no pattern at all (the negative controls). P3 and the negative controls are therefore graded differently now: P3 expects fleet_wide, a negative control expects none.

## Split

Each planted pattern and each recall campaign belongs entirely to dev or entirely to test. No pattern or campaign appears in both sets.

- dev: P2, P4, N1, N2, 20V439000, 20V440000, 23V858000
- test: P1, P3, P5, N3, N4, 26V104000

## Questions

### T01 (test, P1)

Q: Why did brake complaints spike for the 2021 F-150?

Expected verdict: `supplier` / `acme-brakes`

Headline P1. The plant rate (Riverside 4.14) plus the supplier rate (Acme 7.20 vs Beacon 1.17) both point to Acme.

### T02 (test, P1)

Q: Is the brake spike at Riverside a supplier issue?

Expected verdict: `supplier` / `acme-brakes`

Follow-up form of T01: the supplier split is the evidence.

### T03 (test, P1)

Q: Which line at Riverside has the brake problem for the 2021 F-150?

Expected verdict: `line` / `riverside-2`

Drill-down: the planted effect is at Riverside line 2. The mock does not answer line questions (it returns the supplier verdict), so this is a known mock miss.

### T04 (test, P1)

Q: Did brake complaint rates rise at Riverside for the 2019 F-150?

Expected verdict: `none` / null

Over-generalization test: the planted P1 effect is 2021-only. The Riverside brake rate for 2019 is 1.15 per 1,000 — the lowest of the four plants (1.15-1.21) and flat against 2020 (1.23). Correct answer: no supporting evidence found.

### T05 (test, P1)

Q: Which plant has the highest brake complaint rate for the 2021 F-150?

Expected verdict: `plant` / `riverside`

Asks for the plant, not the root cause — so the verdict is plant/riverside even though the deeper cause is Acme. The mock returns the supplier verdict, so this is a known mock miss.

### T06 (test, P1)

Q: Did brake complaints rise at all plants for the 2021 F-150, or just Riverside?

Expected verdict: `plant` / `riverside`

Location question, like T05: just Riverside, so the verdict is plant/riverside even though the deeper cause is Acme.

### T07 (test, P3)

Q: Why did airbag complaints rise for the 2020 F-150?

Expected verdict: `fleet_wide` / null

P3: the rise is uniform across plants (1.80-1.83 per 1,000). The verdict is fleet_wide — a real rise, uniform across plants, not concentrated in any plant, line, or supplier.

### T08 (test, P3)

Q: Is the airbag rise for 2020 concentrated at one plant?

Expected verdict: `fleet_wide` / null

P3: no, it is uniform — fleet_wide.

### T09 (test, P3)

Q: Which plant has the highest airbag complaint rate for 2020?

Expected verdict: `fleet_wide` / null

P3: the highest (Riverside 1.83) is within 2% of the lowest (Harborview 1.80) — no plant stands out, so fleet_wide.

### T10 (test, P3)

Q: Is the airbag problem for 2020 a supplier issue?

Expected verdict: `fleet_wide` / null

P3: no supplier link; the airbag rise is plant-independent — fleet_wide.

### T11 (test, P3)

Q: Did airbag complaints rise for the Honda Pilot too in 2020?

Expected verdict: `fleet_wide` / null

P3: the airbag rise is an F-150 2020 effect; the Pilot's airbag rate is flat. Verdict fleet_wide (a real rise, uniform across plants).

### T12 (test, N3)

Q: Why did brake complaints spike for the 2019 Honda Pilot?

Expected verdict: `none` / null

Negative control: the 2019 Pilot brake rate is 1.19 per 1,000 (baseline, vs 4.14 for the planted P1 spike). Correct answer: no supporting evidence found.

### T13 (test, N4)

Q: Is there an airbag problem at Harborview for 2021?

Expected verdict: `none` / null

Negative control: Harborview 2021 airbag rate is 0.56 per 1,000 (below the 2020 baseline of 1.80). Correct answer: no supporting evidence found.

### T14 (test, P5)

Q: Is there a recall covering the 2021 F-150 brake complaints?

Expected verdict: `recall` / `26V104000`

P5: recall 26V104000 (trailer brake control module, 2021-2026 F-150, 4,381,878 units) covers the 2021 complaints. Real rows only.

### T15 (test, P5)

Q: What share of the 2021 F-150 brake complaints is covered by the trailer brake recall?

Expected verdict: `recall` / `26V104000`

P5, covered-vs-uncovered: of 89 real F-150 brake complaints, 53 are for model year 2021 and covered; the rest are for 2019-2020 and are not covered. The answer must cite the counts, not just the campaign. Known rate-rule false positive: the counts split covered from uncovered complaints by year (a coverage split, not a defect comparison), so the rule flags it even though the counts are the right evidence.

### T16 (test, 26V104000)

Q: Which vehicles are covered by recall 26V104000?

Expected verdict: `recall` / `26V104000`

Real-recall lookup: the 2021-2026 F-150 (plus F-250/F-350/F-450/F-550 SD, Maverick, Ranger, F-600 SD). Answered from the recalls table only.

### T17 (test, 26V104000)

Q: Is the 2021 F-150 covered by a trailer brake recall?

Expected verdict: `recall` / `26V104000`

Real-recall lookup: yes, 26V104000.

### D01 (dev, P2)

Q: Why are steering complaints elevated for the 2019 F-150?

Expected verdict: `plant` / `lakeside`

P2: Lakeside steering rate 3.21 per 1,000 vs 0.81 next, across all suppliers.

### D02 (dev, P2)

Q: Which plant has the highest steering complaint rate for 2019?

Expected verdict: `plant` / `lakeside`

P2: Lakeside.

### D03 (dev, P2)

Q: Is the 2019 steering problem at Lakeside specific to one supplier?

Expected verdict: `plant` / `lakeside`

P2: no — the steering elevation is plant-wide at Lakeside, with no supplier link. The verdict stays plant/lakeside.

### D04 (dev, P2)

Q: Did steering complaints rise for all model years, or just 2019?

Expected verdict: `plant` / `lakeside`

P2: 2019-only at Lakeside.

### D05 (dev, P2)

Q: Is there a steering problem at Riverside for the 2019 F-150 too?

Expected verdict: `plant` / `lakeside`

P2: no — it is Lakeside, not Riverside. The verdict is still plant/lakeside (the cause is at Lakeside).

### D06 (dev, P4)

Q: Why did total complaints rise for the Honda Pilot in 2021?

Expected verdict: `volume` / `honda-pilot`

P4 decoy: production doubled (139,960 to 287,855 vehicles) while the rate stayed flat (7.55 to 7.93 per 1,000). The rise is volume, not a defect.

### D07 (dev, P4)

Q: Is the Honda Pilot's 2021 complaint rise a real defect spike?

Expected verdict: `volume` / `honda-pilot`

P4: no — the rate is flat, so it is volume.

### D08 (dev, P4)

Q: How many more vehicles were built for the Honda Pilot in 2021 than in 2020?

Expected verdict: `volume` / `honda-pilot`

P4: 287,855 vs 139,960 (about double). A volume question, so the verdict is volume/honda-pilot.

### D09 (dev, P4)

Q: Did the Honda Pilot's complaint rate per 1,000 rise in 2021?

Expected verdict: `volume` / `honda-pilot`

P4: no — 7.55 in 2020, 7.93 in 2021. The verdict is volume/honda-pilot (the rate check is what settles it).

### D10 (dev, P4)

Q: Is the Honda Pilot's complaint rise in 2021 explained by production volume?

Expected verdict: `volume` / `honda-pilot`

P4: yes — the count doubled because production doubled.

### D11 (dev, N1)

Q: Why did engine complaints spike for the 2019 F-150?

Expected verdict: `none` / null

Negative control: no engine pattern exists. Correct answer: no supporting evidence found.

### D12 (dev, N2)

Q: Is there a steering problem for the 2021 Honda Pilot?

Expected verdict: `none` / null

Negative control: the 2021 Pilot steering rate is 0.79 per 1,000 (baseline, vs 3.21 for the planted P2 spike). Correct answer: no supporting evidence found.

### D13 (dev, 23V858000)

Q: Which recall covers the Honda Pilot's fuel pump?

Expected verdict: `recall` / `23V858000`

Real-recall lookup: 23V858000, the Pilot fuel pump recall, 2,490,460 units — the largest Pilot campaign in the warehouse.

### D14 (dev, 20V439000)

Q: What does recall 20V439000 cover?

Expected verdict: `recall` / `20V439000`

Real-recall lookup: the 2019-2021 Pilot rearview camera (incorrect instrument panel control module software), 607,786 units.

### D15 (dev, 20V440000)

Q: How many units are affected by recall 20V440000?

Expected verdict: `recall` / `20V440000`

Real-recall lookup: 499,344 units (2019-2021 Pilot rearview camera, incorrect central network software).

### D16 (dev, 20V440000)

Q: What defect does recall 20V440000 describe?

Expected verdict: `recall` / `20V440000`

Real-recall lookup: the 2019-2021 Pilot rearview camera (incorrect central network software).

### D17 (dev, 23V858000)

Q: Which recall covers the most Honda Pilot vehicles?

Expected verdict: `recall` / `23V858000`

Real-recall lookup: 23V858000 (2,490,460 units) is the largest Pilot campaign in the warehouse.
