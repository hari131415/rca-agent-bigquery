"""Deterministic claim validator: a number a hypothesis or finding
cites must appear in its supporting rows.

A claimed number matches when it equals a numeric value in any
evidence row, or the sum of a numeric column within one query's
rows, within rounding tolerance — the claimed number's own
precision, or 1% relative, whichever is looser. Four-digit years
and the rate denominator 1,000 are excluded: years are
identifiers, and 1,000 is the defined rate unit, not a
measurement. Numbers derived from one query's rows within one
column also match: the ratio of two values ("3.4 times higher"),
their difference ("2.92 more per 1,000"), and a value's share of
the column total ("59% covered", shares only for non-rate
columns). A number derived across columns or across queries —
a rate computed from counts in two columns, a grand total no
query returned — is still flagged: the SQL is supposed to return
the numbers the answer states. A claim with no numbers is
verified: there is nothing to mismatch.
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


def _derived_values(rows: list[dict[str, Any]]) -> list[float]:
    """Numbers derivable from one query's rows within one column.

    A ratio ("3.4 times higher" = one rate over another), a
    difference ("2.92 more per 1,000"), and a share of the column
    total ("59% covered"). Shares are computed only for columns
    that are not rates: a share of a rate column has no meaning
    (rates do not sum to a total), while a share of counts or
    production volumes does. Derivations stay inside one query
    and one column: a number built from two different columns or
    two different queries is not a derivation the evidence
    supports on its own.
    """
    values: list[float] = []
    columns: dict[str, list[float]] = {}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                columns.setdefault(key, []).append(float(value))
    for key, column_values in columns.items():
        # Years are identifiers, not measurements: no derivation
        # is built from a column of four-digit years.
        if all(float(v).is_integer() and 1900 <= v <= 2099 for v in column_values):
            continue
        total = sum(column_values)
        for i, a in enumerate(column_values):
            if total and "rate" not in key.lower():
                values.append(a / total * 100.0)
            for j, b in enumerate(column_values):
                if i == j:
                    continue
                values.append(a - b)
                if b:
                    values.append(a / b)
    return values


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
        values += _derived_values(rows)
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


# A rise is "roughly uniform" across the entities of a dimension
# when the highest rate is less than twice the lowest. The
# threshold sits between the planted patterns, measured on the
# warehouse: the localized spikes are 3.6x (P1 brake by plant,
# Riverside 4.14 vs 1.14) and 4.3x (P2 steering by plant, Lakeside
# 3.21 vs 0.74), while the fleet-wide rise is 1.02x (P3 airbag by
# plant, 1.80–1.83) and 1.05x (P3 airbag by line, 1.77–1.86). With
# ~1,000+ vehicles per plant-year the rates are stable enough that
# a 2x difference is a real difference, not sampling noise.
UNIFORMITY_MAX_MIN_RATIO = 2.0


def _rate_pairs(evidence: list[dict[str, Any]], dimension: str) -> dict[Any, dict[Any, float]]:
    """Rates per entity, grouped by model year when the rows carry
    one: {(year, entity): rate}. A column counts as a rate when its
    name contains "rate". The highest rate per (year, entity) wins:
    an entity may appear in several queries for the same year."""
    pairs: dict[Any, dict[Any, float]] = {}
    for item in evidence:
        for row in item.get("rows", []):
            if dimension not in row:
                continue
            rate = next(
                (
                    float(value)
                    for key, value in row.items()
                    if "rate" in key.lower()
                    and isinstance(value, (int, float))
                    and not isinstance(value, bool)
                ),
                None,
            )
            if rate is None:
                continue
            year = row.get("model_year")
            entity = row[dimension]
            group = pairs.setdefault(year, {})
            group[entity] = max(rate, group.get(entity, rate))
    return pairs


def _uniformity_for_dimension(
    evidence: list[dict[str, Any]], dimension: str
) -> tuple[bool | None, float | None, int]:
    """(uniform, worst max/min ratio, entities compared) for one
    dimension, or (None, None, 0) when the evidence has no year
    with two or more entities carrying rates. Ratios are computed
    within each year, never pooled across years: a spike localized
    to one year must not be averaged away."""
    pairs = _rate_pairs(evidence, dimension)
    ratios = []
    entities = 0
    for group in pairs.values():
        rates = [rate for rate in group.values() if rate > 0]
        if len(rates) < 2:
            continue
        entities = max(entities, len(rates))
        ratios.append(max(rates) / min(rates))
    if not ratios:
        return None, None, 0
    worst = max(ratios)
    return worst < UNIFORMITY_MAX_MIN_RATIO, worst, entities


def check_rate_uniformity(evidence: list[dict[str, Any]], cause_type: str) -> dict[str, Any]:
    """Is the rise roughly uniform across the entities of the
    verdict's dimension? Returns a dict with "checked" (whether
    the evidence had comparable rates at all), "uniform", the
    worst max/min "ratio", the "dimension" judged, how many
    "entities" were compared, and a "note".

    The verdict's own dimension is judged first. When it cannot
    be judged — a dimension with a single entity, such as the one
    airbag supplier — uniformity is judged on the other
    dimensions, and the verdict is overridden only when every
    comparable dimension is uniform. A sole-supplier cause is
    indistinguishable from a fleet-wide rise on rates alone (one
    supplier's defect would show up at every plant), so a uniform
    rise is reported as fleet-wide rather than attributed to the
    only supplier that exists.
    """
    if cause_type not in VERDICT_DIMENSION:
        return {
            "checked": False,
            "uniform": False,
            "ratio": None,
            "dimension": None,
            "entities": 0,
            "note": f"no uniformity check for cause type {cause_type}",
        }
    dimension = VERDICT_DIMENSION[cause_type]
    uniform, ratio, entities = _uniformity_for_dimension(evidence, dimension)
    if uniform is not None:
        return {
            "checked": True,
            "uniform": uniform,
            "ratio": ratio,
            "dimension": dimension,
            "entities": entities,
            "note": "",
        }
    others = [
        (other, *_uniformity_for_dimension(evidence, other))
        for other in ("plant_id", "line_id", "supplier_id")
        if other != dimension
    ]
    judged = [(name, u, r, n) for name, u, r, n in others if u is not None]
    if not judged:
        return {
            "checked": False,
            "uniform": False,
            "ratio": None,
            "dimension": dimension,
            "entities": 0,
            "note": "no comparable rates for any dimension",
        }
    uniform_all = all(u for _, u, _, _ in judged)
    worst = max(r for _, _, r, _ in judged if r is not None)
    names = ", ".join(name for name, _, _, _ in judged)
    return {
        "checked": True,
        "uniform": uniform_all,
        "ratio": worst,
        "dimension": names,
        "entities": max(n for _, _, _, n in judged),
        "note": (f"{dimension} has a single entity in the evidence; uniformity judged on {names}"),
    }


def check_confound(
    evidence: list[dict[str, Any]], verdict: dict[str, Any]
) -> dict[str, str] | None:
    """Does the evidence leave a plant/supplier confound open? If
    the verdict names a plant or a supplier, the other dimension
    may be the real cause: Riverside is both the plant with the
    spike and the plant where Acme brakes were introduced, so a
    plant rate alone cannot tell them apart. Returns a follow-up
    dict (description + reason) stratifying one dimension within
    the other, or None when there is nothing to disentangle.

    It fires when the investigation compared both dimensions (a
    rate query exists for each) or when the verdict dimension
    shows a real concentration (top rate at least twice the
    second) while the other dimension was never examined. A
    follow-up that already ran is the caller's problem: this
    function only says whether one is needed.
    """
    cause_type = verdict.get("cause_type")
    entity = verdict.get("entity")
    if cause_type not in ("plant", "supplier") or not entity:
        return None
    other = "supplier_id" if cause_type == "plant" else "plant_id"
    own = VERDICT_DIMENSION[cause_type]
    own_pairs = _rate_pairs(evidence, own)
    # A real concentration within one year: the top rate at least
    # twice the second. Rates from different years are never
    # compared with each other.
    dominant = False
    for group in own_pairs.values():
        rates = sorted((rate for rate in group.values() if rate > 0), reverse=True)
        if len(rates) >= 2 and rates[0] >= UNIFORMITY_MAX_MIN_RATIO * rates[1]:
            dominant = True
            break
    other_examined = bool(_rate_pairs(evidence, other))
    own_examined = bool(own_pairs)
    if not own_examined:
        return None
    if not other_examined and not dominant:
        return None
    if cause_type == "plant":
        description = (
            f"Compute the complaint rate by supplier within plant {entity} "
            "for the same component and model year as the prior queries "
            "(all suppliers at that plant), to check whether the plant "
            "effect is really a supplier effect."
        )
    else:
        description = (
            f"Compute the complaint rate by plant for supplier {entity} "
            "for the same component and model year as the prior queries "
            "(all plants using that supplier), to check whether the "
            "supplier effect is really a plant effect."
        )
    return {
        "description": description,
        "reason": (
            f"The verdict names {cause_type} {entity}, but the evidence "
            "does not separate the plant effect from the supplier "
            "effect: one may be standing in for the other."
        ),
    }
