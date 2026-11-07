"""Parse NHTSA ODI flat files (complaints and recalls).

The files are tab-delimited with no header row; column order is fixed
by the field-layout documents published alongside them:

- complaints: https://static.nhtsa.gov/odi/ffdd/cmpl/CMPL.txt (51 fields)
- recalls:    https://static.nhtsa.gov/odi/ffdd/rcl/RCL.txt (29 fields)

Canonical download host is www-odi.nhtsa.dot.gov; static.nhtsa.gov
serves the same files and is the reachable host from this sandbox
(verified 2026-11-07). Only a curated subset of complaint columns is
loaded into the warehouse: columns that identify a person (VIN,
operator name, dealer phone, city) or that the agent never needs are
dropped here, not downstream. The full positional layout is kept so a
dropped column is a deliberate choice, documented in
data/schemas/README.md.
"""

from __future__ import annotations

import csv
import datetime as _dt
import re
from collections.abc import Iterator
from pathlib import Path
from urllib.request import urlretrieve

COMPLAINTS_URL = "http://www-odi.nhtsa.dot.gov/downloads/folders/Complaints/FLAT_CMPL.zip"
RECALLS_URL = "http://www-odi.nhtsa.dot.gov/downloads/folders/Recalls/FLAT_RCL_POST_2010.zip"
# Reachable mirror of the same files (same host as the field-layout docs).
COMPLAINTS_URL_MIRROR = "https://static.nhtsa.gov/odi/ffdd/cmpl/FLAT_CMPL.zip"
RECALLS_URL_MIRROR = "https://static.nhtsa.gov/odi/ffdd/rcl/FLAT_RCL_POST_2010.zip"

COMPLAINT_COLUMNS = [
    "CMPLID",
    "ODINO",
    "MFR_NAME",
    "MAKETXT",
    "MODELTXT",
    "YEARTXT",
    "CRASH",
    "FAILDATE",
    "FIRE",
    "INJURED",
    "DEATHS",
    "COMPDESC",
    "CITY",
    "STATE",
    "VIN",
    "DATEA",
    "LDATE",
    "MILES",
    "OCCURENCES",
    "CDESCR",
    "CMPL_TYPE",
    "POLICE_RPT_YN",
    "PURCH_DT",
    "ORIG_OWNER_YN",
    "ANTI_BRAKES_YN",
    "CRUISE_CONT_YN",
    "NUM_CYLS",
    "DRIVE_TRAIN",
    "FUEL_SYS",
    "FUEL_TYPE",
    "TRANS_TYPE",
    "VEH_SPEED",
    "DOT",
    "TIRE_SIZE",
    "LOC_OF_TIRE",
    "TIRE_FAIL_TYPE",
    "ORIG_EQUIP_YN",
    "MANUF_DT",
    "SEAT_TYPE",
    "RESTRAINT_TYPE",
    "DEALER_NAME",
    "DEALER_TEL",
    "DEALER_CITY",
    "DEALER_STATE",
    "DEALER_ZIP",
    "PROD_TYPE",
    "REPAIRED_YN",
    "MEDICAL_ATTN",
    "VEHICLES_TOWED_YN",
    "STATE_OF_INCIDENT",
    "VEHICLE_OPERATOR",
]

RECALL_COLUMNS = [
    "RECORD_ID",
    "CAMPNO",
    "MAKETXT",
    "MODELTXT",
    "YEARTXT",
    "MFGCAMPNO",
    "COMPNAME",
    "MFGNAME",
    "BGMAN",
    "ENDMAN",
    "RCLTYPECD",
    "POTAFF",
    "ODATE",
    "INFLUENCED_BY",
    "MFGTXT",
    "RCDATE",
    "DATEA",
    "RPNO",
    "FMVSS",
    "DESC_DEFECT",
    "CONEQUENCE_DEFECT",
    "CORRECTIVE_ACTION",
    "NOTES",
    "RCL_CMPT_ID",
    "MFR_COMP_NAME",
    "MFR_COMP_DESC",
    "MFR_COMP_PTNO",
    "DO_NOT_DRIVE",
    "PARK_OUTSIDE",
]

# Complaint columns loaded into the warehouse, in warehouse order.
# Dropped: VIN, CITY, VEHICLE_OPERATOR, DEALER_* (identify people) and
# the vehicle/tire/seat detail columns the agent never queries.
COMPLAINT_WAREHOUSE_COLUMNS = [
    "CMPLID",
    "ODINO",
    "MFR_NAME",
    "MAKETXT",
    "MODELTXT",
    "YEARTXT",
    "CRASH",
    "FAILDATE",
    "FIRE",
    "INJURED",
    "DEATHS",
    "COMPDESC",
    "STATE",
    "DATEA",
    "LDATE",
    "MILES",
    "OCCURENCES",
    "CDESCR",
    "CMPL_TYPE",
    "PROD_TYPE",
    "MEDICAL_ATTN",
    "VEHICLES_TOWED_YN",
    "STATE_OF_INCIDENT",
]

FILE_START = _dt.date(1995, 1, 1)  # complaints file covers 1995 onward
UNKNOWN_YEAR = "9999"

_DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def parse_yyyymmdd(value: str) -> _dt.date | None:
    """Parse a YYYYMMDD field; blank, malformed, or implausible -> None.

    The file is documented to contain placeholder dates (e.g. 1901,
    1969-12-31) and blanks. Anything before the file's coverage start
    is treated as a placeholder, not a real incident date.
    """
    value = value.strip()
    match = _DATE_RE.match(value)
    if not match:
        return None
    try:
        parsed = _dt.date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None
    if parsed < FILE_START:
        return None
    return parsed


def parse_year(value: str) -> int | None:
    """Model year; '9999' (unknown) and non-numeric values -> None."""
    value = value.strip()
    if value == UNKNOWN_YEAR or not value.isdigit():
        return None
    return int(value)


def parse_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_flag(value: str) -> bool | None:
    """Y/N flag; anything else (including blank) -> None."""
    value = value.strip().upper()
    if value == "Y":
        return True
    if value == "N":
        return False
    return None


class ParseStats:
    """Counts the loader reports so bad data is visible, not silent."""

    def __init__(self) -> None:
        self.rows_read = 0
        self.rows_kept = 0
        self.rows_repaired = 0  # extra/missing fields folded or padded
        self.bad_dates = 0
        self.bad_years = 0

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ParseStats(read={self.rows_read}, kept={self.rows_kept}, "
            f"repaired={self.rows_repaired}, bad_dates={self.bad_dates}, "
            f"bad_years={self.bad_years})"
        )


def _rows(path: Path, columns: list[str], stats: ParseStats) -> Iterator[dict[str, str]]:
    """Yield one dict per row, positional by the documented layout.

    The files have no header row, but a header is skipped if present
    (first field equal to the first column name). Quoting is OFF: the
    files are plain tab-delimited text, and complaint descriptions
    contain literal double quotes (e.g. `"MUSHY" BRAKES...`) that are
    data, not field quoting. With the csv module's default quoting, a
    stray opening quote swallows following lines looking for a close
    and silently merges rows — 1,249 rows were lost that way before
    this was fixed. A row with extra fields is repaired by folding the
    surplus into CDESCR, the only free-form long field; a short row is
    padded with blanks. Both cases are counted.
    """
    text_index = columns.index("CDESCR") if "CDESCR" in columns else None
    with path.open(encoding="utf-8", errors="replace", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)
        for raw in reader:
            if not raw:
                continue
            if raw[0] == columns[0] and stats.rows_read == 0:
                continue  # header row, if present
            stats.rows_read += 1
            if len(raw) != len(columns):
                stats.rows_repaired += 1
                if text_index is not None and len(raw) > len(columns):
                    surplus = len(raw) - len(columns)
                    merged = " ".join(raw[text_index : text_index + 1 + surplus])
                    raw = raw[:text_index] + [merged] + raw[text_index + 1 + surplus :]
                raw = (raw + [""] * len(columns))[: len(columns)]
            yield dict(zip(columns, raw, strict=True))


def parse_complaints(path: Path, stats: ParseStats | None = None) -> Iterator[dict[str, object]]:
    """Yield warehouse-ready complaint rows from a CMPL.txt file."""
    stats = stats or ParseStats()
    for row in _rows(path, COMPLAINT_COLUMNS, stats):
        faildate = parse_yyyymmdd(row["FAILDATE"])
        received = parse_yyyymmdd(row["LDATE"])
        added = parse_yyyymmdd(row["DATEA"])
        for value in (row["FAILDATE"], row["LDATE"], row["DATEA"]):
            if value.strip() and parse_yyyymmdd(value) is None:
                stats.bad_dates += 1
        year = parse_year(row["YEARTXT"])
        if row["YEARTXT"].strip() and year is None:
            stats.bad_years += 1
        out: dict[str, object] = {
            "cmplid": row["CMPLID"].strip(),
            "odino": row["ODINO"].strip(),
            "mfr_name": row["MFR_NAME"].strip(),
            "make": row["MAKETXT"].strip(),
            "model": row["MODELTXT"].strip(),
            "model_year": year,
            "crash": parse_flag(row["CRASH"]),
            "faildate": faildate,
            "fire": parse_flag(row["FIRE"]),
            "injured": parse_int(row["INJURED"]),
            "deaths": parse_int(row["DEATHS"]),
            "compdesc": row["COMPDESC"].strip(),
            "state": row["STATE"].strip(),
            "datea": added,
            "ldate": received,
            "miles": parse_int(row["MILES"]),
            "occurrences": parse_int(row["OCCURENCES"]),
            "cdescr": row["CDESCR"].strip(),
            "cmpl_type": row["CMPL_TYPE"].strip(),
            "prod_type": row["PROD_TYPE"].strip(),
            "medical_attn": parse_flag(row["MEDICAL_ATTN"]),
            "vehicles_towed_yn": parse_flag(row["VEHICLES_TOWED_YN"]),
            "state_of_incident": row["STATE_OF_INCIDENT"].strip(),
            "is_synthetic": False,
        }
        stats.rows_kept += 1
        yield out


def parse_recalls(path: Path, stats: ParseStats | None = None) -> Iterator[dict[str, object]]:
    """Yield warehouse-ready recall rows from an RCL.txt file."""
    stats = stats or ParseStats()
    for row in _rows(path, RECALL_COLUMNS, stats):
        year = parse_year(row["YEARTXT"])
        if row["YEARTXT"].strip() and year is None:
            stats.bad_years += 1
        out: dict[str, object] = {
            "record_id": parse_int(row["RECORD_ID"]),
            "campno": row["CAMPNO"].strip(),
            "make": row["MAKETXT"].strip(),
            "model": row["MODELTXT"].strip(),
            "model_year": year,
            "mfgcampno": row["MFGCAMPNO"].strip(),
            "compname": row["COMPNAME"].strip(),
            "mfgname": row["MFGNAME"].strip(),
            "bgman": parse_yyyymmdd(row["BGMAN"]),
            "endman": parse_yyyymmdd(row["ENDMAN"]),
            "rcltypecd": row["RCLTYPECD"].strip(),
            "potaff": parse_int(row["POTAFF"]),
            "odate": parse_yyyymmdd(row["ODATE"]),
            "influenced_by": row["INFLUENCED_BY"].strip(),
            "mfgtxt": row["MFGTXT"].strip(),
            "rcdate": parse_yyyymmdd(row["RCDATE"]),
            "datea": parse_yyyymmdd(row["DATEA"]),
            "rpno": row["RPNO"].strip(),
            "fmvss": row["FMVSS"].strip(),
            "desc_defect": row["DESC_DEFECT"].strip(),
            "conequence_defect": row["CONEQUENCE_DEFECT"].strip(),
            "corrective_action": row["CORRECTIVE_ACTION"].strip(),
            "notes": row["NOTES"].strip(),
            "rcl_cmpt_id": row["RCL_CMPT_ID"].strip(),
            "mfr_comp_name": row["MFR_COMP_NAME"].strip(),
            "mfr_comp_desc": row["MFR_COMP_DESC"].strip(),
            "mfr_comp_ptno": row["MFR_COMP_PTNO"].strip(),
            "do_not_drive": row["DO_NOT_DRIVE"].strip(),
            "park_outside": row["PARK_OUTSIDE"].strip(),
        }
        stats.rows_kept += 1
        yield out


def download(url: str, dest: Path) -> Path:
    """Download a file, following redirects. Raises on HTTP errors."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    urlretrieve(url, dest)  # noqa: S310 - public NHTSA host, no user input
    return dest
