"""Contract integration tests comparing live Project A MCP to FixtureToolsBackend."""

import json
import os
from pathlib import Path

import pytest

from triage.tools.fixture_backend import FixtureToolsBackend
from triage.tools.mcp_backend import McpToolsBackend


@pytest.mark.integration
def test_contract_project_a_matches_fixtures():
    """Verify live Project A responses match FixtureToolsBackend for all cases."""
    mcp_url = os.getenv("CLAIMS_MCP_URL")
    if not mcp_url:
        pytest.skip("CLAIMS_MCP_URL is not set; skipping Project A integration test.")

    mcp_key = os.getenv("CLAIMS_MCP_KEY", "")
    live = McpToolsBackend(url=mcp_url, key=mcp_key)
    fixture = FixtureToolsBackend(cases_dir="data/cases")

    cases_dir = Path("data/cases")
    case_files = sorted(cases_dir.glob("*.json"))
    assert case_files, "No scenario cases found in data/cases"

    for cf in case_files:
        case = json.loads(cf.read_text(encoding="utf-8"))
        claim_id = case["claim_id"]

        # 1. Compare get_claim
        try:
            live_claim = live.get_claim(claim_id)
            fixture_claim = fixture.get_claim(claim_id)
            assert live_claim == fixture_claim, (
                f"Mismatch in get_claim for {claim_id}: {live_claim} != {fixture_claim}"
            )
        except Exception as e_live:
            try:
                fixture.get_claim(claim_id)
                pytest.fail(f"Live get_claim failed for {claim_id} with {e_live}")
            except Exception as e_fix:
                assert type(e_live) is type(e_fix)
                assert getattr(e_live, "code", "") == getattr(e_fix, "code", "")

        # 2. Compare get_coverage_rule if present
        facts = case.get("facts", {})
        cov = facts.get("coverage_rule")
        if cov:
            plan_id = cov["plan_id"]
            code = cov["procedure_code"]
            live_cov = live.get_coverage_rule(plan_id, code)
            fix_cov = fixture.get_coverage_rule(plan_id, code)
            assert live_cov == fix_cov, f"Mismatch in get_coverage_rule for {claim_id}"

        # 3. Compare get_member_ytd_summary
        member_id = case.get("member_id") or (facts.get("claim") or {}).get("member_id")
        if member_id:
            live_ytd = live.get_member_ytd_summary(member_id, 2026)
            fix_ytd = fixture.get_member_ytd_summary(member_id, 2026)
            assert live_ytd == fix_ytd, (
                f"Mismatch in get_member_ytd_summary for {member_id}"
            )

        # 4. Compare list_member_claims
        if member_id:
            live_list = live.list_member_claims(member_id)
            fix_list = fixture.list_member_claims(member_id)
            assert live_list == fix_list, (
                f"Mismatch in list_member_claims for {member_id}"
            )

        # 5. Compare get_claim_notes
        live_notes = live.get_claim_notes(claim_id)
        fix_notes = fixture.get_claim_notes(claim_id)
        assert live_notes == fix_notes, f"Mismatch in get_claim_notes for {claim_id}"
