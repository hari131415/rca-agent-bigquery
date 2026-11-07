"""Load the offline warehouse into BigQuery (Phase 8).

Reads every table from the offline DuckDB warehouse (built by
scripts/seed_offline.py --from-sample) and full-refresh loads it
into the BigQuery dataset Terraform created. The tables already
exist (Terraform creates them); this only loads the rows. The
traces table is not loaded — it starts empty in rca_ops and fills
as investigations run.

Usage (from the repo root, in Cloud Shell with ADC):
    backend/.venv/bin/python scripts/load_to_bigquery.py --project <project-id>

Requires the gcp extra: pip install -e "backend[dev,gcp]".
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT))

TABLES = ["complaints", "recalls", "plants", "lines", "suppliers", "builds"]


def read_table(db_path: Path, table: str) -> list[dict[str, object]]:
    """Read every row of a DuckDB table as a list of dicts."""
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        columns = [row[1] for row in con.execute(f"PRAGMA table_info('{table}')").fetchall()]
        rows = con.execute(f"SELECT * FROM {table}").fetchall()
    finally:
        con.close()
    return [dict(zip(columns, row)) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the offline warehouse into BigQuery.")
    parser.add_argument("--project", required=True, help="GCP project id")
    parser.add_argument(
        "--dataset", default="rca_agent", help="BigQuery dataset (default: rca_agent)"
    )
    parser.add_argument(
        "--db", default=str(ROOT / "data" / "offline.duckdb"), help="Offline DuckDB warehouse"
    )
    args = parser.parse_args()

    from data.loaders.load_bigquery import load_jsonl

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"warehouse not found: {db_path} — run scripts/seed_offline.py --from-sample first")
        raise SystemExit(1)

    total = 0
    for table in TABLES:
        rows = read_table(db_path, table)
        loaded = load_jsonl(args.project, args.dataset, table, rows)
        total += loaded
        print(f"{table}: {loaded} rows -> {args.project}.{args.dataset}.{table}")
    print(f"total: {total} rows")


if __name__ == "__main__":
    main()
