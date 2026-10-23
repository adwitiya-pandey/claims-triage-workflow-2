"""Tests for deterministic claims triage rule engine per SPEC 11."""

from triage.rules import evaluate_rules

DEFAULT_CFG = {
    "high_amount": 10000.0,
    "filing_days": 90,
    "min_conf": 0.7,
}

BASE_CLEAN_FACTS = {
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
    "extracted": {
        "claim_id": "CLM70000001",
        "member_id": "M700001",
        "provider_id": "PRV7001",
        "procedure_code": "PX0001",
        "service_date": "2026-05-10",
        "billed_amount": 150.0,
        "preauth_reference": None,
        "documents_present": ["claim_form", "itemized_bill"],
    },
    "coverage_rule": {
        "plan_id": "PLN001",
        "procedure_code": "PX0001",
        "covered": True,
        "requires_preauth": False,
        "max_allowed": None,
    },
    "duplicates": [],
    "remaining_annual_limit": 50000.0,
    "required_documents": ["claim_form", "itemized_bill"],
    "supports": ["POL-PX0001"],
    "restricts": [],
    "confidence": 0.95,
    "extraction_failed": False,
    "injection_flags": [],
    "claim_missing": False,
    "tool_error": None,
}


def test_clean_claim_routes_fast_track():
    route, hits = evaluate_rules(BASE_CLEAN_FACTS, DEFAULT_CFG)
    assert route == "FAST_TRACK"
    assert hits == []


def test_rule_r01_claim_missing():
    facts = dict(BASE_CLEAN_FACTS, claim_missing=True)
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R01" in hits


def test_rule_r02_coverage_rule_unknown():
    facts = dict(BASE_CLEAN_FACTS, coverage_rule=None)
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R02" in hits


def test_rule_r03_not_covered():
    cov = dict(BASE_CLEAN_FACTS["coverage_rule"], covered=False)
    facts = dict(BASE_CLEAN_FACTS, coverage_rule=cov)
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R03" in hits


def test_rule_r04_preauth_missing_and_present():
    # Preauth missing
    cov = dict(BASE_CLEAN_FACTS["coverage_rule"], requires_preauth=True)
    facts_missing = dict(BASE_CLEAN_FACTS, coverage_rule=cov)
    route, hits = evaluate_rules(facts_missing, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R04" in hits

    # Preauth present
    ex = dict(BASE_CLEAN_FACTS["extracted"], preauth_reference="AUTH-1234")
    facts_present = dict(BASE_CLEAN_FACTS, coverage_rule=cov, extracted=ex)
    route, hits = evaluate_rules(facts_present, DEFAULT_CFG)
    assert route == "FAST_TRACK"
    assert hits == []


def test_rule_r05_over_max_allowed():
    cov = dict(BASE_CLEAN_FACTS["coverage_rule"], max_allowed=100.0)
    facts = dict(BASE_CLEAN_FACTS, coverage_rule=cov)
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R05" in hits


def test_rule_r06_high_amount():
    c = dict(BASE_CLEAN_FACTS["claim"], billed_amount=15000.0)
    ex = dict(BASE_CLEAN_FACTS["extracted"], billed_amount=15000.0)
    facts = dict(BASE_CLEAN_FACTS, claim=c, extracted=ex)
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R06" in hits


def test_rule_r07_out_of_network():
    c = dict(BASE_CLEAN_FACTS["claim"], provider_network_status="OUT_OF_NETWORK")
    facts = dict(BASE_CLEAN_FACTS, claim=c)
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R07" in hits


def test_rule_r08_duplicates():
    # Pending duplicate -> STANDARD_REVIEW
    facts_pended = dict(BASE_CLEAN_FACTS, duplicates=[{"status": "PENDED"}])
    route, hits = evaluate_rules(facts_pended, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R08" in hits

    # Paid duplicate -> ESCALATE
    facts_paid = dict(BASE_CLEAN_FACTS, duplicates=[{"status": "PAID"}])
    route, hits = evaluate_rules(facts_paid, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R08" in hits


def test_rule_r09_limit_exhausted():
    facts = dict(BASE_CLEAN_FACTS, remaining_annual_limit=50.0)  # billed is 150.0
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R09" in hits


def test_rule_r10_discrepancies():
    # Billed amount discrepancy
    ex_amt = dict(BASE_CLEAN_FACTS["extracted"], billed_amount=500.0)
    facts_amt = dict(BASE_CLEAN_FACTS, extracted=ex_amt)
    route, hits = evaluate_rules(facts_amt, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R10" in hits

    # Member ID discrepancy
    ex_mem = dict(BASE_CLEAN_FACTS["extracted"], member_id="M999999")
    facts_mem = dict(BASE_CLEAN_FACTS, extracted=ex_mem)
    route, hits = evaluate_rules(facts_mem, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R10" in hits


def test_rule_r11_timely_filing():
    # 90 days exact -> allowed (FAST_TRACK)
    c_90 = dict(
        BASE_CLEAN_FACTS["claim"],
        service_date="2026-01-01",
        submitted_on="2026-04-01",
    )
    ex_90 = dict(BASE_CLEAN_FACTS["extracted"], service_date="2026-01-01")
    facts_90 = dict(BASE_CLEAN_FACTS, claim=c_90, extracted=ex_90)
    route, hits = evaluate_rules(facts_90, DEFAULT_CFG)
    assert route == "FAST_TRACK"
    assert hits == []

    # 91 days -> STANDARD_REVIEW (R11)
    c_91 = dict(
        BASE_CLEAN_FACTS["claim"],
        service_date="2026-01-01",
        submitted_on="2026-04-02",
    )
    ex_91 = dict(BASE_CLEAN_FACTS["extracted"], service_date="2026-01-01")
    facts_91 = dict(BASE_CLEAN_FACTS, claim=c_91, extracted=ex_91)
    route, hits = evaluate_rules(facts_91, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R11" in hits


def test_rule_r12_injection_flag():
    facts = dict(BASE_CLEAN_FACTS, injection_flags=["PATTERN"])
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R12" in hits


def test_rule_r13_extraction_failed_or_low_confidence():
    facts_fail = dict(BASE_CLEAN_FACTS, extraction_failed=True)
    route, hits = evaluate_rules(facts_fail, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R13" in hits

    facts_low_conf = dict(BASE_CLEAN_FACTS, confidence=0.5)
    route, hits = evaluate_rules(facts_low_conf, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R13" in hits


def test_rule_r14_missing_documents():
    ex = dict(
        BASE_CLEAN_FACTS["extracted"],
        documents_present=["claim_form"],
    )
    facts = dict(BASE_CLEAN_FACTS, extracted=ex)
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R14" in hits


def test_rule_r15_no_supporting_clause():
    facts = dict(BASE_CLEAN_FACTS, supports=[])
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R15" in hits


def test_rule_r16_status_settled():
    c = dict(BASE_CLEAN_FACTS["claim"], status="PAID")
    facts = dict(BASE_CLEAN_FACTS, claim=c)
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R16" in hits


def test_rule_r17_restricting_clause():
    facts = dict(BASE_CLEAN_FACTS, restricts=["POL-EXCL-EXPERIMENTAL"])
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert "R17" in hits


def test_rule_r18_tool_error():
    facts = dict(BASE_CLEAN_FACTS, tool_error="INTERNAL")
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R18" in hits


def test_composite_and_early_stop_reporting():
    # Tool error plus injection reports BOTH R12 and R18
    facts = dict(BASE_CLEAN_FACTS, tool_error="INTERNAL", injection_flags=["PATTERN"])
    route, hits = evaluate_rules(facts, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert set(hits) == {"R12", "R18"}

    # Two medium hits -> STANDARD_REVIEW with both IDs
    cov = dict(BASE_CLEAN_FACTS["coverage_rule"], max_allowed=100.0)
    c = dict(BASE_CLEAN_FACTS["claim"], provider_network_status="OUT_OF_NETWORK")
    facts_med = dict(BASE_CLEAN_FACTS, coverage_rule=cov, claim=c)
    route, hits = evaluate_rules(facts_med, DEFAULT_CFG)
    assert route == "STANDARD_REVIEW"
    assert set(hits) == {"R05", "R07"}

    # Medium plus injection -> ESCALATE with both IDs
    facts_med_inj = dict(facts_med, injection_flags=["PATTERN"])
    route, hits = evaluate_rules(facts_med_inj, DEFAULT_CFG)
    assert route == "ESCALATE"
    assert "R12" in hits
    assert "R05" in hits or "R07" in hits


def test_property_routes_monotonic_and_bounded():
    """Property test: adding rule hit never lowers route; only 3 routes exist."""
    severity = {"FAST_TRACK": 0, "STANDARD_REVIEW": 1, "ESCALATE": 2}
    valid_routes = {"FAST_TRACK", "STANDARD_REVIEW", "ESCALATE"}

    r_clean, _ = evaluate_rules(BASE_CLEAN_FACTS, DEFAULT_CFG)
    assert r_clean in valid_routes

    perturbations = [
        {"claim_missing": True},
        {"tool_error": "INTERNAL"},
        {"injection_flags": ["PATTERN"]},
        {"extraction_failed": True},
        {"confidence": 0.3},
        {"coverage_rule": None},
        {"remaining_annual_limit": 10.0},
        {"restricts": ["POL-EXCL-EXPERIMENTAL"]},
    ]

    for p in perturbations:
        pert_facts = dict(BASE_CLEAN_FACTS, **p)
        route, hits = evaluate_rules(pert_facts, DEFAULT_CFG)
        assert route in valid_routes
        assert severity[route] >= severity[r_clean]
        assert len(hits) >= 1
