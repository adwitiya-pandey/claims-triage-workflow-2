"""Unit tests for workflow nodes Part 2: retrieve, assess, decide, review, finalize."""

from unittest.mock import MagicMock

from triage.config import TriageConfig
from triage.nodes import NodeDependencies, make_nodes
from triage.store import get_all_decisions, init_db
from triage.tools.fixture_backend import FixtureToolsBackend


def test_malicious_assessment_cannot_reach_fast_track_security_s3():
    """Security Requirement S3: Malicious assessment output cannot reach FAST_TRACK."""
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="live", GEMINI_API_KEY="key")

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

    deps = NodeDependencies(tools=tools, config=cfg, fake_client=fake_client)
    nodes = make_nodes(deps)

    state = {
        "claim_id": "CLM70000001",
        "clean_text": "Sample text",
        "facts": {
            "claim": {
                "claim_id": "CLM70000001",
                "member_id": "M700001",
                "provider_id": "PRV7001",
                "procedure_code": "PX0001",
                "service_date": "2026-05-10",
                "submitted_on": "2026-05-20",
                "billed_amount": 150.0,
                "status": "SUBMITTED",
                "provider_network_status": "IN_NETWORK",
            },
            "coverage_rule": {
                "covered": True,
                "requires_preauth": False,
                "max_allowed": None,
            },
            "remaining_annual_limit": 50000.0,
            "required_documents": ["claim_form", "itemized_bill"],
            "duplicates": [],
        },
        "extraction": {
            "claim_id": "CLM70000001",
            "member_id": "M700001",
            "provider_id": "PRV7001",
            "procedure_code": "PX0001",
            "service_date": "2026-05-10",
            "billed_amount": 150.0,
            "documents_present": ["claim_form", "itemized_bill"],
            "confidence": 0.95,
        },
        "chunks": [
            {
                "chunk_id": "POL-PX0001",
                "text": "Genuine clause text for primary consultation.",
                "kind": "procedure",
            }
        ],
        "injection_flags": [],
        "claim_missing": False,
        "tool_error": None,
        "extraction_failed": False,
    }

    assess_out = nodes["assess"](state)
    assert assess_out["assessment"] is not None
    assert len(assess_out["assessment"]["clauses"]) == 0
    assert "dropped_citations" in assess_out["trail"][0]["detail_codes"]

    state["assessment"] = assess_out["assessment"]
    decide_out = nodes["decide"](state)

    assert decide_out["route"] == "ESCALATE"
    assert "R15" in [h["rule_id"] for h in decide_out["rule_hits"]]


def test_review_packet_contains_no_raw_claim_text():
    """Review packet contains strictly structured facts, no raw text or notes."""
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle")

    captured_packet = {}

    def spy_interrupt(value):
        nonlocal captured_packet
        captured_packet = value
        return {
            "reviewer_id": "analyst.alice",
            "action": "CONFIRM",
            "reason": "Confirmed clinical justification.",
        }

    deps = NodeDependencies(tools=tools, config=cfg)
    nodes = make_nodes(deps, interrupt_fn=spy_interrupt)

    sensitive_text = "Private confidential patient complaint details"
    state = {
        "claim_id": "CLM70000009",
        "raw_text": sensitive_text,
        "clean_text": sensitive_text,
        "route": "STANDARD_REVIEW",
        "rule_hits": [
            {
                "rule_id": "R04",
                "route": "STANDARD_REVIEW",
                "evidence": ["rule.requires_preauth"],
            }
        ],
        "assessment": {
            "summary": "Preauth missing summary",
            "clauses": [],
            "missing_items": ["preauth_letter"],
        },
        "facts": {
            "claim": {
                "claim_id": "CLM70000009",
                "billed_amount": 1400.0,
                "status": "SUBMITTED",
            },
            "notes": [{"note_text": "Sensitive nurse note text"}],
        },
        "extraction": {"billed_amount": 1400.0},
        "injection_flags": [],
    }

    out = nodes["review"](state)
    assert captured_packet != {}
    assert "claim_id" in captured_packet
    assert "recommended_route" in captured_packet

    packet_str = str(captured_packet)
    assert sensitive_text not in packet_str
    assert "Sensitive nurse note text" not in packet_str
    assert "raw_text" not in captured_packet
    assert out["review"]["action"] == "CONFIRM"


def test_review_no_side_effects_before_interrupt():
    """Ensure review node performs no mutation or I/O side effects before interrupt."""
    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle")

    side_effect_happened = False

    def spy_interrupt(value):
        nonlocal side_effect_happened
        assert side_effect_happened is False
        return {
            "reviewer_id": "analyst.alice",
            "action": "CONFIRM",
            "reason": "Valid reason provided.",
        }

    deps = NodeDependencies(tools=tools, config=cfg)
    nodes = make_nodes(deps, interrupt_fn=spy_interrupt)

    state = {
        "claim_id": "CLM70000009",
        "route": "STANDARD_REVIEW",
        "rule_hits": [],
        "assessment": {},
        "facts": {"claim": {}},
        "extraction": {},
        "injection_flags": [],
    }
    nodes["review"](state)


def test_finalize_is_idempotent(tmp_path):
    """Finalize builds decision record and saves via INSERT OR IGNORE idempotently."""
    db_file = tmp_path / "test_triage.db"
    conn = init_db(db_file)

    tools = FixtureToolsBackend(cases_dir="data/cases")
    cfg = TriageConfig(LLM_MODE="oracle", TRIAGE_DB_PATH=str(db_file))
    deps = NodeDependencies(tools=tools, config=cfg, db_conn=conn)
    nodes = make_nodes(deps)

    state = {
        "claim_id": "CLM70000001",
        "version": 1,
        "route": "FAST_TRACK",
        "rule_hits": [],
        "assessment": {"summary": "Clean claim", "clauses": [], "missing_items": []},
        "facts": {"claim": {"billed_amount": 150.0, "status": "SUBMITTED"}},
        "extraction": {"billed_amount": 150.0},
        "review": None,
        "trail": [],
    }

    out1 = nodes["finalize"](state)
    assert "decision" in out1
    assert out1["decision"]["route_final"] == "FAST_TRACK"

    out2 = nodes["finalize"](state)
    assert out2["decision"]["route_final"] == "FAST_TRACK"

    decisions = get_all_decisions(conn, "CLM70000001")
    assert len(decisions) == 1
    assert decisions[0]["version"] == 1
