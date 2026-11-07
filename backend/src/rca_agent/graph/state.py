"""Investigation state: what the graph carries between steps.

`queries` and `evidence` append (LangGraph's `add` reducer), so a
node returns one new entry and the history is preserved. Everything
else is overwritten by the node that owns it. The state is the
investigation's memory: it is what the API polls (Phase 5) and
what Firestore persists (Phase 5).
"""

from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict


class InvestigationState(TypedDict, total=False):
    question: str
    investigation_id: str | None
    plan: dict[str, Any]
    hypotheses: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    step_index: int
    max_steps: int
    follow_up_done: bool
    execution_error: bool
    schema_doc: str
    current_sql: str | None
    last_verdict: dict[str, Any]
    queries: Annotated[list[dict[str, Any]], add]
    evidence: Annotated[list[dict[str, Any]], add]
    analysis: dict[str, Any] | None
    answer: str | None
    warnings: list[str]
    status: str  # running | done | failed
    error: str | None
    attempts: int  # self-correct attempts used
    unverified_claims: int  # claims whose numbers match no evidence row
    verdict: dict[str, Any] | None  # the machine-readable verdict (conclude)


def initial_state(
    question: str, investigation_id: str | None = None, max_steps: int = 4
) -> InvestigationState:
    return InvestigationState(
        question=question,
        investigation_id=investigation_id,
        plan={},
        hypotheses=[],
        steps=[],
        step_index=0,
        max_steps=max_steps,
        follow_up_done=False,
        execution_error=False,
        schema_doc="",
        current_sql=None,
        last_verdict={},
        queries=[],
        evidence=[],
        analysis=None,
        answer=None,
        warnings=[],
        status="running",
        error=None,
        attempts=0,
        unverified_claims=0,
        verdict=None,
    )
