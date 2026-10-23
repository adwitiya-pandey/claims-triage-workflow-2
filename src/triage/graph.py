"""LangGraph workflow compilation and topology."""

from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from triage.state import TriageState


def _route_after_decide(state: TriageState) -> str:
    """Conditional router routing FAST_TRACK to finalize and all others to review."""
    if state.get("route") == "FAST_TRACK":
        return "finalize"
    return "review"


def build_graph(nodes: dict[str, Any], checkpointer: Any = None) -> CompiledStateGraph:
    """Build and compile the claims triage LangGraph workflow.

    Topology (SPEC 5):
        START -> intake -> extract -> fetch_facts -> retrieve -> assess -> decide
        decide -> [conditional edge]:
            - if route == "FAST_TRACK": finalize -> END
            - otherwise (!= "FAST_TRACK"): review -> finalize -> END

    Conditional edge rule:
        The decision node evaluates deterministic rules over compiled facts and sets
        the initial recommendation (FAST_TRACK, STANDARD_REVIEW, or ESCALATE).
        If and only if the route is FAST_TRACK, the workflow branches directly to
        finalize without human intervention. All non-fast-track claims (STANDARD_REVIEW
        or ESCALATE) branch to the review node for human oversight.

    LangGraph interrupt() rules (AGENTS Gotcha 2):
        1. On resume, the review node restarts from its very first line.
        2. Therefore, the review node must construct the review packet,
           call interrupt(), validate the human response upon resumption,
           and return state updates.
        3. Zero side effects before interrupt() (no DB writes, no logging of actions,
               no external mutations).
        4. No broad try/except around interrupt().
        5. No while loop around interrupt().
        6. Pass only plain JSON-serialisable values into interrupt().
        7. Resume with Command(resume=...) using the same thread_id.
        8. Never use Command(update=...) as input, and do not use response_schema=.
    """
    workflow = StateGraph(TriageState)

    # Register all 8 workflow nodes
    for name, fn in nodes.items():
        workflow.add_node(name, fn)

    # Linear upstream pipeline
    workflow.add_edge(START, "intake")
    workflow.add_edge("intake", "extract")
    workflow.add_edge("extract", "fetch_facts")
    workflow.add_edge("fetch_facts", "retrieve")
    workflow.add_edge("retrieve", "assess")
    workflow.add_edge("assess", "decide")

    # Conditional routing after deterministic decision
    workflow.add_conditional_edges(
        "decide",
        _route_after_decide,
        {
            "finalize": "finalize",
            "review": "review",
        },
    )

    # Review flows into finalize, and finalize terminates the graph
    workflow.add_edge("review", "finalize")
    workflow.add_edge("finalize", END)

    return workflow.compile(checkpointer=checkpointer)
