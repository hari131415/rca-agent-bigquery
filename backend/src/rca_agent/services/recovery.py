"""Startup recovery: investigations left "running" by a previous
process (a crash, a restart, a deploy) are marked "failed" with
the reason recorded, so nothing stays stuck on "running" and a
poll can return a final answer. Finished investigations are
untouched.
"""

from __future__ import annotations

from typing import Any

from .store import Store

INTERRUPTED_REASON = "interrupted by server restart"


def recover_interrupted(store: Store) -> list[str]:
    """Mark every running investigation failed. Returns the ids recovered."""
    recovered: list[str] = []
    for state in store.list_investigations():
        if state.get("status") != "running":
            continue
        state["status"] = "failed"
        state["error"] = INTERRUPTED_REASON
        store.save_investigation(state)
        recovered.append(str(state["investigation_id"]))
    return recovered


def investigation_to_dict(state: dict[str, Any]) -> dict[str, Any]:
    """The public shape of an investigation (what the API returns)."""
    return {
        "investigation_id": state.get("investigation_id"),
        "question": state.get("question"),
        "status": state.get("status"),
        "answer": state.get("answer"),
        "evidence": state.get("evidence", []),
        "queries": state.get("queries", []),
        "warnings": state.get("warnings", []),
        "unverified_claims": state.get("unverified_claims", 0),
        "verdict": state.get("verdict"),
        "verdict_unverified": state.get("verdict_unverified", False),
        "error": state.get("error"),
        "created_at": state.get("created_at"),
        "updated_at": state.get("updated_at"),
    }
