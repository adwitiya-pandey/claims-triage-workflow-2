"""LangGraph state schema and reducers for claims triage."""

import operator
from typing import Annotated, Any, TypedDict


class TriageState(TypedDict, total=False):
    """Workflow state holding only plain JSON-serialisable types."""

    claim_id: str
    version: int
    raw_text: str
    clean_text: str
    injection_flags: list[str]
    extraction: dict[str, Any] | None
    extraction_failed: bool
    facts: dict[str, Any]
    tool_error: str | None
    claim_missing: bool
    chunks: list[dict[str, Any]]
    assessment: dict[str, Any] | None
    route: str | None
    rule_hits: list[dict[str, Any]]
    review: dict[str, Any] | None
    decision: dict[str, Any] | None
    trail: Annotated[list[dict[str, Any]], operator.add]
