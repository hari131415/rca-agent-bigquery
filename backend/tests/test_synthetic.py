"""Tests for the synthetic plant-data generator."""

from data.loaders import synthetic
from data.loaders.synthetic import VehicleScope

SCOPE = [
    VehicleScope(
        make="FORD", model="F-150", vehicles_by_year={2019: 100_000, 2020: 100_000, 2021: 100_000}
    ),
    VehicleScope(
        make="TOYOTA", model="RAV4", vehicles_by_year={2019: 100_000, 2020: 100_000, 2021: 200_000}
    ),
]


def test_generate_is_deterministic() -> None:
    first = synthetic.generate(SCOPE)
    second = synthetic.generate(SCOPE)
    assert first.builds == second.builds
    assert first.complaints == second.complaints


def test_builds_cover_every_plant_and_line() -> None:
    builds = synthetic.generate_builds(SCOPE)
    assert {(b["plant_id"], b["line_id"]) for b in builds} == {
        (line["plant_id"], line["line_id"]) for line in synthetic.LINES
    }
    # Monthly batches: 2 vehicles x 3 years x 8 lines x 12 months.
    assert len(builds) == 2 * 3 * 8 * 12


def test_p1_supplier_is_acme_only_in_2021() -> None:
    builds = synthetic.generate_builds(SCOPE)
    riverside_2021 = [
        b for b in builds if b["line_id"] == "riverside-2" and b["model_year"] == 2021
    ]
    riverside_2020 = [
        b for b in builds if b["line_id"] == "riverside-2" and b["model_year"] == 2020
    ]
    assert riverside_2021 and all(b["supplier_brake"] == "acme-brakes" for b in riverside_2021)
    assert riverside_2020 and all(b["supplier_brake"] == "beacon-brakes" for b in riverside_2020)


def test_synthetic_complaints_carry_plant_linkage() -> None:
    data = synthetic.generate(SCOPE)
    assert data.complaints
    assert all(c["is_synthetic"] is True for c in data.complaints)
    assert all(c["plant_id"] and c["line_id"] for c in data.complaints)
    # Brake complaints carry a supplier; engine complaints do not.
    brakes = [c for c in data.complaints if c["compdesc"] == "SERVICE BRAKES"]
    engines = [c for c in data.complaints if c["compdesc"] == "ENGINE"]
    assert brakes and all(c["supplier_id"] for c in brakes)
    assert engines and all(c["supplier_id"] is None for c in engines)


def test_p1_brake_rate_highest_at_riverside() -> None:
    data = synthetic.generate(SCOPE)
    by_plant: dict[str, int] = {}
    for complaint in data.complaints:
        if complaint["compdesc"] == "SERVICE BRAKES" and complaint["model_year"] == 2021:
            by_plant[str(complaint["plant_id"])] = by_plant.get(str(complaint["plant_id"]), 0) + 1
    built: dict[str, int] = {}
    for batch in data.builds:
        if batch["model_year"] == 2021:
            built[str(batch["plant_id"])] = built.get(str(batch["plant_id"]), 0) + int(
                batch["vehicles_built"]
            )
    rates = {plant: count * 1000 / built[plant] for plant, count in by_plant.items()}
    top = max(rates.items(), key=lambda item: item[1])[0]
    assert top == "riverside"
    assert rates["riverside"] > 3 * sorted(rates.values())[-2]
