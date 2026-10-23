# Threat Model: Claims Triage Workflow

**Document Version:** 1.0.0  
**Target System:** `claims-triage-workflow` (Project B)  
**Security Standard:** STRIDE Threat Classification & Zero-Trust Architectural Principles  
**Scope:** Ingestion, sanitization, LLM extraction/assessment, deterministic rule evaluation, human review gates, persistence, and external MCP communication.

---

## 1. System Overview & Trust Boundaries

The Claims Triage Workflow automates the intake, fact-checking, and routing recommendations (`FAST_TRACK`, `STANDARD_REVIEW`, `ESCALATE`) for health insurance claims submitted to a Third-Party Administrator (TPA). The system operates on a zero-trust model where LLMs have **zero routing authority**, claim notes are treated as **untrusted data**, and all routing decisions are enforced by a **pure Python rule engine**.

```
                           [TB1: External Submitter]
                                      │
                         Untrusted Claim Text & Payload
                                      ▼
                      ┌───────────────────────────────┐
                      │      1. Intake & Sanitize     │
                      │  - Remove Control / Cf chars  │
                      │  - Scan 13 Injection Regexes  │
                      └──────────────┬────────────────┘
                                     │
           [TB3: LLM API]            │ Safe Prompt Delimiters
           ┌────────────┐            ▼
           │  Gemini /  │◄──── 2. Extraction Node
           │ Cassette   │─────►  (Pydantic extra='forbid')
           └────────────┘            │
                                     ▼
                      ┌───────────────────────────────┐
                      │      3. Fetch Facts Node      │
                      └──────────────┬────────────────┘
                                     │
                                     │ Read-Only Queries
                                     ▼
                        [TB2: Project A MCP Boundary]
                        ┌───────────────────────────┐
                        │ Claims MCP Server         │
                        │ - Bearer Token Auth       │
                        │ - Token Bucket Rate Limit │
                        │ - Sanitized Clinical Notes│
                        └───────────────────────────┘
                                     │
                                     ▼
                      ┌───────────────────────────────┐
                      │    4. BM25 Policy Retrieval   │
                      │    & LLM Assessment Node      │
                      │  - Deterministic Citation Chk │
                      │  - Verbatim Quote Grounding   │
                      └──────────────┬────────────────┘
                                     │
                                     ▼
                      ┌───────────────────────────────┐
                      │     5. Deterministic Rules    │
                      │  (Only place route is chosen) │
                      └──────────────┬────────────────┘
                                     │
                    Route != FAST_TRACK?
                                     │
                                     ▼
                        [TB4: Human Review Gate]
                        ┌───────────────────────────┐
                        │ Review Node: interrupt()  │
                        │ - Durable Pause / Resume  │
                        │ - Confirm / Escalate only │
                        └──────────────┬────────────┘
                                     │
                                     ▼
                        [TB5: Local Store Boundary]
                        ┌───────────────────────────┐
                        │ SQLite Store (store.py)   │
                        │ - Append-Only decisions   │
                        │ - Zero UPDATE / DELETE    │
                        └───────────────────────────┘
```

### Trust Boundary Definitions:
- **TB1: External Submitter Boundary**: Patient or clinic submitting unstructured claim text. Treated as hostile and untrusted.
- **TB2: External MCP Server Boundary**: Out-of-process Project A service. Communication strictly read-only over streamable HTTP with Bearer authentication and exponential retry backoff.
- **TB3: LLM Provider Boundary**: Google Gemini API (or offline cassette replay). Delimited prompt structure; strict Pydantic schemas; zero tool execution capabilities.
- **TB4: Human Reviewer Boundary**: Authenticated staff interacting through FastAPI endpoints. May confirm or escalate caution; cannot approve payment or bypass safety gates.
- **TB5: Persistence Boundary**: SQLite databases (`triage.db` and `checkpoint.db`). Local file access restricted; append-only schema constraints.

---

## 2. STRIDE Threat Analysis

| Threat Category | Threat Description | Boundary Affected | Architectural Defense & Mitigation | Cross-Ref |
| :--- | :--- | :--- | :--- | :--- |
| **Spoofing** | Adversary attempts to forge internal review decisions or bypass API authentication. | TB4 (API) | Constant-time bearer token verification (`secrets.compare_digest`). Workflow refuses startup without `TRIAGE_API_KEY`. Review action records reviewer ID and timestamp. | S8, S9 |
| **Tampering** | Indirect prompt injection embedded in claim submission or MCP clinical notes attempting to force `FAST_TRACK`. | TB1, TB2 | Two-phase injection scanner (13 regex heuristics + zero-width Unicode normalization). Injected notes force rule R08/R12 to trigger immediate `ESCALATE`. Fixed delimiters `<<<CLAIM_TEXT>>>`. Pure Python rules make route decision immutable by LLM text. | S1, S2, S3 |
| **Repudiation** | Auditor cannot determine why a claim was fast-tracked or who authorized a high-dollar settlement. | TB4, TB5 | Immutable append-only SQLite `decisions` table with SHA-256 state hashing and monotonic versioning. Decision records persist exact rule IDs hit, evidence citations, reviewer ID, and duration. | S10, S11 |
| **Information Disclosure** | PHI / PII leakage in application logs, error payloads, or external API responses. | TB1, TB3, TB4 | Structured JSON log formatter strictly enforces `ALLOWED_LOG_KEYS` and rejects `FORBIDDEN_KEYS` (`raw_text`, `submission_text`, `notes`). FastAPI exception handlers scrub tracebacks and internal schema internals. | S6, S7 |
| **Denial of Service** | Extremely large claim text, regex catastrophic backtracking (ReDoS), or MCP connection exhaustion. | TB1, TB2 | Hard input length limits (5,000 characters). Strict anchored regexes (`re.fullmatch` with `[0-9]` instead of non-ASCII digits). Client-side exponential retry backoff on `RATE_LIMITED` HTTP responses. | S2, S5 |
| **Elevation of Privilege** | Compromised API key attempting to approve claim payments or delete historical triage audits. | TB2, TB5 | Non-goals strictly enforced: zero payment/approve endpoints exist in codebase. `ClaimsTools` interface has no write methods. SQLite triggers or queries enforce `INSERT` only (no `UPDATE` or `DELETE`). | S4, S10 |

---

## 3. Security Requirements Compliance Matrix (S1–S14)

| Req ID | Security Requirement Description | Implementation Location | Verification Test |
| :--- | :--- | :--- | :--- |
| **S1** | Untrusted claim text delimited as data, never in system instructions; LLM has zero tools and zero route authority. | `src/triage/nodes.py`, `src/triage/llm.py` | `tests/test_nodes_extract.py`, `tests/test_llm.py` |
| **S2** | Text sanitized (control and Cf characters removed, length capped) and scanned before any LLM call. Injection forces R12/R08. | `src/triage/sanitize.py`, `src/triage/injection.py` | `tests/test_sanitize.py`, `tests/test_injection.py` |
| **S3** | Malicious LLM output cannot force `FAST_TRACK`. Extra fields forbidden; `supports` requires verbatim quote >= 20 chars. | `src/triage/models.py`, `src/triage/nodes.py` | `tests/test_nodes_assess.py` |
| **S4** | Read-only tools interface. No write methods. No import of Project A internal code. | `src/triage/tools/base.py`, `src/triage/tools/mcp_backend.py` | `tests/test_security.py::test_tools_interface_is_read_only` |
| **S5** | Fail-closed posture: tool errors, missing claims, or low confidence force `ESCALATE`. Unhandled exceptions record no decision. | `src/triage/rules.py`, `src/triage/service.py` | `tests/test_rules.py`, `tests/test_store_service.py` |
| **S6** | Secrets managed via environment and `.env`. `.env` git-ignored; keys never logged or printed. | `src/triage/config.py`, `src/triage/logging_setup.py` | `tests/test_security.py::test_config_error_no_secret` |
| **S7** | Structured JSON logs with strict key allowlist. Zero raw text, notes, prompts, or model outputs in logs. | `src/triage/logging_setup.py` | `tests/test_security.py::test_logs_have_no_text` |
| **S8** | API requires Bearer authentication with constant-time comparison. Binds 127.0.0.1 by default. | `src/triage/api.py` | `tests/test_api.py::test_api_auth` |
| **S9** | Reviewer actions restricted to `CONFIRM` or `ESCALATE_FURTHER`. Reason required. No `DENY` or `APPROVE` actions. | `src/triage/models.py`, `src/triage/nodes.py` | `tests/test_models.py::test_review_action_schema` |
| **S10** | Decisions store is strictly append-only. No `UPDATE` or `DELETE` SQL operations exist. | `src/triage/store.py` | `tests/test_security.py::test_no_update_delete_on_decisions` |
| **S11** | Idempotent triage: re-triaging a claim with an active review thread reuses thread without duplicate threads. | `src/triage/service.py` | `tests/test_store_service.py::test_idempotent_triage` |
| **S12** | Purely synthetic test data. Zero PII/PHI (verified via email and phone regex scans). | `data/cases/*.json`, `data/policy/clauses.md` | `tests/test_security.py::test_synthetic_only` |
| **S13** | Strict dependency ban: no `langchain*`, `openai`, `anthropic`, or vector databases permitted. | `pyproject.toml`, codebase imports | `tests/test_security.py::test_import_ban` |
| **S14** | Offline replay mode never falls back to live network calls. Cassette miss raises `CassetteMiss`. | `src/triage/llm.py` | `tests/test_llm.py::test_cassette_miss_raises` |

---

## 4. Residual Risks & Operational Mitigations

1. **Subtle Euphemistic Injection (Novel Prompt Injection Techniques):**
   - *Risk:* An adversarial claim uses novel multi-lingual or metaphorical phrasing not caught by the 13 regex heuristics.
   - *Mitigation:* Even if injection bypasses `scan_for_injection()`, the LLM has zero routing authority. The LLM only extracts fields and evaluates clauses. If the extracted procedure code or dollar amount does not match the database claim record from MCP, Rule R11 triggers immediate `ESCALATE`.
2. **Denial of Service via Heavy Replay / Evaluation Jobs:**
   - *Risk:* Concurrent bulk triage invocations could consume memory or SQLite locks.
   - *Mitigation:* `TriageService` implements per-claim mutual exclusion locks (`threading.Lock`). Re-triaging an existing pending claim returns the active thread ID without spinning up duplicate LangGraph executions.
3. **Compromised Local Machine / SQLite Direct Access:**
   - *Risk:* An attacker with direct Linux shell access modifies SQLite `.db` files directly on disk.
   - *Mitigation:* Production deployments must run in containerized environments with read-only root filesystems and restricted file permissions on mounted persistent storage volumes (addressed in `Dockerfile`).
