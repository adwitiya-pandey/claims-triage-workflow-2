"""LangGraph workflow compilation, topology, and telemetry logging."""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import RunnableConfig

from triage.state import TriageState

telemetry_logger = logging.getLogger("triage.workflow")

# Gemini 2.5/3.8 Flash token pricing estimates in USD
GEMINI_INPUT_COST_PER_TOKEN = 0.075 / 1_000_000
GEMINI_OUTPUT_COST_PER_TOKEN = 0.30 / 1_000_000


def _route_after_decide(state: TriageState) -> str:
    """Conditional router routing FAST_TRACK to finalize and all others to review."""
    if state.get("route") == "FAST_TRACK":
        return "finalize"
    return "review"


def _extract_token_usage(result: Any) -> dict[str, int]:
    """Extract token usage dictionary from node execution result."""
    if isinstance(result, dict):
        trail = result.get("trail")
        if isinstance(trail, list) and trail:
            last = trail[-1]
            if isinstance(last, dict) and "usage" in last:
                u = last["usage"]
                return {
                    "input_tokens": int(u.get("input_tokens", 0)),
                    "output_tokens": int(u.get("output_tokens", 0)),
                }
    return {"input_tokens": 0, "output_tokens": 0}


def _wrap_node_with_telemetry(
    node_name: str, fn: Callable[..., Any]
) -> Callable[..., Any]:
    """Wrap a workflow node to emit structured JSON telemetry on completion."""

    def wrapped_node(state: TriageState, config: RunnableConfig | None = None) -> Any:
        claim_id = state.get("claim_id", "")
        thread_id = ""
        if isinstance(config, dict):
            thread_id = config.get("configurable", {}).get("thread_id", "")
        if not thread_id:
            thread_id = state.get("thread_id", "")
        run_id = thread_id or claim_id or "run_default"
        t0 = time.perf_counter()

        # Execute node function. If LangGraph interrupt() is raised, allow it
        # to bubble up directly without side effects per AGENTS Gotcha 2.
        try:
            import inspect

            sig = inspect.signature(fn)
            if len(sig.parameters) >= 2:
                result = fn(state, config)
            else:
                result = fn(state)
        except TypeError:
            result = fn(state)

        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        token_usage = _extract_token_usage(result)
        cost_usd = round(
            token_usage["input_tokens"] * GEMINI_INPUT_COST_PER_TOKEN
            + token_usage["output_tokens"] * GEMINI_OUTPUT_COST_PER_TOKEN,
            6,
        )

        merged = dict(state)
        if isinstance(result, dict):
            merged.update(result)

        guardrail_triggered = bool(
            merged.get("injection_flags")
            or merged.get("extraction_failed")
            or merged.get("tool_error")
            or (node_name == "decide" and merged.get("route") != "FAST_TRACK")
        )

        final_route = merged.get("route")

        telemetry_event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "claim_id": claim_id,
            "node_name": node_name,
            "latency_ms": latency_ms,
            "token_usage": token_usage,
            "cost_usd": cost_usd,
            "guardrail_triggered": guardrail_triggered,
            "final_route": final_route,
        }
        telemetry_logger.info(
            json.dumps(telemetry_event),
            extra={
                "run_id": run_id,
                "claim_id": claim_id,
                "node_name": node_name,
                "latency_ms": latency_ms,
                "token_usage": token_usage,
                "cost_usd": cost_usd,
                "guardrail_triggered": guardrail_triggered,
                "final_route": final_route,
            },
        )
        return result

    return wrapped_node


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

    # Register all 8 workflow nodes wrapped with structured telemetry
    for name, fn in nodes.items():
        workflow.add_node(name, _wrap_node_with_telemetry(name, fn))

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
