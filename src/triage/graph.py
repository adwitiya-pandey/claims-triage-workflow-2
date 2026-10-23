"""LangGraph workflow compilation and topology."""

from typing import Any


def build_graph(nodes: dict[str, Any], checkpointer: Any = None) -> Any:
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
    raise NotImplementedError("build_graph is implemented in T11")
