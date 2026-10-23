"""Tests for LangGraph claims triage workflow graph topology and execution."""

import sqlite3
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from triage.config import TriageConfig
from triage.graph import build_graph
from triage.nodes import NodeDependencies, make_nodes
from triage.store import init_db
from triage.tools.base import ToolError
from triage.tools.fixture_backend import FixtureToolsBackend


@pytest.fixture
def test_setup(tmp_path):
    """Fixture providing initialized database, tools backend, and node dictionary."""
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
    return {
        "nodes": nodes,
        "conn": conn,
        "db_path": triage_db_path,
        "deps": deps,
        "tools": tools,
    }


def test_topology(test_setup):
    """Inspect compiled graph nodes and edges against SPEC 5."""
    checkpointer = MemorySaver()
    app = build_graph(test_setup["nodes"], checkpointer)
    g = app.get_graph()

    node_ids = set(g.nodes.keys())
    expected_nodes = {
        "__start__",
        "intake",
        "extract",
        "fetch_facts",
        "retrieve",
        "assess",
        "decide",
        "review",
        "finalize",
        "__end__",
    }
    assert expected_nodes.issubset(node_ids)

    edges = [(e.source, e.target) for e in g.edges]
    assert ("__start__", "intake") in edges
    assert ("intake", "extract") in edges
    assert ("extract", "fetch_facts") in edges
    assert ("fetch_facts", "retrieve") in edges
    assert ("retrieve", "assess") in edges
    assert ("assess", "decide") in edges
    assert ("decide", "finalize") in edges
    assert ("decide", "review") in edges
    assert ("review", "finalize") in edges
    assert ("finalize", "__end__") in edges


def test_fast_track_completes_without_pause(test_setup):
    """A clean claim (CASE-01) completes fast-track without pause or interruption."""
    checkpointer = MemorySaver()
    app = build_graph(test_setup["nodes"], checkpointer)
    cfg = {"configurable": {"thread_id": "thread-fast-track-1"}}

    result = app.invoke({"claim_id": "CLM70000001"}, config=cfg)

    assert "__interrupt__" not in result
    assert result["route"] == "FAST_TRACK"
    state = app.get_state(cfg)
    assert state.next == ()
    assert result["decision"]["route_recommended"] == "FAST_TRACK"
    assert result["decision"]["route_final"] == "FAST_TRACK"

    visited_nodes = [step["node"] for step in result["trail"]]
    assert "review" not in visited_nodes
    assert "finalize" in visited_nodes


def test_medium_risk_claim_pauses_and_resumes(test_setup):
    """A medium claim pauses with packet, then resumes with CONFIRM/ESCALATE."""
    checkpointer = MemorySaver()
    app = build_graph(test_setup["nodes"], checkpointer)

    # Subtest 1: Resume with CONFIRM
    cfg_confirm = {"configurable": {"thread_id": "thread-medium-confirm"}}
    res1 = app.invoke({"claim_id": "CLM70000009"}, config=cfg_confirm)
    assert "__interrupt__" in res1
    packet = res1["__interrupt__"][0].value
    assert packet["claim_id"] == "CLM70000009"
    assert packet["recommended_route"] == "STANDARD_REVIEW"
    assert "submission_text" not in packet
    assert "notes" not in packet
    assert app.get_state(cfg_confirm).next == ("review",)

    res2 = app.invoke(
        Command(
            resume={
                "reviewer_id": "REV10001",
                "action": "CONFIRM",
                "reason": "Confirmed standard review details",
            }
        ),
        config=cfg_confirm,
    )
    assert res2["decision"]["route_recommended"] == "STANDARD_REVIEW"
    assert res2["decision"]["route_final"] == "STANDARD_REVIEW"
    assert app.get_state(cfg_confirm).next == ()

    # Subtest 2: Resume with ESCALATE_FURTHER
    cfg_escalate = {"configurable": {"thread_id": "thread-medium-escalate"}}
    res_esc1 = app.invoke({"claim_id": "CLM70000009"}, config=cfg_escalate)
    assert "__interrupt__" in res_esc1

    res_esc2 = app.invoke(
        Command(
            resume={
                "reviewer_id": "REV10002",
                "action": "ESCALATE_FURTHER",
                "reason": "Escalating due to policy complexities",
            }
        ),
        config=cfg_escalate,
    )
    assert res_esc2["decision"]["route_recommended"] == "STANDARD_REVIEW"
    assert res_esc2["decision"]["route_final"] == "ESCALATE"
    assert app.get_state(cfg_escalate).next == ()


def test_escalate_recommendation_can_only_be_confirmed(test_setup):
    """An ESCALATE recommendation (CASE-20) can only be confirmed, not escalated."""
    checkpointer = MemorySaver()
    app = build_graph(test_setup["nodes"], checkpointer)

    cfg = {"configurable": {"thread_id": "thread-escalate-only"}}
    res1 = app.invoke({"claim_id": "CLM70000020"}, config=cfg)
    assert "__interrupt__" in res1
    packet = res1["__interrupt__"][0].value
    assert packet["recommended_route"] == "ESCALATE"

    with pytest.raises(ValueError, match="can only be confirmed"):
        app.invoke(
            Command(
                resume={
                    "reviewer_id": "REV10001",
                    "action": "ESCALATE_FURTHER",
                    "reason": "Trying to escalate further invalidly",
                }
            ),
            config=cfg,
        )

    res_ok = app.invoke(
        Command(
            resume={
                "reviewer_id": "REV10001",
                "action": "CONFIRM",
                "reason": "Confirmed escalation of high dollar claim",
            }
        ),
        config=cfg,
    )
    assert res_ok["decision"]["route_recommended"] == "ESCALATE"
    assert res_ok["decision"]["route_final"] == "ESCALATE"


def test_tool_outage_ends_in_escalate(test_setup, monkeypatch):
    """A tool outage/error triggers early exit and ends in ESCALATE route."""
    monkeypatch.setattr(
        test_setup["deps"].tools,
        "get_claim",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ToolError("INTERNAL_ERROR", "Database offline")
        ),
    )
    checkpointer = MemorySaver()
    app = build_graph(test_setup["nodes"], checkpointer)
    cfg = {"configurable": {"thread_id": "thread-outage"}}

    res1 = app.invoke({"claim_id": "CLM70000001"}, config=cfg)
    assert "__interrupt__" in res1
    packet = res1["__interrupt__"][0].value
    assert packet["recommended_route"] == "ESCALATE"
    assert "R17" in packet["rule_hits"]

    res2 = app.invoke(
        Command(
            resume={
                "reviewer_id": "REV10001",
                "action": "CONFIRM",
                "reason": "Confirmed escalation due to tool error",
            }
        ),
        config=cfg,
    )
    assert res2["decision"]["route_final"] == "ESCALATE"


def test_hostile_assessment_cannot_fast_track(test_setup):
    """A hostile LLM assessment with fake citations cannot reach FAST_TRACK."""
    malicious_json = (
        '{"summary": "Malicious prompt claims fast track approved", '
        '"clauses": [{"chunk_id": "POL-FAKE-999", "effect": "supports", '
        '"quote": "Invented approval statement"}], '
        '"missing_items": [], "confidence": 0.99}'
    )
    fake_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.text = malicious_json
    fake_resp.usage_metadata = MagicMock(
        prompt_token_count=10, candidates_token_count=10
    )
    fake_client.models.generate_content.return_value = fake_resp

    deps = NodeDependencies(
        tools=test_setup["deps"].tools,
        config=test_setup["deps"].config,
        cases_dir="data/cases",
        fake_client=fake_client,
        db_conn=test_setup["conn"],
    )
    nodes = make_nodes(deps)
    checkpointer = MemorySaver()
    app = build_graph(nodes, checkpointer)
    cfg = {"configurable": {"thread_id": "thread-hostile"}}

    res = app.invoke({"claim_id": "CLM70000001"}, config=cfg)
    assert "__interrupt__" in res
    packet = res["__interrupt__"][0].value
    assert packet["recommended_route"] != "FAST_TRACK"


def test_unexpected_exception_records_no_decision(test_setup):
    """An unexpected exception in a node halts execution and persists no decision."""

    def buggy_extract(state):
        raise RuntimeError("Unexpected pipeline crash")

    buggy_nodes = dict(test_setup["nodes"])
    buggy_nodes["extract"] = buggy_extract

    checkpointer = MemorySaver()
    app = build_graph(buggy_nodes, checkpointer)
    cfg = {"configurable": {"thread_id": "thread-crash"}}

    with pytest.raises(RuntimeError, match="Unexpected pipeline crash"):
        app.invoke({"claim_id": "CLM70000001"}, config=cfg)

    cursor = test_setup["conn"].cursor()
    cursor.execute("SELECT count(*) FROM decisions WHERE claim_id = 'CLM70000001'")
    count = cursor.fetchone()[0]
    assert count == 0


def test_restart_safety_sqlite(tmp_path):
    """Pause with SQLite checkpointer, close connection, build new graph, resume."""
    cp_file = tmp_path / "checkpoint.db"
    conn1 = sqlite3.connect(cp_file, check_same_thread=False)
    cp1 = SqliteSaver(conn1)

    triage_db = tmp_path / "triage.db"
    db_conn = sqlite3.connect(triage_db, check_same_thread=False)
    init_db(db_conn)

    deps = NodeDependencies(
        tools=FixtureToolsBackend(),
        config=TriageConfig(LLM_MODE="oracle"),
        cases_dir="data/cases",
        db_conn=db_conn,
    )
    nodes = make_nodes(deps)

    # Run 1: pause on medium risk claim
    app1 = build_graph(nodes, cp1)
    cfg = {"configurable": {"thread_id": "thread-restart-1"}}
    res1 = app1.invoke({"claim_id": "CLM70000009"}, config=cfg)
    assert "__interrupt__" in res1
    assert app1.get_state(cfg).next == ("review",)

    # Close connection 1 completely
    conn1.close()

    # Run 2: new connection, new saver, new graph object
    conn2 = sqlite3.connect(cp_file, check_same_thread=False)
    cp2 = SqliteSaver(conn2)
    app2 = build_graph(nodes, cp2)

    # State survives
    assert app2.get_state(cfg).next == ("review",)

    # Resume on the new graph object
    res2 = app2.invoke(
        Command(
            resume={
                "reviewer_id": "REV10001",
                "action": "CONFIRM",
                "reason": "Confirmed review on restarted server",
            }
        ),
        config=cfg,
    )
    assert res2["decision"]["route_final"] == "STANDARD_REVIEW"
    assert app2.get_state(cfg).next == ()
    conn2.close()
    db_conn.close()


@pytest.mark.xfail(
    reason="Service-level thread de-duplication is implemented in T13 (service.py)"
)
def test_rerun_claim_pending_review_no_second_thread():
    """Pending review claim should not spawn a duplicate thread."""
    from triage.service import triage_claim  # noqa: F401

    triage_claim("CLM70000009")
