"""Load the offline DuckDB warehouse and verify the planted patterns.

The warehouse is built from the parsed real rows (bounded sample),
the real recalls for the sampled vehicles, and the full synthetic
data. `verify_patterns` runs the rate queries the eval depends on and
fails loudly if a planted correlation is not detectable in the loaded
data — a silent loss of signal here would invalidate the benchmark.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import duckdb

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "duckdb.sql"

COMPLAINT_COLUMNS = [
    "cmplid",
    "odino",
    "mfr_name",
    "make",
    "model",
    "model_year",
    "crash",
    "faildate",
    "fire",
    "injured",
    "deaths",
    "compdesc",
    "state",
    "datea",
    "ldate",
    "miles",
    "occurrences",
    "cdescr",
    "cmpl_type",
    "prod_type",
    "medical_attn",
    "vehicles_towed_yn",
    "state_of_incident",
    "is_synthetic",
    "plant_id",
    "line_id",
    "supplier_id",
]

RECALL_COLUMNS = [
    "record_id",
    "campno",
    "make",
    "model",
    "model_year",
    "mfgcampno",
    "compname",
    "mfgname",
    "bgman",
    "endman",
    "rcltypecd",
    "potaff",
    "odate",
    "influenced_by",
    "mfgtxt",
    "rcdate",
    "datea",
    "rpno",
    "fmvss",
    "desc_defect",
    "conequence_defect",
    "corrective_action",
    "notes",
    "rcl_cmpt_id",
    "mfr_comp_name",
    "mfr_comp_desc",
    "mfr_comp_ptno",
    "do_not_drive",
    "park_outside",
]

BUILD_COLUMNS = [
    "build_id",
    "make",
    "model",
    "model_year",
    "plant_id",
    "line_id",
    "build_date",
    "supplier_brake",
    "supplier_steering",
    "supplier_airbag",
    "vehicles_built",
]


def _to_db_value(value: object) -> object:
    if isinstance(value, _dt.date):
        return value
    return value


def create_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_PATH.read_text())


def load_rows(
    con: duckdb.DuckDBPyConnection,
    table: str,
    columns: list[str],
    rows: list[dict[str, object]],
) -> int:
    """Bulk-insert rows via DuckDB's appender; returns the row count.

    The appender is the bulk path for in-memory rows: one batched
    write instead of one INSERT per row. The previous executemany
    version wrote ~2 GB of WAL for a ~6 MB warehouse and took most
    of a 27-minute seed run; this replaces it.
    """
    if not rows:
        return 0
    with con.appender(table) as appender:
        for row in rows:
            appender.append_row(*[_to_db_value(row.get(col)) for col in columns])
        appender.flush()
    return len(rows)


def load_csv(con: duckdb.DuckDBPyConnection, table: str, columns: list[str], path: Path) -> int:
    """Bulk-load a CSV file with COPY; returns the row count.

    The CSV must have a header row with exactly these columns, in
    this order. Used for the committed real-rows sample, so the
    offline warehouse can be rebuilt from the sample without the
    376 MB raw download.
    """
    quoted = ", ".join(f'"{col}"' for col in columns)
    # COPY returns an empty result; fetch it to clear the connection
    # before the appender runs, or DuckDB refuses further work. The
    # COUNT fetch must also run to exhaustion (fetchall, not
    # fetchone) for the same reason.
    con.execute(
        f"COPY {table} ({quoted}) FROM '{path}' (FORMAT CSV, HEADER true)"  # noqa: S608
    ).fetchall()
    result = con.execute(f"SELECT COUNT(*) FROM {table}").fetchall()
    return int(result[0][0])


def rate_by_plant(
    con: duckdb.DuckDBPyConnection, component: str, year: int
) -> list[tuple[str, float, int]]:
    """Synthetic complaint rate per 1,000 vehicles built, by plant.

    Numerator: synthetic complaint rows for the component/year/plant.
    Denominator: vehicles built by that plant that year (all vehicles
    in scope). MAX, not SUM: the join repeats the denominator once per
    complaint row, so summing it would divide by the count itself.
    Returns (plant_id, rate, complaint_count), highest first.
    """
    rows = con.execute(
        """
        SELECT c.plant_id,
               COUNT(*) * 1000.0 / MAX(b.vehicles_built) AS rate,
               COUNT(*) AS n
        FROM complaints c
        JOIN (
            SELECT plant_id, model_year, SUM(vehicles_built) AS vehicles_built
            FROM builds GROUP BY plant_id, model_year
        ) b ON b.plant_id = c.plant_id AND b.model_year = c.model_year
        WHERE c.is_synthetic AND c.compdesc = ? AND c.model_year = ?
        GROUP BY c.plant_id
        ORDER BY rate DESC
        """,
        [component, year],
    ).fetchall()
    return [(str(plant), float(rate), int(n)) for plant, rate, n in rows]


def verify_patterns(con: duckdb.DuckDBPyConnection) -> dict[str, dict[str, object]]:
    """Check P1-P4 are detectable as rate differences in the warehouse.

    P5 is not a data pattern (it pairs a real recall with a spike at
    eval-construction time), so it is checked by the eval, not here.
    Thresholds are deliberately loose (2x, not the planted 3-6x) so
    the check catches a lost signal without being brittle to sampling.
    """
    results: dict[str, dict[str, object]] = {}

    p1 = rate_by_plant(con, "SERVICE BRAKES", 2021)
    top, second = p1[0], p1[1] if len(p1) > 1 else ("", 0.0, 0)
    results["P1"] = {
        "passed": top[0] == "riverside" and top[1] > 2 * second[1],
        "detail": f"top plant {top[0]} rate {top[1]:.2f} (n={top[2]}), next {second[1]:.2f}",
    }

    p2 = rate_by_plant(con, "STEERING", 2019)
    top, second = p2[0], p2[1] if len(p2) > 1 else ("", 0.0, 0)
    results["P2"] = {
        "passed": top[0] == "lakeside" and top[1] > 2 * second[1],
        "detail": f"top plant {top[0]} rate {top[1]:.2f} (n={top[2]}), next {second[1]:.2f}",
    }

    p3 = rate_by_plant(con, "AIR BAGS", 2020)
    rates = [rate for _, rate, _ in p3]
    spread = (max(rates) / min(rates)) if rates and min(rates) > 0 else float("inf")
    results["P3"] = {
        "passed": spread < 1.5,
        "detail": f"plant rates {[f'{r:.2f}' for r in rates]}, max/min {spread:.2f}",
    }

    rows = con.execute(
        """
        SELECT c.make, c.model_year,
               COUNT(*) * 1000.0 / MAX(b.vehicles_built) AS rate,
               COUNT(*) AS n
        FROM complaints c
        JOIN (
            SELECT make, model_year, SUM(vehicles_built) AS vehicles_built
            FROM builds GROUP BY make, model_year
        ) b ON b.make = c.make AND b.model_year = c.model_year
        WHERE c.is_synthetic AND c.model_year IN (2020, 2021)
        GROUP BY c.make, c.model_year
        """
    ).fetchall()
    rates_by_make_year = {(str(make), int(year)): float(rate) for make, year, rate, _ in rows}
    # The decoy make is the one whose 2021 production roughly doubled.
    decoy_row = con.execute(
        """
        SELECT make FROM (
            SELECT make,
                   SUM(CASE WHEN model_year = 2021 THEN vehicles_built ELSE 0 END) * 1.0
                   / NULLIF(SUM(CASE WHEN model_year = 2020 THEN vehicles_built ELSE 0 END), 0)
                   AS growth
            FROM builds GROUP BY make
        ) ORDER BY growth DESC LIMIT 1
        """
    ).fetchone()
    decoy_make = str(decoy_row[0]) if decoy_row else ""
    rate_2020 = rates_by_make_year.get((decoy_make, 2020), 0.0)
    rate_2021 = rates_by_make_year.get((decoy_make, 2021), 0.0)
    flat = rate_2020 > 0 and abs(rate_2021 - rate_2020) / rate_2020 < 0.25
    results["P4"] = {
        "passed": bool(flat),
        "detail": f"decoy make {decoy_make}: rate 2020 {rate_2020:.2f}, 2021 {rate_2021:.2f}",
    }
    return results
