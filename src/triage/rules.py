"""Deterministic claims triage rule engine per SPEC 11.

Rule Table:
R01: Claim not found in the system of record -> ESCALATE
R02: No coverage rule for the plan and procedure -> ESCALATE
R03: Rule says covered = false -> STANDARD_REVIEW
R04: Rule requires preauth and submission lacks preauth_reference -> STANDARD_REVIEW
R05: max_allowed is set and billed_amount exceeds it -> STANDARD_REVIEW
R06: billed_amount >= HIGH_AMOUNT_THRESHOLD -> ESCALATE
R07: Provider is OUT_OF_NETWORK -> STANDARD_REVIEW
R08: Duplicate exists: status APPROVED/PAID -> ESCALATE, else STANDARD_REVIEW
R09: remaining_annual_limit < billed_amount -> STANDARD_REVIEW
R10: Extracted fields differ from claim record -> ESCALATE
R11: Days from service_date to submitted_on > TIMELY_FILING_DAYS -> STANDARD_REVIEW
R12: Injection flag from submission text or any note -> ESCALATE
R13: Extraction failed or confidence < MIN_EXTRACT_CONFIDENCE -> ESCALATE
R14: Required document missing from documents_present -> STANDARD_REVIEW
R15: No valid supports clause after citation checks -> ESCALATE
R16: Claim status is not SUBMITTED or PENDED -> ESCALATE
R17: At least one valid restricts clause -> STANDARD_REVIEW
R18: Facts unavailable because a tool failed after retries -> ESCALATE
"""

from datetime import date
from typing import Any

RANK = {
    "FAST_TRACK": 0,
    "STANDARD_REVIEW": 1,
    "ESCALATE": 2,
}

REVERSE_RANK = {v: k for k, v in RANK.items()}


def evaluate_rules(
    facts: dict[str, Any],
    cfg: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """Pure function routing claims based on deterministic facts and config.

    Args:
        facts: Dictionary of facts compiled by workflow nodes.
        cfg: Config dictionary with high_amount, filing_days, min_conf.

    Returns:
        tuple of (final_route, list_of_triggered_rule_ids)
    """
    if cfg is None:
        cfg = {"high_amount": 10000.0, "filing_days": 90, "min_conf": 0.7}

    hits: list[tuple[str, str]] = []

    def hit(rule_id: str, route_type: str) -> None:
        hits.append((rule_id, route_type))

    # Pre-checks: failures and injection
    if facts.get("extraction_failed") or facts.get("confidence", 1.0) < cfg["min_conf"]:
        hit("R13", "ESCALATE")

    if facts.get("injection_flags"):
        hit("R12", "ESCALATE")

    if facts.get("claim_missing"):
        hit("R01", "ESCALATE")

    if facts.get("tool_error"):
        hit("R18", "ESCALATE")

    # Early termination: missing claim, extraction failure, or tool error
    if (
        facts.get("claim_missing")
        or facts.get("extraction_failed")
        or facts.get("tool_error")
    ):
        return "ESCALATE", sorted({h[0] for h in hits})

    claim = facts.get("claim", {})
    extracted = facts.get("extracted", {})

    # R16: Claim status must be SUBMITTED or PENDED
    if claim.get("status") not in ("SUBMITTED", "PENDED"):
        hit("R16", "ESCALATE")

    # R10: Discrepancies between extracted fields and system claim record
    for k in ("member_id", "procedure_code", "provider_id", "service_date"):
        if k in extracted and k in claim and extracted.get(k) != claim.get(k):
            hit("R10", "ESCALATE")

    if "billed_amount" in extracted and "billed_amount" in claim:
        diff = abs(float(extracted["billed_amount"]) - float(claim["billed_amount"]))
        if diff > 0.01:
            hit("R10", "ESCALATE")

    # Coverage rule evaluation
    rule = facts.get("coverage_rule")
    if rule is None:
        hit("R02", "ESCALATE")
    else:
        if not rule.get("covered", True):
            hit("R03", "STANDARD_REVIEW")
        if rule.get("requires_preauth") and not extracted.get("preauth_reference"):
            hit("R04", "STANDARD_REVIEW")
        if (
            rule.get("max_allowed") is not None
            and claim.get("billed_amount", 0.0) > rule["max_allowed"]
        ):
            hit("R05", "STANDARD_REVIEW")

    # R06: High amount threshold
    if claim.get("billed_amount", 0.0) >= cfg["high_amount"]:
        hit("R06", "ESCALATE")

    # R07: Provider network status
    if claim.get("provider_network_status") == "OUT_OF_NETWORK":
        hit("R07", "STANDARD_REVIEW")

    # R08: Duplicates
    for d in facts.get("duplicates", []):
        if d.get("status") in ("APPROVED", "PAID"):
            hit("R08", "ESCALATE")
        else:
            hit("R08", "STANDARD_REVIEW")

    # R09: Remaining annual limit
    rem_limit = facts.get("remaining_annual_limit")
    if rem_limit is not None and rem_limit < claim.get("billed_amount", 0.0):
        hit("R09", "STANDARD_REVIEW")

    # R11: Timely filing
    if "service_date" in claim and "submitted_on" in claim:
        try:
            sd = date.fromisoformat(claim["service_date"])
            so = date.fromisoformat(claim["submitted_on"])
            if (so - sd).days > cfg["filing_days"]:
                hit("R11", "STANDARD_REVIEW")
        except Exception:
            pass

    # R14: Required documents
    req_docs = set(facts.get("required_documents", []))
    pres_docs = set(extracted.get("documents_present", []))
    if req_docs - pres_docs:
        hit("R14", "STANDARD_REVIEW")

    # R15: Supporting policy clause
    if not facts.get("supports"):
        hit("R15", "ESCALATE")

    # R17: Restricting policy clause
    if facts.get("restricts"):
        hit("R17", "STANDARD_REVIEW")

    if not hits:
        return "FAST_TRACK", []

    top_severity = max(RANK[rt] for _, rt in hits)
    final_route = REVERSE_RANK[top_severity]
    unique_rule_ids = sorted({h[0] for h in hits})
    return final_route, unique_rule_ids
