# Schema notes

## Source and license

Real data: NHTSA Office of Defects Investigation (ODI) bulk flat
files — complaints (`FLAT_CMPL.zip`) and recalls
(`FLAT_RCL_POST_2010.zip`). U.S. Government work, public domain under
17 U.S.C. 105. Field layouts:
`https://static.nhtsa.gov/odi/ffdd/cmpl/CMPL.txt` (51 fields) and
`https://static.nhtsa.gov/odi/ffdd/rcl/RCL.txt` (29 fields).
Canonical host `www-odi.nhtsa.dot.gov`; `static.nhtsa.gov` serves
the same files and was the reachable host from this sandbox.

Download: 2026-11-07 (recorded by `scripts/seed_offline.py` in
`data/raw/DOWNLOAD.md` alongside the exact file sizes and row
counts; this file is updated from that record, not from memory).

## Complaints: what is loaded, and what is dropped

The files are tab-delimited with no header row; column order is fixed
by the layout docs above. The loader (`data/loaders/nhtsa.py`) keeps
a curated subset of the 51 complaint columns. Dropped:

- **Identifies a person:** `VIN` (partial VIN), `CITY`,
  `VEHICLE_OPERATOR` (name), `DEALER_NAME`, `DEALER_TEL`,
  `DEALER_CITY`, `DEALER_STATE`, `DEALER_ZIP`. The agent never needs
  them, so they never enter the warehouse.
- **Not needed for analysis:** tire detail (`DOT`, `TIRE_SIZE`,
  `LOC_OF_TIRE`, `TIRE_FAIL_TYPE`), child-seat detail (`SEAT_TYPE`,
  `RESTRAINT_TYPE`), vehicle detail (`NUM_CYLS`, `DRIVE_TRAIN`,
  `FUEL_SYS`, `FUEL_TYPE`, `TRANS_TYPE`, `VEH_SPEED`,
  `PURCH_DT`, `MANUF_DT`, `ORIG_OWNER_YN`, `ANTI_BRAKES_YN`,
  `CRUISE_CONT_YN`, `ORIG_EQUIP_YN`, `POLICE_RPT_YN`,
  `REPAIRED_YN`). If a later investigation needs one, the loader
  gains a column; nothing is silently added.

Kept: the identifiers (`CMPLID`, `ODINO`), make/model/year,
component (`COMPDESC`), complaint text (`CDESCR`), dates
(`FAILDATE`, `DATEA`, `LDATE`), severity flags (`CRASH`, `FIRE`,
`INJURED`, `DEATHS`, `MEDICAL_ATTN`, `VEHICLES_TOWED_YN`), and the
two state codes (`STATE`, `STATE_OF_INCIDENT`). Complaint text is
user-written and untrusted; it is stored as data and never executed
or followed as instruction.

Date handling: all dates are `YYYYMMDD`. Blank, malformed, or
implausible values (before 1995-01-01, the file's coverage start —
the known placeholder dates such as 1901 and 1969-12-31) load as
NULL. The loader counts them per field instead of failing the load.
Model year `9999` (unknown) loads as NULL.

Recalls: all 29 columns are loaded; the file contains no personal
data.

## Synthetic data

`plants`, `lines`, `suppliers`, and `builds` are entirely synthetic
(fictional plant and supplier names, fixed seed 42). The
`complaints` table also holds synthetic rows, marked
`is_synthetic = TRUE` with `plant_id`, `line_id`, and `supplier_id`
populated; real rows have `is_synthetic = FALSE` and those columns
NULL. Plant-level findings are synthetic by design: real complaint
records carry no plant or supplier information. Full generation and
join rules: `docs/data-plan.md`.

Counts in the offline warehouse (from the seed run): 7,108 real
complaints, 8,706 synthetic complaints, 841 recalls. The synthetic
baseline rates sum to 6.4 per 1,000 vehicles built, calibrated to
the observed real density (~6.2 per 1,000 for the scope vehicles),
so neither side dominates the warehouse.

## PII handling in the committed sample

The warehouse drops identifying columns at load time (above), but
an owner can still type personal data into the free-text complaint
field. Before the sample CSVs are committed, the seed script
(`scripts/seed_offline.py`, patterns in `data/loaders/pii.py`)
applies two gates to that text:

- **Drop the row** when the text holds a phone number (including
  a parenthesized area code, `(555) 123-4567`) or an email
  address. The text is usually a signature block, not the defect
  description.
- **Redact in place, keep the row** for a 17-character VIN, a
  partial VIN named in the text ("VIN # NH106286", "last 8 of VIN
  XB527782", "ending in ZF49715"), and a full-form street address
  (house number + street + city/state/ZIP). Redactions are marked
  `[VIN]` and `[ADDRESS]` so a reader can see something was
  removed.

Latest seed run (2026-11-07): 45 rows dropped (phone/email),
35 rows scrubbed (10 VIN, 29 address redactions), out of 7,108
real rows sampled. Verified after the run: no phone number, email,
or bare 17-character VIN remains in the committed sample.

Limits, stated plainly: a partial VIN with no digit in it is not
redacted (that is how "VIN NUMBER" is kept); a street address
without a state and ZIP is not caught, because in vehicle text a
bare "123 Main St" is indistinguishable from phrases like "4 wheel
drive" — the loose pattern matched 52,000 lines, nearly all
vehicle phrases. Dealer addresses are redacted the same as home
addresses; the pattern does not distinguish them. This is a
documented, tested reduction, not a guarantee that no personal
data remains in free text.

## The offline sample

`data/samples/complaints_sample.csv` is a bounded real-rows sample
for the offline warehouse and CI (target ≤ 20k rows), plus all
synthetic rows for the sampled vehicles. It is regenerable
(`scripts/seed_offline.py`) and never hand-edited. PII handling
for the sample is described in the section above; the seed script
logs the counts on every run.
