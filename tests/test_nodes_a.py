"""Unit tests for workflow nodes Part 1: intake, extract, fetch_facts (T8)."""

from unittest.mock import MagicMock

from triage.config import TriageConfig
from triage.nodes import NodeDependencies, make_nodes
from triage.tools.base import ToolError
from triage.tools.fixture_backend import FixtureToolsBackend


def test_intake_happy_path():
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(tools=tools, config=cfg)
    nodes = make_nodes(deps)

    state = {"claim_id": "CLM70000001"}
    out = nodes["intake"](state)

    assert "raw_text" in out
    assert "clean_text" in out
    assert "injection_flags" in out
    assert out["injection_flags"] == []
    assert len(out["trail"]) == 1
    assert out["trail"][0]["node"] == "intake"
    assert out["trail"][0]["outcome"] == "ok"


def test_intake_injection_flagged():
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(tools=tools, config=cfg)
    nodes = make_nodes(deps)

    state = {"claim_id": "CLM70000034"}  # hostile prompt injection case
    out = nodes["intake"](state)

    assert len(out["injection_flags"]) > 0
    assert "PATTERN" in out["injection_flags"]
    assert "PATTERN" in out["trail"][0]["detail_codes"]


def test_extract_happy_path_oracle():
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(tools=tools, config=cfg)
    nodes = make_nodes(deps)

    state = {
        "claim_id": "CLM70000001",
        "clean_text": "Claim form for CLM70000001, member M700001.",
    }
    out = nodes["extract"](state)

    assert out["extraction_failed"] is False
    assert out["extraction"] is not None
    assert out["extraction"]["claim_id"] == "CLM70000001"
    assert len(out["trail"]) == 1
    assert out["trail"][0]["outcome"] == "ok"


def test_extract_retries_once_then_fails_closed():
    # Simulate LLM returning invalid json twice
    mock_client = MagicMock()
    invalid_resp = MagicMock()
    invalid_resp.text = '{"invalid_field": true}'  # missing required fields
    invalid_resp.usage_metadata = MagicMock(
        prompt_token_count=10, candidates_token_count=10
    )
    mock_client.models.generate_content.return_value = invalid_resp

    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="live", GEMINI_API_KEY="key")
    deps = NodeDependencies(tools=tools, config=cfg, fake_client=mock_client)
    nodes = make_nodes(deps)

    state = {"claim_id": "CLM70000001", "clean_text": "Sample text"}
    out = nodes["extract"](state)

    assert out["extraction_failed"] is True
    assert out["extraction"] is None
    assert out["trail"][0]["outcome"] == "failed"
    # Verify retry occurred (2 calls total)
    assert mock_client.models.generate_content.call_count == 2


def test_fetch_facts_happy_path():
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(tools=tools, config=cfg)
    nodes = make_nodes(deps)

    state = {
        "claim_id": "CLM70000001",
        "extraction": {
            "claim_id": "CLM70000001",
            "member_id": "M700001",
            "provider_id": "PRV7001",
            "procedure_code": "PX0001",
            "service_date": "2026-05-10",
            "billed_amount": 150.0,
            "documents_present": ["claim_form", "itemized_bill"],
        },
        "injection_flags": [],
    }
    out = nodes["fetch_facts"](state)

    assert out["claim_missing"] is False
    assert out["tool_error"] is None
    assert "facts" in out
    assert out["facts"]["claim"]["claim_id"] == "CLM70000001"
    assert out["facts"]["coverage_rule"]["covered"] is True
    assert out["facts"]["remaining_annual_limit"] >= 0.0


def test_fetch_facts_claim_missing_r01():
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(tools=tools, config=cfg)
    nodes = make_nodes(deps)

    state = {
        "claim_id": "CLM70000043",  # case-43: not in DB
        "extraction": {"claim_id": "CLM70000043"},
        "injection_flags": [],
    }
    out = nodes["fetch_facts"](state)

    assert out["claim_missing"] is True
    assert out["tool_error"] is None
    assert out["trail"][0]["outcome"] == "ok"
    assert "claim_missing" in out["trail"][0]["detail_codes"]


def test_fetch_facts_tool_rate_limited_retries():
    slept = []
    mock_tools = MagicMock()
    mock_tools.get_claim.side_effect = [
        ToolError("RATE_LIMITED", "Backoff"),
        ToolError("RATE_LIMITED", "Backoff"),
        {
            "claim_id": "CLM70000001",
            "member_id": "M700001",
            "plan_id": "PLN001",
            "provider_id": "PRV7001",
            "provider_network_status": "IN_NETWORK",
            "procedure_code": "PX0001",
            "service_date": "2026-05-10",
            "status": "SUBMITTED",
            "billed_amount": 150.0,
        },
    ]
    mock_tools.get_coverage_rule.return_value = {
        "covered": True,
        "requires_preauth": False,
        "max_allowed": None,
    }
    mock_tools.get_member_ytd_summary.return_value = {"remaining_annual_limit": 50000.0}
    mock_tools.list_member_claims.return_value = {"items": []}
    mock_tools.get_claim_notes.return_value = {"notes": []}

    cfg = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(tools=mock_tools, config=cfg, sleep_fn=slept.append)
    nodes = make_nodes(deps)

    state = {"claim_id": "CLM70000001", "extraction": {}, "injection_flags": []}
    out = nodes["fetch_facts"](state)

    assert out["claim_missing"] is False
    assert out["tool_error"] is None
    assert slept == [1.0, 2.0]


def test_fetch_facts_internal_tool_error_r18():
    mock_tools = MagicMock()
    mock_tools.get_claim.side_effect = ToolError("INTERNAL", "Database connection lost")

    cfg = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(tools=mock_tools, config=cfg)
    nodes = make_nodes(deps)

    state = {"claim_id": "CLM70000001", "extraction": {}, "injection_flags": []}
    out = nodes["fetch_facts"](state)

    assert out["tool_error"] == "INTERNAL"
    assert out["trail"][0]["outcome"] == "error"
    assert "INTERNAL" in out["trail"][0]["detail_codes"]


def test_fetch_facts_notes_with_injection_sets_flag():
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle")
    deps = NodeDependencies(tools=tools, config=cfg)
    nodes = make_nodes(deps)

    state = {
        "claim_id": "CLM70000037",  # case-37: injection in note
        "extraction": {"claim_id": "CLM70000037"},
        "injection_flags": [],
    }
    out = nodes["fetch_facts"](state)

    assert "PATTERN" in out["injection_flags"]
    assert "NOTE_INJECTION" in out["trail"][0]["detail_codes"]
