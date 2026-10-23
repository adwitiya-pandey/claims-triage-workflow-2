"""Triage service orchestrator and idempotency manager per SPEC 13."""

import sqlite3
import threading
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from triage.config import TriageConfig
from triage.graph import build_graph
from triage.models import ReviewAction
from triage.nodes import NodeDependencies, make_nodes
from triage.store import (
    create_review,
    get_all_decisions,
    get_latest_decision,
    get_latest_version,
    get_pending_review_by_claim,
    get_review,
    init_db,
    list_pending_reviews,
    resolve_review,
)
from triage.tools.base import ClaimsTools
from triage.tools.fixture_backend import FixtureToolsBackend
from triage.tools.mcp_backend import McpToolsBackend

_GLOBAL_SERVICE: Any = None
_GLOBAL_LOCK = threading.Lock()


class TriageService:
    """Thread-safe orchestration service for claim triage lifecycle."""

    def __init__(
        self,
        config: TriageConfig | None = None,
        tools: ClaimsTools | None = None,
        triage_conn: sqlite3.Connection | None = None,
        checkpoint_conn: sqlite3.Connection | None = None,
        cases_dir: str | Path = "data/cases",
        fake_client: Any | None = None,
    ) -> None:
        self.config = config or TriageConfig.load()
        self.triage_conn = triage_conn or init_db(self.config.TRIAGE_DB_PATH)

        if checkpoint_conn is not None:
            self.checkpoint_conn = checkpoint_conn
        else:
            p = Path(self.config.CHECKPOINT_DB_PATH)
            p.parent.mkdir(parents=True, exist_ok=True)
            self.checkpoint_conn = sqlite3.connect(str(p), check_same_thread=False)

        self.checkpointer = SqliteSaver(self.checkpoint_conn)
        self.checkpointer.setup()

        # Injected tools or configure from config
        if tools is not None:
            self.tools = tools
        elif self.config.CLAIMS_TOOLS_BACKEND == "mcp":
            self.tools = McpToolsBackend(
                url=self.config.CLAIMS_MCP_URL,
                key=self.config.CLAIMS_MCP_KEY,
                stdio_cmd=self.config.CLAIMS_MCP_STDIO_CMD,
            )
        else:
            self.tools = FixtureToolsBackend(cases_dir=cases_dir)

        self.cases_dir = cases_dir
        self.fake_client = fake_client
        self._lock = threading.Lock()

        # Construct nodes and compiled graph
        self.deps = NodeDependencies(
            tools=self.tools,
            config=self.config,
            cases_dir=self.cases_dir,
            fake_client=self.fake_client,
            db_conn=self.triage_conn,
        )
        self.nodes = make_nodes(self.deps)
        self.graph = build_graph(self.nodes, checkpointer=self.checkpointer)

    def start_triage(self, claim_id: str, reprocess: bool = False) -> dict[str, Any]:
        """Start or retrieve triage workflow idempotently (SPEC 13)."""
        with self._lock:
            # 1. Check if an awaiting review already exists for this claim
            pending = get_pending_review_by_claim(self.triage_conn, claim_id)
            if pending is not None:
                thread_id = pending["thread_id"]
                cfg = {"configurable": {"thread_id": thread_id}}
                state = self.graph.get_state(cfg)
                packet = None
                if state.tasks and state.tasks[0].interrupts:
                    packet = state.tasks[0].interrupts[0].value
                return {
                    "status": "awaiting_review",
                    "thread_id": thread_id,
                    "version": pending["version"],
                    "review_packet": packet,
                }

            # 2. Check if a decision already exists and reprocess is False
            if not reprocess:
                latest_decision = get_latest_decision(self.triage_conn, claim_id)
                if latest_decision is not None:
                    return {
                        "status": "completed",
                        "decision": latest_decision,
                    }

            # 3. Create version n + 1 and run the graph
            version = get_latest_version(self.triage_conn, claim_id) + 1
            thread_id = f"triage-{claim_id}-v{version}"
            cfg = {"configurable": {"thread_id": thread_id}}

            result = self.graph.invoke(
                {"claim_id": claim_id, "version": version},
                config=cfg,
            )

            # If execution paused at review node
            if "__interrupt__" in result and result["__interrupt__"]:
                packet = result["__interrupt__"][0].value
                create_review(
                    self.triage_conn,
                    thread_id=thread_id,
                    claim_id=claim_id,
                    version=version,
                )
                return {
                    "status": "awaiting_review",
                    "thread_id": thread_id,
                    "version": version,
                    "review_packet": packet,
                }

            # Graph finalized without pausing (FAST_TRACK)
            return {
                "status": "completed",
                "decision": result.get("decision"),
            }

    def submit_review(self, thread_id: str, action: dict[str, Any]) -> dict[str, Any]:
        """Resume a paused triage workflow with a human review action (SPEC 12)."""
        with self._lock:
            review_row = get_review(self.triage_conn, thread_id)
            if review_row is None:
                raise ValueError(f"Review thread {thread_id} not found")
            if review_row["status"] != "awaiting":
                raise ValueError(f"Review thread {thread_id} is already resolved")

            # Validate review action
            validated_action = ReviewAction.model_validate(action)
            cfg = {"configurable": {"thread_id": thread_id}}

            res = self.graph.invoke(
                Command(resume=validated_action.model_dump(mode="json")),
                config=cfg,
            )

            resolve_review(
                self.triage_conn,
                thread_id=thread_id,
                reviewer_id=validated_action.reviewer_id,
            )

            return {
                "status": "completed",
                "decision": res.get("decision"),
            }

    def get_decisions(self, claim_id: str) -> list[dict[str, Any]]:
        """Retrieve all historical decision records for claim_id."""
        return get_all_decisions(self.triage_conn, claim_id)

    def list_pending(self) -> list[dict[str, Any]]:
        """List all claims currently awaiting human review."""
        return list_pending_reviews(self.triage_conn)


def get_service() -> TriageService:
    """Get or initialize singleton TriageService."""
    global _GLOBAL_SERVICE
    with _GLOBAL_LOCK:
        if _GLOBAL_SERVICE is None:
            _GLOBAL_SERVICE = TriageService()
        return _GLOBAL_SERVICE


def triage_claim(
    claim_id: str, reprocess: bool = False, service: TriageService | None = None
) -> dict[str, Any]:
    """Convenience functional interface to start triage on a claim."""
    svc = service or get_service()
    return svc.start_triage(claim_id=claim_id, reprocess=reprocess)
