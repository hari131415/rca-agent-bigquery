"""Tests for the seed script's sample-based rebuild path."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import duckdb
from data.loaders import load_duckdb, nhtsa, synthetic


def _load_seed() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "seed_offline",
        Path(__file__).resolve().parent.parent.parent / "scripts" / "seed_offline.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so @dataclass can resolve annotations
    # against this module (dataclasses reads sys.modules).
    sys.modules["seed_offline"] = module
    spec.loader.exec_module(module)
    return module


def _row(make: str, model: str, year: int, compdesc: str) -> dict[str, object]:
    return {
        "cmplid": f"{make[:2]}{year}",
        "odino": f"{make[:2]}{year}",
        "mfr_name": make,
        "make": make,
        "model": model,
        "model_year": year,
        "crash": False,
        "faildate": None,
        "fire": False,
        "injured": 0,
        "deaths": 0,
        "compdesc": compdesc,
        "state": "OH",
        "datea": None,
        "ldate": None,
        "miles": None,
        "occurrences": 1,
        "cdescr": "Test complaint.",
        "cmpl_type": "VOQ",
        "prod_type": "V",
        "medical_attn": False,
        "vehicles_towed_yn": False,
        "state_of_incident": "OH",
        "is_synthetic": False,
        "plant_id": None,
        "line_id": None,
        "supplier_id": None,
    }


def _recall(make: str, model: str, year: int, campno: str) -> dict[str, object]:
    return {
        "record_id": 1,
        "campno": campno,
        "make": make,
        "model": model,
        "model_year": year,
        "mfgcampno": "",
        "compname": "SERVICE BRAKES",
        "mfgname": make,
        "bgman": None,
        "endman": None,
        "rcltypecd": "V",
        "potaff": 100,
        "odate": None,
        "influenced_by": "MFR",
        "mfgtxt": make,
        "rcdate": None,
        "datea": None,
        "rpno": "",
        "fmvss": "",
        "desc_defect": "Test defect.",
        "conequence_defect": "",
        "corrective_action": "",
        "notes": "",
        "rcl_cmpt_id": "",
        "mfr_comp_name": "",
        "mfr_comp_desc": "",
        "mfr_comp_ptno": "",
        "do_not_drive": "",
        "park_outside": "",
    }


def test_from_sample_rebuild(tmp_path: Path) -> None:
    seed = _load_seed()
    complaints_csv = tmp_path / "complaints_sample.csv"
    recalls_csv = tmp_path / "recalls_sample.csv"
    rows = [_row("FORD", "F-150", year, "SERVICE BRAKES") for year in (2019, 2020, 2021)] + [
        _row("HONDA", "PILOT", year, "STEERING") for year in (2019, 2020, 2021)
    ]
    seed.write_csv(rows, complaints_csv, load_duckdb.COMPLAINT_COLUMNS)
    seed.write_csv(
        [_recall("FORD", "F-150", 2021, "21V000000")],
        recalls_csv,
        load_duckdb.RECALL_COLUMNS,
    )

    scope = seed.scope_from_sample(complaints_csv)
    assert [(s.make, s.model) for s in scope] == [("FORD", "F-150"), ("HONDA", "PILOT")]
    generated = synthetic.generate(scope)
    db_path = tmp_path / "offline.duckdb"
    counts = seed.build_warehouse(complaints_csv, recalls_csv, generated, db_path)
    assert counts == {"real": 6, "synthetic": len(generated.complaints), "recalls": 1}

    con = duckdb.connect(str(db_path), read_only=True)
    syn_count = con.execute("SELECT COUNT(*) FROM complaints WHERE is_synthetic").fetchone()
    real_count = con.execute("SELECT COUNT(*) FROM complaints WHERE NOT is_synthetic").fetchone()
    rcl_count = con.execute("SELECT COUNT(*) FROM recalls").fetchone()
    assert syn_count is not None and syn_count[0] == len(generated.complaints)
    assert real_count is not None and real_count[0] == 6
    assert rcl_count is not None and rcl_count[0] == 1
    con.close()


def test_parse_counts_match_physical_lines(tmp_path: Path) -> None:
    # Regression test for the quote-merge bug: a complaint text with a
    # stray quote used to swallow the next row. The parser must count
    # exactly the physical lines.
    values = [""] * 51
    values[0] = "1"
    values[3] = "FORD"
    values[19] = '"MUSHY" BRAKES DUE TO AIR IN SYSTEM.'
    line1 = "\t".join(values)
    values[0] = "2"
    values[19] = "Normal text."
    line2 = "\t".join(values)
    path = tmp_path / "CMPL.txt"
    path.write_text(line1 + "\n" + line2 + "\n", encoding="utf-8")
    stats = nhtsa.ParseStats()
    rows = list(nhtsa.parse_complaints(path, stats))
    assert stats.rows_read == 2
    assert rows[0]["cdescr"] == '"MUSHY" BRAKES DUE TO AIR IN SYSTEM.'
    assert rows[1]["cdescr"] == "Normal text."
