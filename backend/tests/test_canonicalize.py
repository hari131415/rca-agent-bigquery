"""Entity canonicalization: one form per cause type, used by the
grader, the grounding check, and the analyze prompt.

The dev live run's P4 misses were format, not reasoning: the model
answered volume/"HONDA PILOT" (and once "Honda Pilot") while the
benchmark expects "honda-pilot", and the grounding check flagged
those verdicts ungrounded for the same reason. The multi-recall
verdicts (Run 2's T14, five campaigns; the dev run's D13, two)
are the other half: the entity field must name exactly one recall.
"""

from __future__ import annotations

from rca_agent.graph.nodes import build_analyze_prompt
from rca_agent.graph.validate import (
    canonicalize_entity,
    check_verdict_grounding,
    is_multi_recall_entity,
)


def test_canonicalize_volume_variants() -> None:
    assert canonicalize_entity("volume", "HONDA PILOT") == "honda-pilot"
    assert canonicalize_entity("volume", "Honda Pilot") == "honda-pilot"
    assert canonicalize_entity("volume", "honda-pilot") == "honda-pilot"


def test_canonicalize_other_types() -> None:
    assert canonicalize_entity("plant", "Riverside") == "riverside"
    assert canonicalize_entity("plant", "riverside") == "riverside"
    assert canonicalize_entity("line", "Riverside 2") == "riverside-2"
    assert canonicalize_entity("line", "riverside-2") == "riverside-2"
    assert canonicalize_entity("supplier", "Acme Brakes") == "acme-brakes"
    assert canonicalize_entity("recall", "26v104000") == "26V104000"
    assert canonicalize_entity("recall", "26V104000") == "26V104000"
    assert canonicalize_entity("none", None) is None
    assert canonicalize_entity("fleet_wide", None) is None


def test_canonicalize_multi_recall_matches_nothing() -> None:
    entity = canonicalize_entity("recall", "23V858000, 21V215000")
    assert entity != "23V858000"
    assert is_multi_recall_entity("23V858000, 21V215000")
    assert is_multi_recall_entity("21V533000, 22V193000, 23V509000, 25V132000, 26V104000")
    assert not is_multi_recall_entity("26V104000")
    assert not is_multi_recall_entity(None)


def test_grounding_accepts_volume_case_variants() -> None:
    evidence = [
        {"sql": "SELECT make, model FROM builds", "rows": [{"make": "HONDA", "model": "PILOT"}]}
    ]
    for entity in ("HONDA PILOT", "Honda Pilot", "honda-pilot"):
        assert check_verdict_grounding(
            {"cause_type": "volume", "entity": entity, "supporting_queries": [0]},
            evidence,
        )


def test_grounding_refuses_multi_recall_entity() -> None:
    evidence = [
        {
            "sql": "SELECT campno FROM recalls",
            "rows": [{"campno": "23V858000"}, {"campno": "21V215000"}],
        }
    ]
    assert not check_verdict_grounding(
        {"cause_type": "recall", "entity": "23V858000, 21V215000", "supporting_queries": [0]},
        evidence,
    )
    assert check_verdict_grounding(
        {"cause_type": "recall", "entity": "23V858000", "supporting_queries": [0]},
        evidence,
    )


def test_analyze_prompt_names_canonical_forms_and_one_recall() -> None:
    prompt = build_analyze_prompt("Why did complaints rise?", [])
    assert "honda-pilot" in prompt
    assert "never 'HONDA PILOT' or 'Honda Pilot'" in prompt
    assert "exactly ONE campaign number" in prompt
    assert "never put several campaign numbers in the entity field" in prompt
