"""Tests for structured JSON workflow logging and cost telemetry."""

import json
import logging
import sqlite3

import pytest
from langgraph.checkpoint.memory import MemorySaver

from triage.config import TriageConfig
from triage.graph import build_graph
from triage.logging_setup import FORBIDDEN_KEYS
from triage.nodes import NodeDependencies, make_nodes
from triage.store import init_db
from triage.tools.fixture_backend import FixtureToolsBackend


@pytest.fixture
def telemetry_setup(tmp_path):
    """Setup graph with captured logger for telemetry validation."""
    triage_db_path = tmp_path / "triage.db"
    conn = sqlite3.connect(triage_db_path)
    init_db(conn)

    tools = FixtureToolsBackend()
    config = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(
        tools=tools,
        config=config,
        cases_dir="data/cases",
        db_conn=conn,
    )
    nodes = make_nodes(deps)
    checkpointer = MemorySaver()
    app = build_graph(nodes, checkpointer)
    return {
        "app": app,
        "deps": deps,
    }


def test_workflow_node_telemetry_emission(telemetry_setup, caplog):
    """Assert structured JSON logs and cost telemetry are emitted per transition."""
    caplog.set_level(logging.INFO, logger="triage.workflow")
    app = telemetry_setup["app"]

    initial_state = {
        "claim_id": "CLM70000001",
        "raw_text": "Claim CLM70000001 for 99213 routine consult, amount $150.00",
        "thread_id": "thread-test-telemetry",
    }
    app.invoke(
        initial_state,
        config={"configurable": {"thread_id": "thread-test-telemetry"}},
    )

    records = [r for r in caplog.records if r.name == "triage.workflow"]
    assert len(records) >= 7, (
        f"Expected at least 7 node telemetry records, got {len(records)}"
    )

    node_names_seen = []
    for record in records:
        data = json.loads(record.getMessage())
        node_name = data.get("node_name")
        node_names_seen.append(node_name)

        # 1. Assert all required telemetry fields exist
        assert "timestamp" in data
        assert data["run_id"] == "thread-test-telemetry"
        assert data["claim_id"] == "CLM70000001"
        assert node_name in (
            "intake",
            "extract",
            "fetch_facts",
            "retrieve",
            "assess",
            "decide",
            "finalize",
        )
        assert isinstance(data["latency_ms"], (int, float))
        assert data["latency_ms"] >= 0.0

        # 2. Token usage & cost telemetry
        token_usage = data.get("token_usage", {})
        assert "input_tokens" in token_usage
        assert "output_tokens" in token_usage
        assert isinstance(data["cost_usd"], (int, float))
        assert data["cost_usd"] >= 0.0

        # 3. Guardrail and route
        assert isinstance(data["guardrail_triggered"], bool)
        if node_name == "finalize":
            assert data["final_route"] == "FAST_TRACK"

        # 4. Strict security compliance: zero forbidden keys
        for key in FORBIDDEN_KEYS:
            assert key not in data, f"Forbidden key {key} leaked into telemetry JSON"

    assert "intake" in node_names_seen
    assert "extract" in node_names_seen
    assert "fetch_facts" in node_names_seen
    assert "retrieve" in node_names_seen
    assert "assess" in node_names_seen
    assert "decide" in node_names_seen
    assert "finalize" in node_names_seen


def test_guardrail_triggered_telemetry(telemetry_setup, caplog):
    """Assert guardrail_triggered is True on high-cost or injection claims."""
    caplog.set_level(logging.INFO, logger="triage.workflow")
    app = telemetry_setup["app"]

    # CLM70000034 is an adversarial injection case
    initial_state = {
        "claim_id": "CLM70000034",
        "thread_id": "thread-test-injection",
    }
    try:
        app.invoke(
            initial_state,
            config={"configurable": {"thread_id": "thread-test-injection"}},
        )
    except Exception:
        # Expected interrupt pause at review node
        pass

    records = [r for r in caplog.records if r.name == "triage.workflow"]
    decide_records = [
        json.loads(r.getMessage())
        for r in records
        if json.loads(r.getMessage()).get("node_name") == "decide"
    ]
    assert decide_records, "No decide node telemetry recorded"
    decide_data = decide_records[0]

    assert decide_data["guardrail_triggered"] is True
    assert decide_data["final_route"] in ("ESCALATE", "STANDARD_REVIEW")
