"""Tests for the NHTSA flat-file parser."""

import datetime as _dt
from pathlib import Path

import pytest
from data.loaders import nhtsa


def test_parse_yyyymmdd_valid() -> None:
    assert nhtsa.parse_yyyymmdd("20210115") == _dt.date(2021, 1, 15)


def test_parse_yyyymmdd_rejects_placeholders_and_blanks() -> None:
    assert nhtsa.parse_yyyymmdd("") is None
    assert nhtsa.parse_yyyymmdd("19010101") is None  # known placeholder year
    assert nhtsa.parse_yyyymmdd("19691231") is None  # known placeholder date
    assert nhtsa.parse_yyyymmdd("20210230") is None  # impossible day
    assert nhtsa.parse_yyyymmdd("not-a-date") is None


def test_parse_year() -> None:
    assert nhtsa.parse_year("2021") == 2021
    assert nhtsa.parse_year("9999") is None  # unknown
    assert nhtsa.parse_year("") is None
    assert nhtsa.parse_year("abcd") is None


def test_parse_flag() -> None:
    assert nhtsa.parse_flag("Y") is True
    assert nhtsa.parse_flag("N") is False
    assert nhtsa.parse_flag("") is None
    assert nhtsa.parse_flag("x") is None


def _fixture(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "CMPL.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _row(**overrides: str) -> str:
    values = [""] * 51
    values[0] = "123456789"  # CMPLID
    values[1] = "987654321"  # ODINO
    values[3] = "FORD"  # MAKETXT
    values[4] = "F-150"  # MODELTXT
    values[5] = "2021"  # YEARTXT
    values[7] = "20210301"  # FAILDATE
    values[11] = "SERVICE BRAKES"  # COMPDESC
    values[16] = "20210305"  # LDATE
    values[19] = "Brakes squeal."  # CDESCR
    for key, val in overrides.items():
        values[nhtsa.COMPLAINT_COLUMNS.index(key)] = val
    return "\t".join(values)


def test_parse_complaints_basic(tmp_path: Path) -> None:
    path = _fixture(tmp_path, [_row()])
    rows = list(nhtsa.parse_complaints(path))
    assert len(rows) == 1
    row = rows[0]
    assert row["make"] == "FORD"
    assert row["model"] == "F-150"
    assert row["model_year"] == 2021
    assert row["faildate"] == _dt.date(2021, 3, 1)
    assert row["compdesc"] == "SERVICE BRAKES"
    assert row["cdescr"] == "Brakes squeal."
    assert row["is_synthetic"] is False
    assert "vin" not in row  # dropped: identifies a person
    assert "vehicle_operator" not in row


def test_parse_complaints_repairs_extra_fields_into_text(tmp_path: Path) -> None:
    # A tab inside the complaint text adds fields; they fold back into CDESCR.
    line = _row()
    parts = line.split("\t")
    parts[19] = "Brakes\tsqueal\tloudly."
    path = _fixture(tmp_path, ["\t".join(parts)])
    stats = nhtsa.ParseStats()
    rows = list(nhtsa.parse_complaints(path, stats))
    assert rows[0]["cdescr"] == "Brakes squeal loudly."
    assert stats.rows_repaired == 1


def test_parse_complaints_stray_quote_does_not_merge_rows(tmp_path: Path) -> None:
    # Complaint text contains literal quotes; the file has no quoting,
    # so a stray opening quote must not swallow the following row.
    line1 = _row(CDESCR='"URGENT brakes failed.')
    line2 = _row(CMPLID="222222222")
    path = _fixture(tmp_path, [line1, line2])
    rows = list(nhtsa.parse_complaints(path))
    assert len(rows) == 2
    assert rows[0]["cdescr"] == '"URGENT brakes failed.'
    assert rows[1]["cmplid"] == "222222222"


def test_parse_complaints_skips_header_row(tmp_path: Path) -> None:
    header = "\t".join(nhtsa.COMPLAINT_COLUMNS)
    path = _fixture(tmp_path, [header, _row()])
    rows = list(nhtsa.parse_complaints(path))
    assert len(rows) == 1


def test_parse_recalls_basic(tmp_path: Path) -> None:
    values = [""] * 29
    values[0] = "1"
    values[1] = "21V000000"
    values[2] = "FORD"
    values[3] = "F-150"
    values[4] = "2021"
    values[6] = "SERVICE BRAKES"
    values[19] = "Brake pads may fail."
    path = tmp_path / "RCL.txt"
    path.write_text("\t".join(values) + "\n", encoding="utf-8")
    rows = list(nhtsa.parse_recalls(path))
    assert len(rows) == 1
    assert rows[0]["campno"] == "21V000000"
    assert rows[0]["model_year"] == 2021
    assert rows[0]["desc_defect"] == "Brake pads may fail."


@pytest.mark.parametrize("url", [nhtsa.COMPLAINTS_URL, nhtsa.RECALLS_URL])
def test_canonical_urls_are_odi_hosts(url: str) -> None:
    assert "odi.nhtsa.dot.gov" in url
