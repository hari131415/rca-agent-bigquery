"""The investigation graph: plan -> schema -> sql -> guardrail
-> (execute | self-correct -> guardrail) -> analyze -> conclude.

A refused query routes to self-correction, which asks the model to
fix the SQL given the refusal reason and loops back to the
guardrail. After MAX_CORRECT_ATTEMPTS refusals the investigation
fails with the last verdict recorded — it never loops forever, and
a refused attempt still costs the agent a try (the query cap in
Phase 3 counts it). A failed node (bad JSON from the model, a
warehouse error) ends the run with status "failed" and the error
in the state; LangGraph surfaces the exception to the caller.
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
    graph.add_node("fail", nodes.fail)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "schema")
    graph.add_edge("schema", "sql")
    graph.add_edge("sql", "guardrail")
    graph.add_conditional_edges(
        "guardrail",
        _route_after_guardrail,
        {"execute": "execute", "self_correct": "self_correct", "fail": "fail"},
    )
    graph.add_edge("self_correct", "guardrail")
    graph.add_conditional_edges(
        "execute",
        _route_after_execute,
        {"sql": "sql", "analyze": "analyze", "self_correct": "self_correct", "fail": "fail"},
    )
    graph.add_conditional_edges(
        "analyze",
        _route_after_analyze,
        {"sql": "sql", "conclude": "conclude"},
    )
    graph.add_edge("conclude", END)
    graph.add_edge("fail", END)
    return graph


def _route_after_guardrail(state: InvestigationState) -> str:
    verdict = state.get("last_verdict") or {}
    if verdict.get("allowed"):
        return "execute"
    # A spent budget is not fixable by rewriting the SQL: the
    # investigation fails instead of burning corrections on it.
    if verdict.get("rule") in ("query-cap", "daily-byte-cap"):
        return "fail"
    if state.get("attempts", 0) < MAX_CORRECT_ATTEMPTS:
        return "self_correct"
    return "fail"


def _route_after_execute(state: InvestigationState) -> str:
    if state.get("execution_error"):
        if state.get("attempts", 0) < MAX_CORRECT_ATTEMPTS:
            return "self_correct"
        return "fail"
    steps = state.get("steps", [])
    if state.get("step_index", 0) < min(len(steps), state.get("max_steps", 4)):
        return "sql"
    return "analyze"


def _route_after_analyze(state: InvestigationState) -> str:
    analysis = state.get("analysis") or {}
    if (
        analysis.get("follow_up")
        and not state.get("follow_up_done")
        and len(state.get("queries", [])) < state.get("max_steps", 4)
    ):
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
