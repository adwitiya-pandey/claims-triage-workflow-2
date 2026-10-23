"""Workflow node functions for claims triage LangGraph pipeline."""

import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langgraph.types import interrupt
from pydantic import ValidationError

from triage.config import TriageConfig
from triage.injection import scan_injection
from triage.llm import format_user_content, generate_json, load_prompt
from triage.models import Assessment, Decision, Extraction, ReviewAction, RuleHit
from triage.retrieval import compute_corpus_sha256, load_corpus, retrieve
from triage.rules import evaluate_rules
from triage.sanitize import sanitize_text
from triage.state import TriageState
from triage.store import init_db, save_decision
from triage.tools.base import ClaimsTools, ToolError


@dataclass
class NodeDependencies:
    """External dependencies injected into workflow nodes."""

    tools: ClaimsTools
    config: TriageConfig
    cases_dir: str | Path = "data/cases"
    fake_client: Any | None = None
    sleep_fn: Callable[[float], None] | None = None
    db_conn: sqlite3.Connection | None = None
    interrupt_fn: Callable[[Any], Any] | None = None


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
    interrupt_fn: Callable[[Any], Any] | None = None,
) -> dict[str, Callable[[TriageState], dict[str, Any]]]:
    """Factory creating node functions bound to injected dependencies."""
    _interrupt = (
        interrupt_fn
        if interrupt_fn is not None
        else (deps.interrupt_fn if deps.interrupt_fn is not None else interrupt)
    )

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
                    continue
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

    def retrieve_node(state: TriageState) -> dict[str, Any]:
        """BM25 retrieval over policy clauses + procedure clause per SPEC 9."""
        started_at = datetime.now(timezone.utc).isoformat()

        # Skip immediately if prerequisites failed
        if (
            state.get("extraction_failed")
            or state.get("claim_missing")
            or state.get("tool_error")
        ):
            ended_at = datetime.now(timezone.utc).isoformat()
            return {
                "chunks": [],
                "trail": [
                    {
                        "node": "retrieve",
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "outcome": "skipped",
                        "detail_codes": [],
                    }
                ],
            }

        proc_desc = (state.get("extraction") or {}).get("procedure_description", "")
        claim_facts = (state.get("facts") or {}).get("claim", {})
        proc_code = claim_facts.get("procedure_code", "")

        chunks = retrieve(
            query=proc_desc,
            procedure_code=proc_code,
            top_k=deps.config.TOP_K,
        )

        ended_at = datetime.now(timezone.utc).isoformat()
        return {
            "chunks": chunks,
            "trail": [
                {
                    "node": "retrieve",
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "outcome": "ok",
                    "detail_codes": [],
                }
            ],
        }

    def assess(state: TriageState) -> dict[str, Any]:
        """Assess claim against retrieved clauses and apply citation checks."""
        started_at = datetime.now(timezone.utc).isoformat()

        # Skip immediately if prerequisites failed
        if (
            state.get("extraction_failed")
            or state.get("claim_missing")
            or state.get("tool_error")
        ):
            ended_at = datetime.now(timezone.utc).isoformat()
            return {
                "assessment": None,
                "trail": [
                    {
                        "node": "assess",
                        "started_at": started_at,
                        "ended_at": ended_at,
                        "outcome": "skipped",
                        "detail_codes": [],
                    }
                ],
            }

        claim_id = state.get("claim_id", "")
        prompt_text = load_prompt("assess_v1")
        chunks = state.get("chunks", [])

        facts_dict = state.get("facts") or {}
        context_payload = {
            "extracted": state.get("extraction"),
            "facts": {
                "claim": facts_dict.get("claim"),
                "coverage_rule": facts_dict.get("coverage_rule"),
                "remaining_annual_limit": facts_dict.get("remaining_annual_limit"),
                "duplicates": facts_dict.get("duplicates"),
            },
            "retrieved_clauses": [
                {"chunk_id": c["chunk_id"], "text": c["text"]} for c in chunks
            ],
        }
        user_content = json.dumps(context_payload, indent=2)

        gold_response = None
        if deps.config.LLM_MODE == "oracle":
            case_data = _load_case_data(claim_id, deps.cases_dir)
            if case_data and "gold_assessment" in case_data:
                gold_response = json.dumps(case_data["gold_assessment"])

        res = generate_json(
            node="assess",
            system_prompt=prompt_text,
            user_content=user_content,
            schema=Assessment.model_json_schema(),
            prompt_version="assess_v1",
            config=deps.config,
            fake_client=deps.fake_client,
            sleep_fn=deps.sleep_fn,
            gold_response=gold_response,
        )

        raw_assessment = Assessment.model_validate_json(res.text)

        # Deterministic citation checks per SPEC 10:
        # 1. Every chunk_id must exist in retrieved chunks
        # 2. Quotes for supports/restricts must be >= 20 chars and verbatim
        chunk_map = {c["chunk_id"]: c["text"] for c in chunks}
        validated_clauses = []
        dropped_count = 0

        for clause in raw_assessment.clauses:
            cid = clause.chunk_id
            if cid not in chunk_map:
                dropped_count += 1
                continue

            if clause.effect in ("supports", "restricts"):
                q = clause.quote.strip()
                if len(q) < 20:
                    dropped_count += 1
                    continue
                q_norm = " ".join(q.lower().split())
                chunk_norm = " ".join(chunk_map[cid].lower().split())
                if q_norm not in chunk_norm:
                    dropped_count += 1
                    continue

            validated_clauses.append(clause)

        detail_codes = []
        if dropped_count > 0:
            detail_codes.append("dropped_citations")

        sanitized_assessment = Assessment(
            summary=raw_assessment.summary,
            clauses=validated_clauses,
            missing_items=raw_assessment.missing_items,
            confidence=raw_assessment.confidence,
        ).model_dump(mode="json")

        ended_at = datetime.now(timezone.utc).isoformat()
        return {
            "assessment": sanitized_assessment,
            "trail": [
                {
                    "node": "assess",
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "outcome": "ok",
                    "detail_codes": detail_codes,
                }
            ],
        }

    def decide(state: TriageState) -> dict[str, Any]:
        """Pure rule engine invocation: the only place a route is chosen (SPEC 11)."""
        started_at = datetime.now(timezone.utc).isoformat()

        facts_bundle = state.get("facts") or {}
        assessment = state.get("assessment") or {}
        clauses = assessment.get("clauses", [])

        supports_chunks = [
            c["chunk_id"] for c in clauses if c.get("effect") == "supports"
        ]
        restricts_chunks = [
            c["chunk_id"] for c in clauses if c.get("effect") == "restricts"
        ]

        facts_for_rules = {
            "claim": facts_bundle.get("claim", {}),
            "extracted": state.get("extraction") or {},
            "coverage_rule": facts_bundle.get("coverage_rule"),
            "remaining_annual_limit": facts_bundle.get("remaining_annual_limit"),
            "duplicates": facts_bundle.get("duplicates", []),
            "required_documents": facts_bundle.get("required_documents", []),
            "supports": supports_chunks,
            "restricts": restricts_chunks,
            "confidence": (state.get("extraction") or {}).get("confidence", 1.0),
            "extraction_failed": state.get("extraction_failed", False),
            "injection_flags": state.get("injection_flags", []),
            "claim_missing": state.get("claim_missing", False),
            "tool_error": state.get("tool_error"),
        }

        cfg_thresholds = {
            "high_amount": deps.config.HIGH_AMOUNT_THRESHOLD,
            "filing_days": deps.config.TIMELY_FILING_DAYS,
            "min_conf": deps.config.MIN_EXTRACT_CONFIDENCE,
        }

        route, hit_ids = evaluate_rules(facts_for_rules, cfg_thresholds)

        rule_hits = [
            RuleHit(
                rule_id=hid,
                route=route,
                evidence=[f"rule.{hid}"],
            ).model_dump(mode="json")
            for hid in hit_ids
        ]

        ended_at = datetime.now(timezone.utc).isoformat()
        return {
            "route": route,
            "rule_hits": rule_hits,
            "trail": [
                {
                    "node": "decide",
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "outcome": "ok",
                    "detail_codes": hit_ids,
                }
            ],
        }

    def review(state: TriageState) -> dict[str, Any]:
        """Human review node using LangGraph durable interrupt (SPEC 12)."""
        started_at = datetime.now(timezone.utc).isoformat()

        # Build review packet: strictly JSON metadata, NO raw text, NO note text
        packet = {
            "claim_id": state.get("claim_id"),
            "recommended_route": state.get("route"),
            "rule_hits": state.get("rule_hits", []),
            "citations": (state.get("assessment") or {}).get("clauses", []),
            "analyst_summary": (state.get("assessment") or {}).get("summary"),
            "missing_items": (state.get("assessment") or {}).get("missing_items", []),
            "flags": state.get("injection_flags", []),
            "comparison": {
                "extracted": state.get("extraction"),
                "system_record": {
                    "billed_amount": (
                        (state.get("facts") or {}).get("claim", {}).get("billed_amount")
                    ),
                    "status": (
                        (state.get("facts") or {}).get("claim", {}).get("status")
                    ),
                },
            },
        }

        # Durable interrupt: node restarts from first line on resume
        resume_value = _interrupt(packet)

        # Validate human action with strict Pydantic model
        action = ReviewAction.model_validate(resume_value)

        ended_at = datetime.now(timezone.utc).isoformat()
        return {
            "review": action.model_dump(mode="json"),
            "trail": [
                {
                    "node": "review",
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "outcome": "ok",
                    "detail_codes": [action.action],
                }
            ],
        }

    def finalize(state: TriageState) -> dict[str, Any]:
        """Build decision record and persist to SQLite store idempotently (SPEC 13)."""
        started_at = datetime.now(timezone.utc).isoformat()

        route_recommended = state.get("route") or "ESCALATE"
        review_data = state.get("review")

        if review_data and review_data.get("action") == "ESCALATE_FURTHER":
            route_final = "ESCALATE"
        else:
            route_final = route_recommended

        # Load policy corpus hash
        corpus = load_corpus("data/policy/clauses.md")
        corpus_hash = compute_corpus_sha256(corpus)

        claim_facts = (state.get("facts") or {}).get("claim", {})
        facts_digest = {
            "billed_amount": claim_facts.get("billed_amount"),
            "status": claim_facts.get("status"),
            "service_date": claim_facts.get("service_date"),
            "procedure_code": claim_facts.get("procedure_code"),
            "provider_network_status": claim_facts.get("provider_network_status"),
        }

        ended_at = datetime.now(timezone.utc).isoformat()
        current_trail = list(state.get("trail", []))
        finalize_trail_entry = {
            "node": "finalize",
            "started_at": started_at,
            "ended_at": ended_at,
            "outcome": "ok",
            "detail_codes": [],
        }
        all_trail = current_trail + [finalize_trail_entry]

        decision_obj = Decision(
            claim_id=state.get("claim_id", ""),
            version=state.get("version", 1),
            route_recommended=route_recommended,
            route_final=route_final,
            reasons=state.get("rule_hits", []),
            citations=(state.get("assessment") or {}).get("clauses", []),
            summary=(
                (state.get("assessment") or {}).get(
                    "summary", "Triage decision recorded."
                )
            ),
            missing_items=(state.get("assessment") or {}).get("missing_items", []),
            extraction=state.get("extraction"),
            facts_digest=facts_digest,
            review=review_data,
            versions={
                "graph": "1",
                "rules": "1",
                "prompt_extract": "extract_v1",
                "prompt_assess": "assess_v1",
                "model": deps.config.GEMINI_MODEL,
                "corpus_sha256": corpus_hash,
            },
            llm_usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "calls": 2 if not state.get("extraction_failed") else 1,
            },
            trail=all_trail,
            created_at=ended_at,
        )

        decision_dict = decision_obj.model_dump(mode="json")

        # Persist to database (S10 append-only via INSERT OR IGNORE)
        db_conn = deps.db_conn
        if db_conn is None:
            db_conn = init_db(deps.config.TRIAGE_DB_PATH)
        save_decision(db_conn, decision_dict)

        return {
            "decision": decision_dict,
            "trail": [finalize_trail_entry],
        }

    return {
        "intake": intake,
        "extract": extract,
        "fetch_facts": fetch_facts,
        "retrieve": retrieve_node,
        "assess": assess,
        "decide": decide,
        "review": review,
        "finalize": finalize,
    }
