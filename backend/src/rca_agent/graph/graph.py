"""The investigation graph: plan -> schema -> sql -> guardrail
-> (execute | self-correct -> guardrail) -> analyze -> conclude.

A refused query routes to self-correction, which asks the model to
fix the SQL given the exact refusal and loops back to the guardrail.
There are no dead ends: when the corrections are spent, the query
cap is hit, or a correction comes back unchanged, the investigation
goes to analyze with whatever evidence it collected — and if it
collected none, analyze abstains with an explicit
insufficient_evidence verdict instead of guessing. A failed node
(bad JSON from the model, a warehouse error) still ends the run with
status "failed" and the error in the state; LangGraph surfaces the
exception to the caller.
"""

from __future__ import annotations

from typing import Iterator, cast

from langgraph.graph import END, START, StateGraph

from ..llm.base import LLM
from ..services.recall import Recall
from ..services.traces import TraceStore
from ..sql.guardrails import Guardrails
from ..sql.warehouse import Warehouse
from .nodes import MAX_CORRECT_ATTEMPTS, Nodes
from .state import InvestigationState, initial_state


def build_graph(
    llm: LLM,
    warehouse: Warehouse,
    guardrails: Guardrails,
    trace_store: TraceStore | None = None,
    recall: Recall | None = None,
) -> StateGraph[InvestigationState]:
    nodes = Nodes(llm, warehouse, guardrails, trace_store, recall)
    graph: StateGraph[InvestigationState] = StateGraph(InvestigationState)
    graph.add_node("plan", nodes.plan)
    graph.add_node("schema", nodes.schema)
    graph.add_node("sql", nodes.sql)
    graph.add_node("guardrail", nodes.guardrail)
    graph.add_node("execute", nodes.execute)
    graph.add_node("self_correct", nodes.self_correct)
    graph.add_node("analyze", nodes.analyze)
    graph.add_node("conclude", nodes.conclude)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "schema")
    graph.add_edge("schema", "sql")
    graph.add_edge("sql", "guardrail")
    graph.add_conditional_edges(
        "guardrail",
        _route_after_guardrail,
        {"execute": "execute", "self_correct": "self_correct", "analyze": "analyze"},
    )
    graph.add_conditional_edges(
        "self_correct",
        _route_after_self_correct,
        {"guardrail": "guardrail", "analyze": "analyze"},
    )
    graph.add_conditional_edges(
        "execute",
        _route_after_execute,
        {"sql": "sql", "analyze": "analyze", "self_correct": "self_correct"},
    )
    graph.add_conditional_edges(
        "analyze",
        _route_after_analyze,
        {"sql": "sql", "conclude": "conclude"},
    )
    graph.add_edge("conclude", END)
    return graph


def _route_after_guardrail(state: InvestigationState) -> str:
    verdict = state.get("last_verdict") or {}
    if verdict.get("allowed"):
        return "execute"
    # A spent budget is not fixable by rewriting the SQL, and
    # neither is a third refused correction: analyze with the
    # evidence collected so far instead of failing the run.
    if verdict.get("rule") in ("query-cap", "daily-byte-cap"):
        return "analyze"
    if state.get("attempts", 0) < MAX_CORRECT_ATTEMPTS:
        return "self_correct"
    return "analyze"


def _route_after_self_correct(state: InvestigationState) -> str:
    # The correction came back identical to the refused SQL: the
    # model has nothing new to try, so guarding it again would
    # only burn a query-cap attempt. Analyze instead.
    if state.get("correction_unchanged"):
        return "analyze"
    return "guardrail"


def _route_after_execute(state: InvestigationState) -> str:
    if state.get("execution_error"):
        if state.get("attempts", 0) < MAX_CORRECT_ATTEMPTS:
            return "self_correct"
        return "analyze"
    steps = state.get("steps", [])
    if state.get("step_index", 0) < min(len(steps), state.get("max_steps", 4)):
        return "sql"
    return "analyze"


def _route_after_analyze(state: InvestigationState) -> str:
    analysis = state.get("analysis") or {}
    # The follow-up is not bounded by the step cap: it is one
    # refinement query, not a new plan step, and a 4-step plan
    # would otherwise leave no room for it — the first live run's
    # investigations all ran 4 queries, so a confound follow-up
    # could never have run. The bound is the follow_up_done flag
    # (one follow-up per investigation) plus the guardrail's
    # per-investigation query cap.
    if analysis.get("follow_up") and not state.get("follow_up_done"):
        return "sql"
    return "conclude"


def run_investigation_stream(
    graph: StateGraph[InvestigationState],
    question: str,
    investigation_id: str | None = None,
    max_steps: int = 4,
) -> Iterator[InvestigationState]:
    """Yield the full state after each graph node, so a caller (the
    API) can persist progress as the investigation runs instead of
    only at the end."""
    compiled = graph.compile()
    for state in compiled.stream(
        initial_state(question, investigation_id, max_steps),
        config={"recursion_limit": 50},
        stream_mode="values",
    ):
        yield cast(InvestigationState, state)


def run_investigation(
    graph: StateGraph[InvestigationState],
    question: str,
    investigation_id: str | None = None,
    max_steps: int = 4,
) -> InvestigationState:
    """Run one investigation to completion. Returns the final state
    (answer, evidence, queries, status). Raises on a node failure;
    the caller records status "failed" with the error."""
    compiled = graph.compile()
    result = compiled.invoke(
        initial_state(question, investigation_id, max_steps),
        config={"recursion_limit": 50},
    )
    return cast(InvestigationState, result)
