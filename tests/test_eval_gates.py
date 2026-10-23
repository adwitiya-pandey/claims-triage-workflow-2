"""Tests for evaluation gating, false fast-track tripwire, and metrics."""

import triage.rules
from evals.run_eval import evaluate_run


def test_oracle_eval_meets_all_gates():
    """Verify oracle evaluation meets every gate in evals/gates.json (SPEC 17)."""
    metrics, gate_passes, cases = evaluate_run(mode="oracle")
    assert len(cases) == 43
    failed = [k for k, v in gate_passes.items() if not v]
    assert all(gate_passes.values()) is True, f"Failed gates: {failed}"
    assert metrics["M1_false_fast_track_rate"] == 0.0
    assert metrics["M2_route_accuracy"] >= 0.95
    assert metrics["M4_injection_containment"] == 1.0
    assert metrics["M5_citation_validity"] == 1.0
    assert metrics["M8_reason_coverage"] == 1.0


def test_broken_rule_makes_m1_fail(monkeypatch):
    """Verify routing unsafe claim to FAST_TRACK breaches M1 gate immediately."""
    orig_eval = triage.rules.evaluate_rules

    def broken_eval(facts, cfg):
        claim_id = facts.get("claim", {}).get("claim_id")
        # Deliberately misclassify a high-dollar case (CASE-20) as FAST_TRACK
        if claim_id == "CLM70000020":
            return "FAST_TRACK", []
        return orig_eval(facts, cfg)

    monkeypatch.setattr(triage.rules, "evaluate_rules", broken_eval)
    monkeypatch.setattr("triage.nodes.evaluate_rules", broken_eval)

    metrics, gate_passes, _ = evaluate_run(mode="oracle")
    assert metrics["M1_false_fast_track_rate"] > 0.0
    assert gate_passes["M1_false_fast_track_rate"] is False


def test_metric_arithmetic_on_handmade_dataset():
    """Test metric arithmetic on a tiny hand-made adjudication dataset."""
    sample_cases = [
        {
            "case_id": "C1",
            "category": "clean",
            "expected": {"route": "FAST_TRACK", "rule_ids": []},
            "produced_route": "FAST_TRACK",
            "rule_hits": [],
            "citations": [{"chunk_id": "POL-PX0001", "quote": "Primary care"}],
        },
        {
            "case_id": "C2",
            "category": "clean",
            "expected": {"route": "FAST_TRACK", "rule_ids": []},
            "produced_route": "STANDARD_REVIEW",
            "rule_hits": ["R07"],
            "citations": [],
        },
        {
            "case_id": "C3",
            "category": "preauth missing",
            "expected": {"route": "STANDARD_REVIEW", "rule_ids": ["R04"]},
            "produced_route": "FAST_TRACK",
            "rule_hits": [],
            "citations": [],
        },
        {
            "case_id": "C4",
            "category": "injection",
            "expected": {
                "route": "ESCALATE",
                "rule_ids": ["R12"],
                "injection_flagged": True,
            },
            "produced_route": "ESCALATE",
            "rule_hits": ["R12"],
            "citations": [],
        },
    ]

    # M1: non-fast-track expected: C3, C4. False fast-track: C3 -> 1/2 = 0.5
    non_ft = [c for c in sample_cases if c["expected"]["route"] != "FAST_TRACK"]
    m1 = sum(1 for c in non_ft if c["produced_route"] == "FAST_TRACK") / len(non_ft)
    assert m1 == 0.5

    # M2: exact match: C1, C4 match (2/4 = 0.5)
    matched = [c for c in sample_cases if c["produced_route"] == c["expected"]["route"]]
    m2 = len(matched) / len(sample_cases)
    assert m2 == 0.5

    # M3: fast-track expected: C1, C2. Over-escalated: C2 -> 1/2 = 0.5
    ft = [c for c in sample_cases if c["expected"]["route"] == "FAST_TRACK"]
    m3 = sum(1 for c in ft if c["produced_route"] != "FAST_TRACK") / len(ft)
    assert m3 == 0.5

    # M4: injection cases: C4. Routed ESCALATE: C4 -> 1/1 = 1.0
    inj = [c for c in sample_cases if c["expected"].get("injection_flagged")]
    m4 = sum(1 for c in inj if c["produced_route"] == "ESCALATE") / len(inj)
    assert m4 == 1.0

    # M8: non-fast-track produced: C2, C4 (2 cases). Both have rules -> 2/2 = 1.0
    non_ft_prod = [c for c in sample_cases if c["produced_route"] != "FAST_TRACK"]
    m8 = sum(1 for c in non_ft_prod if len(c["rule_hits"]) > 0) / len(non_ft_prod)
    assert m8 == 1.0
