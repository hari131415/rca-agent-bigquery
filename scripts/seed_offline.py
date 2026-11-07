"""Build the offline DuckDB warehouse from the NHTSA files.

Run from the repo root: `python scripts/seed_offline.py`

Steps: download the flat files if missing (mirror first, canonical
host as fallback), pick the scope (top 2 makes by complaint volume
for model years 2019-2021, top model per make), generate the
synthetic plant data for that scope, sample the real complaints
(deterministic stride, capped), build data/offline.duckdb, and verify
the planted patterns are detectable. Exits non-zero if any pattern
check fails or a download fails. Writes data/raw/DOWNLOAD.md with
the provenance record.

`--from-sample` rebuilds the warehouse from the committed sample
CSVs alone (no download, no 1.6 GB parse): the real-rows sample and
the recalls sample plus the synthetic generator. That is the path CI
uses. Real rows are loaded with COPY from the sample CSV, not from
in-memory dicts, so the file CI loads is the file this script writes.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import sys
import time
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.loaders import load_duckdb, nhtsa, pii, synthetic  # noqa: E402
from data.loaders.synthetic import VehicleScope  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
DB_PATH = ROOT / "data" / "offline.duckdb"
SAMPLE_PATH = ROOT / "data" / "samples" / "complaints_sample.csv"
RECALLS_SAMPLE_PATH = ROOT / "data" / "samples" / "recalls_sample.csv"
SCOPE_YEARS = (2019, 2020, 2021)
SAMPLE_CAP = 20_000

# Fictional annual production per vehicle. The second vehicle doubles
# in 2021 (P4 volume decoy): raw complaints rise, rate stays flat.
VEHICLES_BY_YEAR = [
    {2019: 180_000, 2020: 190_000, 2021: 200_000},
    {2019: 150_000, 2020: 140_000, 2021: 290_000},
]


@dataclass
class SampleStats:
    """PII handling counts for the real-rows sample."""

    dropped_pii: int = 0
    scrubbed_rows: int = 0
    vin_redactions: int = 0
    address_redactions: int = 0


def download_if_missing(name: str, urls: list[str]) -> Path:
    dest = RAW_DIR / name
    if dest.exists():
        return dest
    last_error: Exception | None = None
    for url in urls:
        try:
            print(f"downloading {url} ...")
            nhtsa.download(url, dest)
            print(f"  -> {dest} ({dest.stat().st_size:,} bytes)")
            return dest
        except Exception as exc:  # noqa: BLE001 - try the next host
            last_error = exc
            print(f"  failed ({exc}); trying next host")
            dest.unlink(missing_ok=True)
    raise RuntimeError(f"could not download {name}: {last_error}")


def extract_txt(zip_path: Path) -> Path:
    with zipfile.ZipFile(zip_path) as archive:
        members = [m for m in archive.namelist() if m.lower().endswith(".txt")]
        if len(members) != 1:
            raise RuntimeError(f"{zip_path}: expected one .txt member, got {members}")
        out = RAW_DIR / Path(members[0]).name
        if not out.exists():
            archive.extract(members[0], RAW_DIR)
            (RAW_DIR / members[0]).rename(out)
        return out


def pick_scope(complaints_path: Path) -> list[VehicleScope]:
    """Top 2 makes by complaint volume in the scope years, top model each."""
    counts: Counter[tuple[str, str, int]] = Counter()
    stats = nhtsa.ParseStats()
    for row in nhtsa.parse_complaints(complaints_path, stats):
        year = row["model_year"]
        if isinstance(year, int) and year in SCOPE_YEARS:
            counts[(str(row["make"]), str(row["model"]), year)] += 1
    by_make: Counter[str] = Counter()
    by_model: Counter[tuple[str, str]] = Counter()
    for (make, model, _), count in counts.items():
        by_make[make] += count
        by_model[(make, model)] += count
    scope: list[VehicleScope] = []
    for index, (make, _) in enumerate(by_make.most_common(2)):
        model = max(
            (m for (mk, m) in by_model if mk == make),
            key=lambda m: by_model[(make, m)],
        )
        scope.append(VehicleScope(make=make, model=model, vehicles_by_year=VEHICLES_BY_YEAR[index]))
    print(f"scope: {[(s.make, s.model) for s in scope]} (from {stats.rows_read:,} rows)")
    return scope


def scope_from_sample(path: Path) -> list[VehicleScope]:
    """Scope from a sample CSV: distinct (make, model), sorted.

    The committed sample holds exactly the two scope vehicles; sorted
    order keeps the P4 decoy (the second vehicle) stable.
    """
    with path.open(newline="", encoding="utf-8") as handle:
        vehicles = sorted({(r["make"], r["model"]) for r in csv.DictReader(handle)})
    return [
        VehicleScope(make=make, model=model, vehicles_by_year=VEHICLES_BY_YEAR[index])
        for index, (make, model) in enumerate(vehicles)
    ]


def sample_real_rows(
    complaints_path: Path, scope: list[VehicleScope]
) -> tuple[list[dict[str, object]], SampleStats]:
    """Deterministic stride sample of real rows for the scope vehicles.

    Rows whose complaint text holds a phone number or email are
    dropped; rows with a VIN or street address in the text are kept
    with the match redacted (see data/loaders/pii.py). Counts for
    both are returned in SampleStats.
    """
    wanted = {(s.make, s.model, year) for s in scope for year in SCOPE_YEARS}
    counts: Counter[tuple[str, str, int]] = Counter()
    stats = nhtsa.ParseStats()
    for row in nhtsa.parse_complaints(complaints_path, stats):
        key = (str(row["make"]), str(row["model"]), row["model_year"])
        if key in wanted:
            counts[key] += 1
    per_group = max(1, SAMPLE_CAP // max(1, len(counts)))
    strides = {key: max(1, count // per_group) for key, count in counts.items()}
    seen: Counter[tuple[str, str, int]] = Counter()
    kept: list[dict[str, object]] = []
    pii_stats = SampleStats()
    stats = nhtsa.ParseStats()
    for row in nhtsa.parse_complaints(complaints_path, stats):
        key = (str(row["make"]), str(row["model"]), row["model_year"])
        if key not in wanted:
            continue
        seen[key] += 1
        if seen[key] % strides[key] != 0:
            continue
        if pii.contains_contact_info(str(row["cdescr"])):
            pii_stats.dropped_pii += 1
            continue
        scrubbed = pii.scrub_text(str(row["cdescr"]))
        if scrubbed.redacted:
            pii_stats.scrubbed_rows += 1
            pii_stats.vin_redactions += scrubbed.vin_redactions
            pii_stats.address_redactions += scrubbed.address_redactions
            row["cdescr"] = scrubbed.text
        row["plant_id"] = None
        row["line_id"] = None
        row["supplier_id"] = None
        kept.append(row)
        if len(kept) >= SAMPLE_CAP:
            break
    print(
        f"sample: {len(kept):,} real rows (dropped {pii_stats.dropped_pii} "
        f"with phone/email in text; scrubbed {pii_stats.scrubbed_rows} rows: "
        f"{pii_stats.vin_redactions} VIN, {pii_stats.address_redactions} address redactions)"
    )
    return kept, pii_stats


def sample_recalls(recalls_path: Path, scope: list[VehicleScope]) -> list[dict[str, object]]:
    wanted = {(s.make, s.model, year) for s in scope for year in SCOPE_YEARS}
    stats = nhtsa.ParseStats()
    kept = [
        row
        for row in nhtsa.parse_recalls(recalls_path, stats)
        if (str(row["make"]), str(row["model"]), row["model_year"]) in wanted
    ]
    print(f"recalls: {len(kept):,} rows for scope vehicles (from {stats.rows_read:,})")
    return kept


def _csv_value(value: object) -> str:
    """Serialize one value for a sample CSV (read back by COPY)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)


def write_csv(rows: list[dict[str, object]], path: Path, columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _csv_value(row.get(col)) for col in columns})


def build_warehouse(
    real_csv: Path,
    recalls_csv: Path,
    generated: synthetic.GeneratedData,
    db_path: Path = DB_PATH,
) -> dict[str, int]:
    """Create the DuckDB file from the sample CSVs and generated data.

    Real rows and recalls load with COPY (bulk); synthetic rows load
    with the appender (bulk). Returns row counts per table.
    """
    started = time.monotonic()
    if db_path.exists():
        db_path.unlink()
    import duckdb

    con = duckdb.connect(str(db_path))
    load_duckdb.create_tables(con)
    n_real = load_duckdb.load_csv(con, "complaints", load_duckdb.COMPLAINT_COLUMNS, real_csv)
    n_syn = load_duckdb.load_rows(
        con, "complaints", load_duckdb.COMPLAINT_COLUMNS, generated.complaints
    )
    n_rcl = load_duckdb.load_csv(con, "recalls", load_duckdb.RECALL_COLUMNS, recalls_csv)
    load_duckdb.load_rows(
        con,
        "plants",
        ["plant_id", "name", "state"],
        [
            {"plant_id": p["plant_id"], "name": p["name"], "state": p["state"]}
            for p in synthetic.PLANTS
        ],
    )
    load_duckdb.load_rows(
        con,
        "lines",
        ["line_id", "plant_id", "product"],
        [
            {"line_id": item["line_id"], "plant_id": item["plant_id"], "product": item["product"]}
            for item in synthetic.LINES
        ],
    )
    load_duckdb.load_rows(
        con,
        "suppliers",
        ["supplier_id", "name", "part"],
        [
            {"supplier_id": s["supplier_id"], "name": s["name"], "part": s["part"]}
            for s in synthetic.SUPPLIERS
        ],
    )
    load_duckdb.load_rows(con, "builds", load_duckdb.BUILD_COLUMNS, generated.builds)
    results = load_duckdb.verify_patterns(con)
    con.close()
    elapsed = time.monotonic() - started
    print(f"loaded: {n_real:,} real + {n_syn:,} synthetic complaints, {n_rcl:,} recalls")
    print(f"load: {elapsed:.1f}s (COPY + appender)")
    failed = [name for name, res in results.items() if not res["passed"]]
    for name, res in results.items():
        print(f"  {name}: {'PASS' if res['passed'] else 'FAIL'} — {res['detail']}")
    if failed:
        raise RuntimeError(f"failed pattern checks: {failed}")
    print("offline warehouse ready:", db_path)
    return {"real": n_real, "synthetic": n_syn, "recalls": n_rcl}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the offline DuckDB warehouse.")
    parser.add_argument(
        "--from-sample",
        action="store_true",
        help="rebuild from the committed sample CSVs; no download or raw parse",
    )
    args = parser.parse_args(argv)
    started = time.monotonic()

    if args.from_sample:
        if not SAMPLE_PATH.exists() or not RECALLS_SAMPLE_PATH.exists():
            print("sample CSVs missing; run without --from-sample first")
            return 1
        scope = scope_from_sample(SAMPLE_PATH)
        print(f"scope from sample: {[(s.make, s.model) for s in scope]}")
        generated = synthetic.generate(scope)
        print(
            f"synthetic: {len(generated.builds):,} build batches, "
            f"{len(generated.complaints):,} complaint rows"
        )
        try:
            build_warehouse(SAMPLE_PATH, RECALLS_SAMPLE_PATH, generated)
        except RuntimeError as exc:
            print(exc)
            return 1
        print(f"total: {time.monotonic() - started:.1f}s")
        return 0

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cmpl_zip = download_if_missing(
        "FLAT_CMPL.zip", [nhtsa.COMPLAINTS_URL_MIRROR, nhtsa.COMPLAINTS_URL]
    )
    rcl_zip = download_if_missing(
        "FLAT_RCL_POST_2010.zip", [nhtsa.RECALLS_URL_MIRROR, nhtsa.RECALLS_URL]
    )
    cmpl_txt = extract_txt(cmpl_zip)
    rcl_txt = extract_txt(rcl_zip)

    scope = pick_scope(cmpl_txt)
    real_rows, pii_stats = sample_real_rows(cmpl_txt, scope)
    recall_rows = sample_recalls(rcl_txt, scope)
    write_csv(real_rows, SAMPLE_PATH, load_duckdb.COMPLAINT_COLUMNS)
    write_csv(recall_rows, RECALLS_SAMPLE_PATH, load_duckdb.RECALL_COLUMNS)

    generated = synthetic.generate(scope)
    print(
        f"synthetic: {len(generated.builds):,} build batches, "
        f"{len(generated.complaints):,} complaint rows"
    )
    try:
        build_warehouse(SAMPLE_PATH, RECALLS_SAMPLE_PATH, generated)
    except RuntimeError as exc:
        print(exc)
        return 1

    cmpl_url = nhtsa.COMPLAINTS_URL_MIRROR
    rcl_url = nhtsa.RECALLS_URL_MIRROR
    download_record = (
        f"# Download record\n\n"
        f"- Date: {_dt.date.today().isoformat()}\n"
        f"- Complaints: {cmpl_zip.name} ({cmpl_zip.stat().st_size:,} bytes) from {cmpl_url}\n"
        f"- Recalls: {rcl_zip.name} ({rcl_zip.stat().st_size:,} bytes) from {rcl_url}\n"
        f"- Scope: {[(s.make, s.model, s.vehicles_by_year) for s in scope]}\n"
        f"- Sample: {len(real_rows):,} real rows, {pii_stats.dropped_pii} dropped "
        f"(phone/email in text), {pii_stats.scrubbed_rows} rows scrubbed "
        f"({pii_stats.vin_redactions} VIN, {pii_stats.address_redactions} address redactions)\n"
        f"- Synthetic: {len(generated.builds):,} builds, {len(generated.complaints):,} complaints\n"
        f"- Pattern checks: all passed\n"
    )
    (RAW_DIR / "DOWNLOAD.md").write_text(download_record)
    print(f"total: {time.monotonic() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
