"""Workflow node functions for claims triage LangGraph pipeline."""

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError

from triage.config import TriageConfig
from triage.injection import scan_injection
from triage.llm import format_user_content, generate_json, load_prompt
from triage.models import Extraction
from triage.sanitize import sanitize_text
from triage.state import TriageState
from triage.tools.base import ClaimsTools, ToolError


@dataclass
class NodeDependencies:
    """External dependencies injected into workflow nodes."""

    tools: ClaimsTools
    config: TriageConfig
    cases_dir: str | Path = "data/cases"
    fake_client: Any | None = None
    sleep_fn: Callable[[float], None] | None = None


def _load_case_data(claim_id: str, cases_dir: str | Path) -> dict[str, Any] | None:
    """Find and load case JSON by claim_id."""
    c_dir = Path(cases_dir)
    if not c_dir.exists():
        return None
    for jf in c_dir.glob("*.json"):
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
            if data.get("claim_id") == claim_id:
                return data
        except Exception:
            continue
    return None


def _load_required_documents(procedure_code: str) -> list[str]:
    """Load required documents for procedure code from JSON config."""
    req_path = Path("data/rules/required_documents.json")
    if not req_path.exists():
        req_path = (
            Path(__file__).parents[2] / "data" / "rules" / "required_documents.json"
        )
    if req_path.exists():
        try:
            rules_map = json.loads(req_path.read_text(encoding="utf-8"))
            if procedure_code in rules_map:
                return list(rules_map[procedure_code])
        except Exception:
            pass
    return ["claim_form", "itemized_bill"]


def _call_with_retry(
    fn: Callable[..., Any],
    *args: Any,
    sleep_fn: Callable[[float], None] | None = None,
    **kwargs: Any,
) -> Any:
    """Execute tool call with backoff retry on RATE_LIMITED per SPEC 8."""
    delays = [1.0, 2.0, 4.0]
    sleep = sleep_fn if sleep_fn is not None else time.sleep

    for attempt, delay in enumerate([0.0] + delays):
        if delay > 0:
            sleep(delay)
        try:
            return fn(*args, **kwargs)
        except ToolError as err:
            if err.code != "RATE_LIMITED" or attempt == len(delays):
                raise
    raise ToolError("RATE_LIMITED", "Exceeded rate limit retries")


def make_nodes(
    deps: NodeDependencies,
) -> dict[str, Callable[[TriageState], dict[str, Any]]]:
    """Factory creating node functions bound to injected dependencies."""

    def intake(state: TriageState) -> dict[str, Any]:
        """Validate claim ID, load submission text, sanitize and scan for injection."""
        started_at = datetime.now(timezone.utc).isoformat()
        claim_id = state.get("claim_id", "")

        case_data = _load_case_data(claim_id, deps.cases_dir)
        raw_text = case_data.get("submission_text", "") if case_data else ""

        injection_flags = scan_injection(raw_text)
        clean_text, _ = sanitize_text(raw_text)

        ended_at = datetime.now(timezone.utc).isoformat()
        trail_entry = {
            "node": "intake",
            "started_at": started_at,
            "ended_at": ended_at,
            "outcome": "ok",
            "detail_codes": list(injection_flags),
        }

        return {
            "raw_text": raw_text,
            "clean_text": clean_text,
            "injection_flags": injection_flags,
            "trail": [trail_entry],
        }

    def extract(state: TriageState) -> dict[str, Any]:
        """Extract structured claim fields via LLM with single validation retry."""
        started_at = datetime.now(timezone.utc).isoformat()
        claim_id = state.get("claim_id", "")
        clean_text = state.get("clean_text", "")

        prompt_text = load_prompt("extract_v1")
        user_content = format_user_content(clean_text)
        schema = Extraction.model_json_schema()

        gold_response = None
        if deps.config.LLM_MODE == "oracle":
            case_data = _load_case_data(claim_id, deps.cases_dir)
            if case_data and "gold_extraction" in case_data:
                gold_response = json.dumps(case_data["gold_extraction"])

        extraction_dict: dict[str, Any] | None = None
        extraction_failed = False
        outcome = "ok"
        detail_codes: list[str] = []

        # Attempt extraction with 1 retry on validation failure
        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                res = generate_json(
                    node="extract",
                    system_prompt=prompt_text,
                    user_content=user_content,
                    schema=schema,
                    prompt_version="extract_v1",
                    config=deps.config,
                    fake_client=deps.fake_client,
                    sleep_fn=deps.sleep_fn,
                    gold_response=gold_response,
                )
                model_inst = Extraction.model_validate_json(res.text)
                extraction_dict = model_inst.model_dump(mode="json")
                extraction_failed = False
                outcome = "ok"
                break
            except (ValidationError, Exception):
                if attempt < max_attempts - 1:
                    continue  # Retry once
                extraction_failed = True
                extraction_dict = None
                outcome = "failed"
                detail_codes.append("validation_error")

        ended_at = datetime.now(timezone.utc).isoformat()
        trail_entry = {
            "node": "extract",
            "started_at": started_at,
            "ended_at": ended_at,
            "outcome": outcome,
            "detail_codes": detail_codes,
        }

        return {
            "extraction": extraction_dict,
            "extraction_failed": extraction_failed,
            "trail": [trail_entry],
        }

    def fetch_facts(state: TriageState) -> dict[str, Any]:
        """Call read-only tools to retrieve claims facts and scan clinical notes."""
        started_at = datetime.now(timezone.utc).isoformat()
        claim_id = state.get("claim_id", "")
        current_injection_flags = list(state.get("injection_flags", []))
        detail_codes: list[str] = []

        # 1. Fetch claim record
        try:
            claim_row = _call_with_retry(
                deps.tools.get_claim,
                claim_id,
                sleep_fn=deps.sleep_fn,
            )
        except ToolError as err:
            ended_at = datetime.now(timezone.utc).isoformat()
            if err.code == "NOT_FOUND":
                trail_entry = {
                    "node": "fetch_facts",
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "outcome": "ok",
                    "detail_codes": ["claim_missing"],
                }
                return {
                    "facts": {},
                    "claim_missing": True,
                    "tool_error": None,
                    "trail": [trail_entry],
                }
            # Other tool error (INTERNAL, UNAUTHENTICATED, etc.)
            trail_entry = {
                "node": "fetch_facts",
                "started_at": started_at,
                "ended_at": ended_at,
                "outcome": "error",
                "detail_codes": [err.code],
            }
            return {
                "facts": {},
                "claim_missing": False,
                "tool_error": err.code,
                "trail": [trail_entry],
            }

        member_id = claim_row["member_id"]
        plan_id = claim_row["plan_id"]
        procedure_code = claim_row["procedure_code"]
        service_date = claim_row["service_date"]

        # 2. Fetch coverage rule
        coverage_rule: dict[str, Any] | None = None
        try:
            coverage_rule = _call_with_retry(
                deps.tools.get_coverage_rule,
                plan_id,
                procedure_code,
                sleep_fn=deps.sleep_fn,
            )
        except ToolError as err:
            if err.code == "NOT_FOUND":
                coverage_rule = None
            else:
                ended_at = datetime.now(timezone.utc).isoformat()
                return {
                    "facts": {},
                    "claim_missing": False,
                    "tool_error": err.code,
                    "trail": [
                        {
                            "node": "fetch_facts",
                            "started_at": started_at,
                            "ended_at": ended_at,
                            "outcome": "error",
                            "detail_codes": [err.code],
                        }
                    ],
                }

        # 3. Member YTD summary
        year = int(service_date.split("-")[0]) if "-" in service_date else 2026
        try:
            ytd = _call_with_retry(
                deps.tools.get_member_ytd_summary,
                member_id,
                year,
                sleep_fn=deps.sleep_fn,
            )
            remaining_annual_limit = ytd.get("remaining_annual_limit") if ytd else None
        except ToolError as err:
            ended_at = datetime.now(timezone.utc).isoformat()
            return {
                "facts": {},
                "claim_missing": False,
                "tool_error": err.code,
                "trail": [
                    {
                        "node": "fetch_facts",
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "outcome": "error",
                        "detail_codes": [err.code],
                    }
                ],
            }

        # 4. Member claims history (duplicate detection)
        try:
            history = _call_with_retry(
                deps.tools.list_member_claims,
                member_id,
                limit=50,
                sleep_fn=deps.sleep_fn,
            )
            all_member_claims = history.get("items", []) if history else []
            duplicates = [
                c
                for c in all_member_claims
                if c.get("procedure_code") == procedure_code
                and c.get("service_date") == service_date
                and c.get("claim_id") != claim_id
            ]
        except ToolError as err:
            ended_at = datetime.now(timezone.utc).isoformat()
            return {
                "facts": {},
                "claim_missing": False,
                "tool_error": err.code,
                "trail": [
                    {
                        "node": "fetch_facts",
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "outcome": "error",
                        "detail_codes": [err.code],
                    }
                ],
            }

        # 5. Claim notes (re-sanitized & scanned for injection)
        notes_list: list[dict[str, Any]] = []
        try:
            notes_resp = _call_with_retry(
                deps.tools.get_claim_notes,
                claim_id,
                limit=10,
                sleep_fn=deps.sleep_fn,
            )
            raw_notes = notes_resp.get("notes", []) if notes_resp else []
            for n in raw_notes:
                note_text = n.get("text", "")
                note_flags = scan_injection(note_text)
                if note_flags:
                    for nf in note_flags:
                        if nf not in current_injection_flags:
                            current_injection_flags.append(nf)
                    if "NOTE_INJECTION" not in detail_codes:
                        detail_codes.append("NOTE_INJECTION")
                clean_note, _ = sanitize_text(note_text)
                notes_list.append(dict(n, text=clean_note))
        except ToolError as err:
            ended_at = datetime.now(timezone.utc).isoformat()
            return {
                "facts": {},
                "claim_missing": False,
                "tool_error": err.code,
                "trail": [
                    {
                        "node": "fetch_facts",
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "outcome": "error",
                        "detail_codes": [err.code],
                    }
                ],
            }

        required_documents = _load_required_documents(procedure_code)

        facts = {
            "claim": claim_row,
            "coverage_rule": coverage_rule,
            "remaining_annual_limit": remaining_annual_limit,
            "duplicates": duplicates,
            "required_documents": required_documents,
            "notes": notes_list,
        }

        ended_at = datetime.now(timezone.utc).isoformat()
        trail_entry = {
            "node": "fetch_facts",
            "started_at": started_at,
            "ended_at": ended_at,
            "outcome": "ok",
            "detail_codes": detail_codes,
        }

        return {
            "facts": facts,
            "claim_missing": False,
            "tool_error": None,
            "injection_flags": current_injection_flags,
            "trail": [trail_entry],
        }

    return {
        "intake": intake,
        "extract": extract,
        "fetch_facts": fetch_facts,
    }
