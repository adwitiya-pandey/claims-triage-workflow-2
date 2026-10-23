"""Tests verifying scenario cases, fixture backend, and security S4."""

import inspect
import json
from pathlib import Path

from triage.models import Assessment, Extraction
from triage.retrieval import load_corpus
from triage.tools.base import ClaimsTools
from triage.tools.fixture_backend import FixtureToolsBackend

EXPECTED_CATEGORIES = {
    "clean": 8,
    "preauth missing": 3,
    "not covered": 2,
    "coverage rule unknown": 2,
    "out of network": 2,
    "billed above max allowed": 2,
    "high amount": 2,
    "duplicate (1 pending, 1 approved, 1 paid)": 3,
    "late filing": 2,
    "annual limit exhausted": 2,
    "missing documents": 2,
    "extracted fields differ from record": 3,
    "injection in the submission text": 3,
    "injection in a claim note (from A)": 2,
    "obfuscated injection (hidden characters)": 1,
    "status already PAID": 1,
    "garbled or unreadable submission": 2,
    "claim not in the system": 1,
}


def test_cases_counts_and_validation():
    cases_dir = Path("data/cases")
    case_files = sorted(cases_dir.glob("*.json"))
    assert len(case_files) == 43, f"Expected 43 cases, got {len(case_files)}"

    corpus = load_corpus("data/policy/clauses.md")
    corpus_texts = {c.chunk_id: c.text for c in corpus}

    category_counts: dict[str, int] = {}

    for cf in case_files:
        data = json.loads(cf.read_text(encoding="utf-8"))
        cat = data["category"]
        category_counts[cat] = category_counts.get(cat, 0) + 1

        # Validate extraction & assessment
        Extraction.model_validate(data["gold_extraction"])
        Assessment.model_validate(data["gold_assessment"])

        # Validate quotes exist verbatim in corpus
        for cl in data["gold_assessment"]["clauses"]:
            cid = cl["chunk_id"]
            quote = cl["quote"]
            assert cid in corpus_texts, f"Unknown clause {cid} in {cf.name}"
            assert quote in corpus_texts[cid], (
                f"Quote not verbatim in {cid} for {cf.name}"
            )

    assert category_counts == EXPECTED_CATEGORIES


def test_tools_interface_is_read_only_s4():
    """Security Requirement S4: No write method may exist on tools interface."""
    forbidden_prefixes = (
        "create",
        "update",
        "delete",
        "insert",
        "write",
        "set",
        "pay",
        "approve",
        "deny",
    )

    for cls in [ClaimsTools, FixtureToolsBackend]:
        methods = [
            name
            for name, _ in inspect.getmembers(cls, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        for m in methods:
            assert not any(m.lower().startswith(p) for p in forbidden_prefixes), (
                f"Class {cls.__name__} has forbidden mutating method: {m}"
            )
        # Verify only approved read methods exist
        expected_methods = {
            "get_claim",
            "list_member_claims",
            "get_coverage_rule",
            "get_member_ytd_summary",
            "get_claim_notes",
        }
        assert set(methods) == expected_methods, (
            f"Unexpected methods on {cls.__name__}: {set(methods) - expected_methods}"
        )


def test_fixture_backend_output_shapes():
    backend = FixtureToolsBackend(cases_dir="data/cases")

    # 1. get_claim
    claim = backend.get_claim("CLM70000001")
    assert claim["claim_id"] == "CLM70000001"
    assert "provider_network_status" in claim
    assert "plan_id" in claim
    assert "status" in claim

    # 2. list_member_claims
    mem_claims = backend.list_member_claims("M700001")
    assert "total" in mem_claims
    assert "items" in mem_claims
    assert isinstance(mem_claims["items"], list)
    if mem_claims["items"]:
        item = mem_claims["items"][0]
        assert "claim_id" in item
        assert "service_date" in item
        assert "procedure_code" in item
        assert "billed_amount" in item
        assert "status" in item

    # 3. get_coverage_rule
    rule = backend.get_coverage_rule("PLN001", "PX0001")
    assert rule["plan_id"] == "PLN001"
    assert rule["procedure_code"] == "PX0001"
    assert "covered" in rule
    assert "requires_preauth" in rule

    # 4. get_member_ytd_summary
    ytd = backend.get_member_ytd_summary("M700001", 2026)
    assert ytd["member_id"] == "M700001"
    assert "annual_limit" in ytd
    assert "remaining_annual_limit" in ytd
    assert "by_status" in ytd

    # 5. get_claim_notes
    notes = backend.get_claim_notes("CLM70000001")
    assert notes["claim_id"] == "CLM70000001"
    assert notes["untrusted"] is True
    assert isinstance(notes["notes"], list)
