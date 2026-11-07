"""Startup recovery: a running investigation is marked failed;
finished ones are untouched."""

from __future__ import annotations

from rca_agent.services.recovery import (
    INTERRUPTED_REASON,
    investigation_to_dict,
    recover_interrupted,
)
from rca_agent.services.store import MemoryStore


def test_recovery_marks_running_failed() -> None:
    store = MemoryStore()
    store.save_investigation({"investigation_id": "a", "status": "running", "updated_at": "1"})
    store.save_investigation({"investigation_id": "b", "status": "done", "updated_at": "2"})
    store.save_investigation(
        {"investigation_id": "c", "status": "failed", "error": "old", "updated_at": "3"}
    )
    assert recover_interrupted(store) == ["a"]
    recovered = store.get_investigation("a")
    assert recovered is not None
    assert recovered["status"] == "failed"
    assert recovered["error"] == INTERRUPTED_REASON
    done = store.get_investigation("b")
    assert done is not None and done["status"] == "done"
    failed = store.get_investigation("c")
    assert failed is not None and failed["error"] == "old"
    assert recover_interrupted(store) == []


def test_investigation_to_dict_shape() -> None:
    state = {
        "investigation_id": "a",
        "question": "Why?",
        "status": "done",
        "answer": "Because.",
        "evidence": [{"sql": "SELECT 1", "rows": []}],
        "queries": [{"sql": "SELECT 1", "rule": "allow", "rows": 0}],
        "warnings": ["a warning"],
        "unverified_claims": 1,
        "error": None,
        "created_at": "2026-11-07T00:00:00+00:00",
        "updated_at": "2026-11-07T00:01:00+00:00",
        "plan": {"steps": []},
    }
    out = investigation_to_dict(state)
    assert out["investigation_id"] == "a"
    assert out["answer"] == "Because."
    assert out["warnings"] == ["a warning"]
    assert out["unverified_claims"] == 1
    assert "plan" not in out
