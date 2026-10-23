"""Tests for data models and ID patterns."""

import pytest
from pydantic import ValidationError

from triage.models import (
    _CLAIM_ID_PAT,
    _CLAUSE_ID_PAT,
    _MEMBER_ID_PAT,
    _PLAN_ID_PAT,
    _PROCEDURE_CODE_PAT,
    _PROVIDER_ID_PAT,
    _REVIEWER_ID_PAT,
    Assessment,
    AssessmentClause,
    Decision,
    Extraction,
    ReviewAction,
    validate_id,
)

ID_TEST_CASES = [
    (
        "claim_id",
        _CLAIM_ID_PAT,
        "CLM12345678",
        ["CLM12345678\n", "CLM1234567\u0660", "", "CLM123456789", "CLM1234567"],
    ),
    (
        "member_id",
        _MEMBER_ID_PAT,
        "M123456",
        ["M123456\n", "M12345\u0660", "", "M1234567", "M12345"],
    ),
    (
        "plan_id",
        _PLAN_ID_PAT,
        "PLN123",
        ["PLN123\n", "PLN12\u0660", "", "PLN1234", "PLN12"],
    ),
    (
        "provider_id",
        _PROVIDER_ID_PAT,
        "PRV1234",
        ["PRV1234\n", "PRV123\u0660", "", "PRV12345", "PRV123"],
    ),
    (
        "procedure_code",
        _PROCEDURE_CODE_PAT,
        "PX1234",
        ["PX1234\n", "PX123\u0660", "", "PX12345", "PX123"],
    ),
    (
        "clause_id",
        _CLAUSE_ID_PAT,
        "POL-PREAUTH-01",
        ["POL-PREAUTH-01\n", "POL-0\u0660", "", "P", "POL-" + "A" * 30],
    ),
    (
        "reviewer_id",
        _REVIEWER_ID_PAT,
        "reviewer_1",
        ["reviewer_1\n", "r\u0660", "", "r", "123reviewer", "a" * 35],
    ),
]


@pytest.mark.parametrize("id_name,pattern,valid_sample,invalids", ID_TEST_CASES)
def test_id_patterns_valid_and_invalid(id_name, pattern, valid_sample, invalids):
    # Valid
    assert validate_id(valid_sample, pattern, id_name) == valid_sample

    # Invalids (trailing newline, non-ASCII digits, empty, overlong/short)
    for inv in invalids:
        with pytest.raises(ValueError):
            validate_id(inv, pattern, id_name)

    # Wrong type
    with pytest.raises(ValueError):
        validate_id(12345, pattern, id_name)
    with pytest.raises(ValueError):
        validate_id(["CLM12345678"], pattern, id_name)


def test_extraction_validation_and_extra_fields():
    valid_data = {
        "claim_id": "CLM70000001",
        "member_id": "M700001",
        "provider_id": "PRV7001",
        "procedure_code": "PX0001",
        "procedure_description": "Standard consultation and assessment",
        "billed_amount": 250.0,
        "service_date": "2026-05-10",
        "preauth_reference": "AUTH-1234",
        "documents_present": ["claim_form", "itemized_bill"],
        "confidence": 0.95,
        "unreadable_fields": [],
    }
    ext = Extraction.model_validate(valid_data)
    assert ext.claim_id == "CLM70000001"
    assert ext.billed_amount == 250.0

    # Extra fields rejected
    data_with_extra = dict(valid_data, extra_field="unexpected")
    with pytest.raises(ValidationError):
        Extraction.model_validate(data_with_extra)

    # Negative billed amount rejected
    data_neg_amount = dict(valid_data, billed_amount=-10.0)
    with pytest.raises(ValidationError):
        Extraction.model_validate(data_neg_amount)

    # Confidence out of bounds rejected
    with pytest.raises(ValidationError):
        Extraction.model_validate(dict(valid_data, confidence=1.5))
    with pytest.raises(ValidationError):
        Extraction.model_validate(dict(valid_data, confidence=-0.1))

    # Invalid document type rejected
    with pytest.raises(ValidationError):
        Extraction.model_validate(
            dict(valid_data, documents_present=["random_passport"])
        )


def test_review_action_validation():
    # Valid CONFIRM
    action1 = ReviewAction.model_validate(
        {
            "reviewer_id": "analyst.alice",
            "action": "CONFIRM",
            "reason": "Verified supporting clinical documentation is complete.",
        }
    )
    assert action1.action == "CONFIRM"

    # Valid ESCALATE_FURTHER
    action2 = ReviewAction.model_validate(
        {
            "reviewer_id": "senior_reviewer",
            "action": "ESCALATE_FURTHER",
            "reason": "Discrepancy noted between billed amount and hospital invoice.",
        }
    )
    assert action2.action == "ESCALATE_FURTHER"

    # Strict ban on DENY, APPROVE, PAY
    for forbidden in ["DENY", "APPROVE", "PAY", "REJECT"]:
        with pytest.raises(ValidationError):
            ReviewAction.model_validate(
                {
                    "reviewer_id": "analyst.alice",
                    "action": forbidden,
                    "reason": "This action should not be permitted in the system.",
                }
            )

    # Too short reason (< 10 chars) rejected
    with pytest.raises(ValidationError):
        ReviewAction.model_validate(
            {
                "reviewer_id": "analyst.alice",
                "action": "CONFIRM",
                "reason": "Too short",
            }
        )

    # Extra fields rejected
    with pytest.raises(ValidationError):
        ReviewAction.model_validate(
            {
                "reviewer_id": "analyst.alice",
                "action": "CONFIRM",
                "reason": "Valid reason explanation that meets the length threshold.",
                "unauthorized_field": "blocked",
            }
        )


def test_assessment_and_decision_models():
    clause = AssessmentClause.model_validate(
        {
            "chunk_id": "POL-PREAUTH-01",
            "effect": "supports",
            "quote": "Preauthorisation was granted prior to elective admission.",
        }
    )
    assert clause.effect == "supports"

    assessment = Assessment.model_validate(
        {
            "summary": "Clear claim with full preauthorisation documentation.",
            "clauses": [clause],
            "missing_items": [],
            "confidence": 0.98,
        }
    )
    assert len(assessment.clauses) == 1

    decision = Decision.model_validate(
        {
            "claim_id": "CLM70000001",
            "version": 1,
            "route_recommended": "FAST_TRACK",
            "route_final": "FAST_TRACK",
            "reasons": [],
            "citations": [
                {"chunk_id": "POL-PREAUTH-01", "effect": "supports", "quote": "Quote"}
            ],
            "summary": "Clean fast track claim.",
            "missing_items": [],
            "extraction": {"billed_amount": 250.0},
            "facts_digest": {"status": "SUBMITTED"},
            "review": None,
            "versions": {
                "graph": "1",
                "rules": "1",
                "prompt_extract": "extract_v1",
                "prompt_assess": "assess_v1",
                "model": "gemini-3.8-flash",
                "corpus_sha256": "abc123",
            },
            "llm_usage": {"input_tokens": 100, "output_tokens": 50, "calls": 2},
            "trail": [{"node": "intake", "outcome": "ok", "detail_codes": []}],
            "created_at": "2026-10-23T12:00:00Z",
        }
    )
    assert decision.version == 1
    assert decision.route_final == "FAST_TRACK"
