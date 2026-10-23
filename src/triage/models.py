"""Pydantic data models and schemas with strict validation."""

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# Regex patterns using re.fullmatch and ASCII digits [0-9]
CLAIM_ID_REGEX = r"CLM[0-9]{8}"
MEMBER_ID_REGEX = r"M[0-9]{6}"
PLAN_ID_REGEX = r"PLN[0-9]{3}"
PROVIDER_ID_REGEX = r"PRV[0-9]{4}"
PROCEDURE_CODE_REGEX = r"PX[0-9]{4}"
CLAUSE_ID_REGEX = r"POL-[A-Z0-9-]{3,24}"
REVIEWER_ID_REGEX = r"[a-z][a-z0-9._-]{2,31}"

_CLAIM_ID_PAT = re.compile(f"^{CLAIM_ID_REGEX}$")
_MEMBER_ID_PAT = re.compile(f"^{MEMBER_ID_REGEX}$")
_PLAN_ID_PAT = re.compile(f"^{PLAN_ID_REGEX}$")
_PROVIDER_ID_PAT = re.compile(f"^{PROVIDER_ID_REGEX}$")
_PROCEDURE_CODE_PAT = re.compile(f"^{PROCEDURE_CODE_REGEX}$")
_CLAUSE_ID_PAT = re.compile(f"^{CLAUSE_ID_REGEX}$")
_REVIEWER_ID_PAT = re.compile(f"^{REVIEWER_ID_REGEX}$")


def validate_id(val: Any, pattern: re.Pattern, id_name: str) -> str:
    """Validate that val is a string matching the strict ID regex."""
    if not isinstance(val, str):
        raise ValueError(f"{id_name} must be a string, got {type(val).__name__}")
    if "\n" in val or "\r" in val:
        raise ValueError(f"{id_name} cannot contain newline characters")
    if not pattern.fullmatch(val):
        raise ValueError(f"Invalid {id_name} format: {val!r}")
    return val


ClaimId = Annotated[str, StringConstraints(pattern=f"^{CLAIM_ID_REGEX}$")]
MemberId = Annotated[str, StringConstraints(pattern=f"^{MEMBER_ID_REGEX}$")]
PlanId = Annotated[str, StringConstraints(pattern=f"^{PLAN_ID_REGEX}$")]
ProviderId = Annotated[str, StringConstraints(pattern=f"^{PROVIDER_ID_REGEX}$")]
ProcedureCode = Annotated[str, StringConstraints(pattern=f"^{PROCEDURE_CODE_REGEX}$")]
ClauseId = Annotated[str, StringConstraints(pattern=f"^{CLAUSE_ID_REGEX}$")]
ReviewerId = Annotated[str, StringConstraints(pattern=f"^{REVIEWER_ID_REGEX}$")]

ALLOWED_DOCUMENTS = {
    "claim_form",
    "itemized_bill",
    "referral",
    "medical_report",
    "preauth_letter",
    "id_proof",
}

DocumentType = Literal[
    "claim_form",
    "itemized_bill",
    "referral",
    "medical_report",
    "preauth_letter",
    "id_proof",
]

RouteType = Literal["FAST_TRACK", "STANDARD_REVIEW", "ESCALATE"]
EffectType = Literal["supports", "restricts", "neutral"]


class StrictBaseModel(BaseModel):
    """Base model forbidding extra fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class Extraction(StrictBaseModel):
    """Fields extracted from claim submission text."""

    claim_id: str
    member_id: str
    provider_id: str
    procedure_code: str | None = None
    procedure_description: str = Field(..., max_length=200)
    billed_amount: float = Field(..., ge=0.0)
    service_date: str = Field(..., max_length=10)
    preauth_reference: str | None = Field(default=None, max_length=40)
    documents_present: list[DocumentType] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    unreadable_fields: list[str] = Field(default_factory=list)

    @field_validator("claim_id")
    @classmethod
    def check_claim_id(cls, v: str) -> str:
        return validate_id(v, _CLAIM_ID_PAT, "claim_id")

    @field_validator("member_id")
    @classmethod
    def check_member_id(cls, v: str) -> str:
        return validate_id(v, _MEMBER_ID_PAT, "member_id")

    @field_validator("provider_id")
    @classmethod
    def check_provider_id(cls, v: str) -> str:
        return validate_id(v, _PROVIDER_ID_PAT, "provider_id")

    @field_validator("procedure_code")
    @classmethod
    def check_procedure_code(cls, v: str | None) -> str | None:
        if v is not None:
            return validate_id(v, _PROCEDURE_CODE_PAT, "procedure_code")
        return v

    @field_validator("service_date")
    @classmethod
    def check_service_date(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
            raise ValueError(f"Invalid ISO date format: {v!r}")
        return v


class AssessmentClause(StrictBaseModel):
    """Citation of a policy clause evaluated against a claim."""

    chunk_id: str
    effect: EffectType
    quote: str = Field(..., max_length=300)

    @field_validator("chunk_id")
    @classmethod
    def check_chunk_id(cls, v: str) -> str:
        return validate_id(v, _CLAUSE_ID_PAT, "chunk_id")


class Assessment(StrictBaseModel):
    """Assessment produced by LLM and validated against corpus."""

    summary: str = Field(..., max_length=700)
    clauses: list[AssessmentClause] = Field(default_factory=list)
    missing_items: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(..., ge=0.0, le=1.0)


class RuleHit(StrictBaseModel):
    """A rule triggered during evaluation."""

    rule_id: str
    route: RouteType
    evidence: list[str] = Field(default_factory=list)


class ReviewAction(StrictBaseModel):
    """Action taken by human reviewer during interruption."""

    reviewer_id: str
    action: Literal["CONFIRM", "ESCALATE_FURTHER"]
    reason: str = Field(..., min_length=10, max_length=500)

    @field_validator("reviewer_id")
    @classmethod
    def check_reviewer_id(cls, v: str) -> str:
        return validate_id(v, _REVIEWER_ID_PAT, "reviewer_id")


class TrailEntry(StrictBaseModel):
    """Audit trail record from a single workflow node."""

    node: str
    started_at: str
    ended_at: str
    outcome: str
    detail_codes: list[str] = Field(default_factory=list)


class LLMUsage(StrictBaseModel):
    """Aggregated token and call metrics for LLM operations."""

    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0


class DecisionVersions(StrictBaseModel):
    """Versions of components contributing to a triage decision."""

    graph: str
    rules: str
    prompt_extract: str
    prompt_assess: str
    model: str
    corpus_sha256: str


class Decision(StrictBaseModel):
    """Final auditable decision record."""

    claim_id: str
    version: int = Field(..., ge=1)
    route_recommended: RouteType
    route_final: RouteType
    reasons: list[RuleHit] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    summary: str
    missing_items: list[str] = Field(default_factory=list)
    extraction: dict[str, Any] | None = None
    facts_digest: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] | None = None
    versions: DecisionVersions
    llm_usage: LLMUsage
    trail: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str

    @field_validator("claim_id")
    @classmethod
    def check_claim_id(cls, v: str) -> str:
        return validate_id(v, _CLAIM_ID_PAT, "claim_id")
