"""Synthetic plant/production data generator.

Everything in this module is fictional and deterministic (fixed seed),
and is labeled synthetic wherever it is stored. Real NHTSA rows never
pass through here. See docs/data-plan.md for the join semantics: real
complaint rows carry no plant, so plant-level findings can only come
from the synthetic complaint rows this module emits.

Generation runs in two passes. First the builds pass emits a
production mix: how many vehicles of each make/model/year each plant
and line built, and whose part each batch carries. Then the complaints
pass emits synthetic complaint rows at a baseline rate per 1,000
vehicles built, with the planted multipliers applied only to rows
drawn from the implicated plant/line/supplier. A complaint's plant
assignment is a draw from the production mix, not an arbitrary label,
which is what makes each pattern detectable as a rate difference.
"""

from __future__ import annotations

import datetime as _dt
import random
from dataclasses import dataclass, field
from typing import cast

SEED = 42

# Fictional plants. Names are invented; no real factory is implied.
PLANTS = [
    {"plant_id": "riverside", "name": "Riverside", "state": "OH"},
    {"plant_id": "lakeside", "name": "Lakeside", "state": "MI"},
    {"plant_id": "summit", "name": "Summit", "state": "TN"},
    {"plant_id": "harborview", "name": "Harborview", "state": "CA"},
]

LINES = [
    {"line_id": "riverside-1", "plant_id": "riverside", "product": "brake assembly"},
    {"line_id": "riverside-2", "plant_id": "riverside", "product": "brake assembly"},
    {"line_id": "lakeside-1", "plant_id": "lakeside", "product": "steering rack"},
    {"line_id": "lakeside-2", "plant_id": "lakeside", "product": "steering rack"},
    {"line_id": "summit-1", "plant_id": "summit", "product": "final assembly"},
    {"line_id": "summit-2", "plant_id": "summit", "product": "final assembly"},
    {"line_id": "harborview-1", "plant_id": "harborview", "product": "final assembly"},
    {"line_id": "harborview-2", "plant_id": "harborview", "product": "final assembly"},
]

SUPPLIERS = [
    {"supplier_id": "acme-brakes", "name": "Acme Brakes", "part": "brake pads"},
    {"supplier_id": "beacon-brakes", "name": "Beacon Brakes", "part": "brake pads"},
    {"supplier_id": "apex-steering", "name": "Apex Steering", "part": "tie rods"},
    {"supplier_id": "meridian-airbags", "name": "Meridian Airbags", "part": "airbag inflators"},
]

# Component names match the NHTSA COMPDESC vocabulary.
COMPONENTS = [
    "SERVICE BRAKES",
    "STEERING",
    "AIR BAGS",
    "ENGINE",
    "ELECTRICAL SYSTEM",
    "SUSPENSION",
    "POWER TRAIN",
    "FUEL SYSTEM",
]

# Baseline synthetic complaints per 1,000 vehicles built, per component.
# Calibrated to the real data: the scope vehicles have 7,117 real
# complaint rows for ~1.15M fictional vehicles built, about 6.2 per
# 1,000 across all components. These rates sum to 6.4 per 1,000, so
# synthetic rows land in the same ballpark as real ones instead of
# outnumbering them 6-to-1. The planted multipliers (x6/x4/x3) still
# leave large, detectable rate gaps; the volume is what the rates
# imply, not a target in itself.
BASE_RATES = {
    "SERVICE BRAKES": 1.2,
    "STEERING": 0.8,
    "AIR BAGS": 0.6,
    "ENGINE": 1.0,
    "ELECTRICAL SYSTEM": 1.0,
    "SUSPENSION": 0.6,
    "POWER TRAIN": 0.8,
    "FUEL SYSTEM": 0.4,
}


# Planted patterns (docs/data-plan.md P1-P3). A pattern names the
# component, the model year, and where the multiplier applies; the
# multiplier is applied to synthetic complaint rows drawn from that
# slice only. P4 (volume decoy) is a property of the scope's vehicle
# counts, not a multiplier; P5 (recall explains part) is an
# eval-construction pattern, not a generator pattern.
@dataclass(frozen=True)
class Pattern:
    id: str
    component: str
    model_year: int
    plant_id: str | None
    line_id: str | None
    supplier_id: str | None
    multiplier: float


PATTERNS = [
    Pattern(
        id="P1",
        component="SERVICE BRAKES",
        model_year=2021,
        plant_id="riverside",
        line_id="riverside-2",
        supplier_id="acme-brakes",
        multiplier=6.0,
    ),
    Pattern(
        id="P2",
        component="STEERING",
        model_year=2019,
        plant_id="lakeside",
        line_id=None,
        supplier_id=None,
        multiplier=4.0,
    ),
    Pattern(
        id="P3",
        component="AIR BAGS",
        model_year=2020,
        plant_id=None,
        line_id=None,
        supplier_id=None,
        multiplier=3.0,
    ),
]

# Base share of each plant's production, per vehicle. P1's skew is
# applied on top: Riverside's share of the P1 model year rises.
PLANT_SHARES = {"riverside": 0.30, "lakeside": 0.25, "summit": 0.25, "harborview": 0.20}
P1_RIVERSIDE_SHARE = 0.55

# Supplier per part, per plant/line. Only Riverside line 2 changes
# with the year: Acme pads in the P1 year, Beacon otherwise.
PART_SUPPLIERS = {
    ("riverside-2", "brake pads"): {2021: "acme-brakes"},
    ("riverside-1", "brake pads"): {},
    ("lakeside-1", "tie rods"): {},
    ("lakeside-2", "tie rods"): {},
}
DEFAULT_SUPPLIER = {
    "brake pads": "beacon-brakes",
    "tie rods": "apex-steering",
    "airbag inflators": "meridian-airbags",
}

_TEXT_TEMPLATES = {
    "SERVICE BRAKES": [
        "Brake pedal feels soft and the vehicle pulls to one side when braking.",
        "Brakes squeal loudly and stopping distance feels longer than normal.",
        "Brake pedal goes to the floor at low speed.",
    ],
    "STEERING": [
        "Steering wheel shakes at highway speed and the vehicle drifts.",
        "Loud clunk from the front end when turning the wheel.",
        "Steering feels loose with excessive play.",
    ],
    "AIR BAGS": [
        "Airbag warning light stays on after starting the vehicle.",
        "Passenger airbag light indicates the airbag is off with an adult seated.",
        "Airbag deployed without a crash.",
    ],
}


@dataclass(frozen=True)
class VehicleScope:
    """One make/model and how many were built per model year."""

    make: str
    model: str
    vehicles_by_year: dict[int, int]


@dataclass
class GeneratedData:
    builds: list[dict[str, object]] = field(default_factory=list)
    complaints: list[dict[str, object]] = field(default_factory=list)


def _supplier_for(line_id: str, part: str, year: int) -> str:
    overrides = PART_SUPPLIERS.get((line_id, part), {})
    return overrides.get(year, DEFAULT_SUPPLIER[part])


def generate_builds(scope: list[VehicleScope], seed: int = SEED) -> list[dict[str, object]]:
    """Emit monthly production batches per vehicle, plant, and line."""
    rng = random.Random(seed)
    builds: list[dict[str, object]] = []
    batch_no = 0
    for vehicle in scope:
        for year, total in sorted(vehicle.vehicles_by_year.items()):
            shares = dict(PLANT_SHARES)
            if year == 2021:
                # P1 skew: Riverside builds a much larger share this year.
                extra = P1_RIVERSIDE_SHARE - shares["riverside"]
                shares["riverside"] = P1_RIVERSIDE_SHARE
                for plant_id in shares:
                    if plant_id != "riverside":
                        shares[plant_id] -= (
                            extra * shares[plant_id] / (1 - PLANT_SHARES["riverside"])
                        )
            for plant in PLANTS:
                plant_id = plant["plant_id"]
                plant_total = total * shares[plant_id]
                for line in [item for item in LINES if item["plant_id"] == plant_id]:
                    line_total = plant_total / 2
                    for month in range(1, 13):
                        batch_no += 1
                        jitter = rng.uniform(0.9, 1.1)
                        builds.append(
                            {
                                "build_id": f"B{batch_no:06d}",
                                "make": vehicle.make,
                                "model": vehicle.model,
                                "model_year": year,
                                "plant_id": plant_id,
                                "line_id": line["line_id"],
                                "build_date": _dt.date(year, month, 15),
                                "supplier_brake": _supplier_for(
                                    line["line_id"], "brake pads", year
                                ),
                                "supplier_steering": _supplier_for(
                                    line["line_id"], "tie rods", year
                                ),
                                "supplier_airbag": _supplier_for(
                                    line["line_id"], "airbag inflators", year
                                ),
                                "vehicles_built": max(1, round(line_total / 12 * jitter)),
                            }
                        )
    return builds


def _multiplier_for(
    component: str, year: int, plant_id: str, line_id: str, supplier_id: str | None
) -> float:
    multiplier = 1.0
    for pattern in PATTERNS:
        if pattern.component != component or pattern.model_year != year:
            continue
        if pattern.plant_id is not None and pattern.plant_id != plant_id:
            continue
        if pattern.line_id is not None and pattern.line_id != line_id:
            continue
        if pattern.supplier_id is not None and pattern.supplier_id != supplier_id:
            continue
        multiplier *= pattern.multiplier
    return multiplier


def _complaint_text(component: str, make: str, model: str, year: int, rng: random.Random) -> str:
    templates = _TEXT_TEMPLATES.get(component)
    if not templates:
        return f"Problem reported with the {component.lower()} on a {year} {make} {model}."
    return rng.choice(templates)


def generate_complaints(
    builds: list[dict[str, object]], scope: list[VehicleScope], seed: int = SEED
) -> list[dict[str, object]]:
    """Emit synthetic complaint rows, one per complaint.

    Counts per batch/component follow the baseline rate times any
    planted multiplier for that slice, with deterministic stochastic
    rounding from the seeded RNG. Each row's plant/line/supplier come
    from its batch, so the plant assignment is a draw from the
    production mix.
    """
    rng = random.Random(seed + 1)
    supplier_by_part = {
        "SERVICE BRAKES": "supplier_brake",
        "STEERING": "supplier_steering",
        "AIR BAGS": "supplier_airbag",
    }
    complaints: list[dict[str, object]] = []
    seq = 0
    for batch in builds:
        year = cast(int, batch["model_year"])
        vehicles = cast(int, batch["vehicles_built"])
        for component in COMPONENTS:
            supplier_key = supplier_by_part.get(component)
            supplier_id = str(batch[supplier_key]) if supplier_key else None
            multiplier = _multiplier_for(
                component, year, str(batch["plant_id"]), str(batch["line_id"]), supplier_id
            )
            expected = vehicles * BASE_RATES[component] / 1000 * multiplier
            count = int(expected) + (1 if rng.random() < (expected - int(expected)) else 0)
            for _ in range(count):
                seq += 1
                month = rng.randint(1, 12)
                day = rng.randint(1, 28)
                faildate = _dt.date(year, month, day)
                complaints.append(
                    {
                        "cmplid": f"SYN-{seq:07d}",
                        "odino": f"SYN-{seq:07d}",
                        "mfr_name": str(batch["make"]).upper(),
                        "make": batch["make"],
                        "model": batch["model"],
                        "model_year": year,
                        "crash": rng.random() < 0.02,
                        "faildate": faildate,
                        "fire": False,
                        "injured": 0,
                        "deaths": 0,
                        "compdesc": component,
                        "state": rng.choice(["OH", "MI", "CA", "TX", "FL"]),
                        "datea": faildate,
                        "ldate": faildate,
                        "miles": rng.randint(5_000, 90_000),
                        "occurrences": 1,
                        "cdescr": _complaint_text(
                            component, str(batch["make"]), str(batch["model"]), year, rng
                        ),
                        "cmpl_type": "VOQ",
                        "prod_type": "V",
                        "medical_attn": False,
                        "vehicles_towed_yn": False,
                        "state_of_incident": rng.choice(["OH", "MI", "CA", "TX", "FL"]),
                        "is_synthetic": True,
                        "plant_id": batch["plant_id"],
                        "line_id": batch["line_id"],
                        "supplier_id": supplier_id,
                    }
                )
    return complaints


def generate(scope: list[VehicleScope], seed: int = SEED) -> GeneratedData:
    """Run both passes; the single entry point for loaders and tests."""
    builds = generate_builds(scope, seed)
    complaints = generate_complaints(builds, scope, seed)
    return GeneratedData(builds=builds, complaints=complaints)
