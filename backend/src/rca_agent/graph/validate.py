"""Deterministic claim validator: a number a hypothesis or finding
cites must appear in its supporting rows.

A claimed number matches when it equals a numeric value in any
evidence row, or the sum of a numeric column within one query's
rows, within rounding tolerance — the claimed number's own
precision, or 1% relative, whichever is looser. Four-digit years
and the rate denominator 1,000 are excluded: years are
identifiers, and 1,000 is the defined rate unit, not a
measurement. Derived numbers that are neither a row value nor a
column sum (a percentage, a ratio) are flagged as unverified —
that is a known limitation of this first version, not a bug. A
claim with no numbers is verified: there is nothing to mismatch.
"""

from __future__ import annotations

import re
from typing import Any

_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9-])-?\d[\d,]*\.?\d*(?![A-Za-z0-9])")
# The lookarounds keep an identifier such as a recall campaign
# number ("26V104000") or a model name ("F-150") from being read as
# a claimed number: a number must not touch a letter, a digit, or a
# hyphen on its left, or a letter or digit on its right. (A hyphenated
# range such as "7.55-7.93" therefore yields only its first number;
# the analyses write ranges with "and" instead.)
_RATE_DENOMINATOR = 1000.0


def _decimals(raw: str) -> int:
    if "." not in raw:
        return 0
    return len(raw.split(".", 1)[1])


def _is_excluded(raw: str, value: float) -> bool:
    if "." not in raw and len(raw) == 4 and 1900 <= int(raw) <= 2099:
        return True
    return value == _RATE_DENOMINATOR


def _matches(claimed_raw: str, claimed: float, values: list[float]) -> bool:
    tolerance = max(0.5 * 10 ** -_decimals(claimed_raw), 0.01 * abs(claimed))
    return any(abs(claimed - value) <= tolerance for value in values)


def _numeric_values(evidence: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for item in evidence:
        rows = item.get("rows", [])
        columns: dict[str, list[float]] = {}
        for row in rows:
            for key, value in row.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    values.append(float(value))
                    columns.setdefault(key, []).append(float(value))
        for column_values in columns.values():
            values.append(sum(column_values))
    return values


def _claim_verified(text: str, values: list[float]) -> bool:
    for match in _NUMBER_RE.finditer(text):
        raw = match.group(0).replace(",", "")
        value = float(raw)
        if _is_excluded(raw, value):
            continue
        if not _matches(raw, value, values):
            return False
    return True


def validate_claims(
    analysis: dict[str, Any], evidence: list[dict[str, Any]]
) -> tuple[dict[str, Any], int]:
    """Annotate the analysis with verification flags and return it
    with the count of unverified claims (summary + hypotheses)."""
    values = _numeric_values(evidence)
    unverified = 0
    summary = analysis.get("summary", "")
    summary_verified = _claim_verified(summary, values)
    analysis["summary_verified"] = summary_verified
    if not summary_verified:
        unverified += 1
    for hypothesis in analysis.get("ranked_hypotheses", []):
        verified = _claim_verified(hypothesis.get("evidence", ""), values)
        hypothesis["verified"] = verified
        if not verified:
            unverified += 1
    return analysis, unverified


GROUP_COLUMNS = ("plant_id", "line_id", "supplier_id", "model_year", "make", "model")
VOLUME_COLUMNS = ("vehicles", "vehicles_built")

# The column that carries the verdict's entity for each cause type.
# Volume is special: its entity is a "make-model" string (e.g.
# "honda-pilot"), matched against the make and model columns
# together. fleet_wide and none carry no entity, so there is
# nothing to ground.
VERDICT_DIMENSION = {
    "plant": "plant_id",
    "supplier": "supplier_id",
    "line": "line_id",
    "recall": "campno",
}


def check_verdict_grounding(verdict: dict[str, Any], evidence: list[dict[str, Any]]) -> bool:
    """True when the verdict's entity appears in a supporting
    query's result rows for the matching dimension.

    A verdict names a cause (a plant, a supplier, a line, a
    make/model, a recall campaign) and the indices of the evidence
    blocks it rests on. If none of those blocks has a row carrying
    the entity in the column for that cause type, the verdict is
    not grounded in the evidence — it is a claim the agent made
    without a supporting row. (The smoke test caught this: the
    verdict said supplier/acme-brakes, but the only supporting
    query grouped by plant, so no supporting row contained the
    supplier.) fleet_wide, none, and insufficient_evidence carry
    no entity, so they are grounded by definition. A verdict with
    no supporting queries is not grounded.
    """
    cause_type = verdict.get("cause_type")
    entity = verdict.get("entity")
    if cause_type in ("fleet_wide", "none", "insufficient_evidence") or entity is None:
        return True
    if not isinstance(cause_type, str):
        return False
    supporting = verdict.get("supporting_queries") or []
    if not supporting:
        return False
    entity_text = str(entity).strip().lower()
    for index in supporting:
        if not isinstance(index, int) or not 0 <= index < len(evidence):
            continue
        for row in evidence[index].get("rows", []):
            if cause_type == "volume":
                make = str(row.get("make", "")).strip().lower()
                model = str(row.get("model", "")).strip().lower()
                if make and model and f"{make}-{model}" == entity_text:
                    return True
                continue
            column = VERDICT_DIMENSION.get(cause_type)
            if column is None:
                continue
            if column in row and str(row[column]).strip().lower() == entity_text:
                return True
    return False


def check_rate_comparison(analysis: dict[str, Any], evidence: list[dict[str, Any]]) -> list[str]:
    """Flag a group comparison that leans on counts with no rate or
    volume check for the same dimension.

    For each grouping dimension (plant, line, supplier, model year,
    make, model): the analysis cites a complaint count from a query
    whose rows span two or more groups of that dimension, and no
    query whose rows span that dimension carries a rate (a column
    containing "rate") or a production volume. Counts alone cannot
    tell a defect apart from a larger production volume — that is
    the P4 trap, and it applies to suppliers exactly as it applies
    to plants and model years. A rate check in a different dimension
    does not cover the comparison: a plant-rate query does not make
    a supplier-count comparison safe.
    """
    texts = [analysis.get("summary", "")]
    texts += [h.get("evidence", "") for h in analysis.get("ranked_hypotheses", [])]
    numbers: list[tuple[str, float]] = []
    for text in texts:
        for match in _NUMBER_RE.finditer(text):
            raw = match.group(0).replace(",", "")
            value = float(raw)
            if _is_excluded(raw, value):
                continue
            numbers.append((raw, value))
    if not numbers:
        return []
    for dimension in GROUP_COLUMNS:
        spanning = [
            item
            for item in evidence
            if len({row.get(dimension) for row in item.get("rows", []) if dimension in row}) >= 2
        ]
        if not spanning:
            continue
        count_values: list[float] = []
        for item in spanning:
            block_counts = [
                float(row["n"])
                for row in item.get("rows", [])
                if "n" in row and isinstance(row["n"], (int, float))
            ]
            count_values += block_counts + [sum(block_counts)]
        cites_count = any(_matches(raw, value, count_values) for raw, value in numbers)
        if not cites_count:
            continue
        rate_for_dimension = any(
            "rate" in key.lower() or key in VOLUME_COLUMNS
            for item in spanning
            for row in item.get("rows", [])
            for key in row
        )
        if not rate_for_dimension:
            return [
                "This comparison uses complaint counts, but no rate per 1,000 "
                "vehicles built (or production volume) is in the evidence. "
                "Counts alone cannot tell a defect apart from a larger "
                "production volume."
            ]
    return []
