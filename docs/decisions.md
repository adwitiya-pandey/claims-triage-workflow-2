# Architectural Decision Records (ADRs)

This document records the architectural and design decisions for `claims-triage-workflow` (Project B), capturing context, options considered, decisions taken, and consequences.

---

## ADR-001: Zero LLM Route Authority

- **Status:** Accepted
- **Context:** Large Language Models are prone to sycophancy, probabilistic variance, and indirect prompt injection attacks. Permitting the model to directly decide routes (`FAST_TRACK`, `STANDARD_REVIEW`, `ESCALATE`) risks severe financial exposure (e.g. paying high-dollar unapproved claims).
- **Decision:** The LLM is strictly prohibited from selecting or suggesting routes. The LLM is restricted to two isolated tasks: extracting structured entities (`extract_node`) and labeling policy clauses with verbatim quotes (`assess_node`). All routing decisions are executed solely by a pure Python rule engine (`rules.py`).
- **Consequences:** Even in cases of complete LLM jailbreak or adversarial prompt injection, the workflow cannot be tricked into `FAST_TRACK`. If LLM extracted fields disagree with verified database records, Rule R11 escalates the claim immediately.

---

## ADR-002: Deterministic Citation & Verbatim Quote Grounding

- **Status:** Accepted
- **Context:** LLMs frequently hallucinate policy clause numbers or invent plausible-sounding policy justifications.
- **Decision:** In `assess_node`, every cited policy chunk ID must exist in the retrieved BM25 chunk set. Furthermore, for any clause marked `supports` or `restricts`, the quote must be at least 20 characters long and must be a verbatim substring (normalized for whitespace and case) of the source clause text. Hallucinated or modified citations are dropped prior to decision rule evaluation.
- **Consequences:** Guarantees that only grounded, verifiable policy justifications influence routing logic. Dropped citations trigger audit detail codes (`dropped_citations`).

---

## ADR-003: Pure StateGraph Topology with Durable `interrupt()`

- **Status:** Accepted
- **Context:** Non-fast-track claims require human reviewer oversight before finalization. The system must support long pauses (minutes to days) while awaiting human action without tying up compute processes or losing workflow context.
- **Decision:** LangGraph 1.2+ `interrupt()` is used in `review_node` backed by `SqliteSaver`. Adhering to LangGraph Gotcha rules:
  1. The node constructs the review packet, calls `interrupt()`, and halts cleanly.
  2. Zero side effects (no database writes, no mutations) precede `interrupt()`.
  3. Resume is executed via `Command(resume=...)` targeting the same thread.
  4. Reviewer actions are constrained to `CONFIRM` or `ESCALATE_FURTHER`; human reviewers cannot approve claims or bypass safety gates.
- **Consequences:** Workflows can be stopped, restarted, or resumed across separate operating system processes and SQLite connections without state loss.

---

## ADR-004: Read-Only Isolated MCP Client Interface

- **Status:** Accepted
- **Context:** Claims facts (member records, coverage rules, prior claims history, clinical notes) reside in Project A (`claims-mcp-server`). Project B must not create tight coupling or violate service boundaries.
- **Decision:** Project A is treated as a separate, read-only external service. Project B never imports Project A's codebase. All communication occurs over streamable HTTP with Bearer token authentication and client-side exponential backoff jitter on `RATE_LIMITED` responses.
- **Consequences:** Decouples deployments and ensures that Project B cannot execute write operations against the core claims database.

---

## ADR-005: Offline Cassette Replay & Deterministic Evaluation Gates

- **Status:** Accepted
- **Context:** Automated CI/CD pipelines require reproducible, deterministic test runs that execute without external API keys, rate limits, or network failures.
- **Decision:** A custom cassette record/replay harness (`llm.py`) hashes the exact canonical JSON payload of prompts, models, and schemas. In `replay` mode, cassette lookups are strictly offline; cassette misses fail loudly (`CassetteMiss`) rather than silently falling back to live network calls.
- **Consequences:** CI runs offline with zero API key dependencies and zero flakiness. All evaluation metrics (M1 through M8) are evaluated against hard gates in `evals/gates.json`.

---

## ADR-006: Immutable Append-Only Auditing

- **Status:** Accepted
- **Context:** Healthcare regulatory standards (e.g. HIPAA, CMS audit rules) mandate immutable decision records.
- **Decision:** The SQLite `decisions` table permits `INSERT` statements only. `UPDATE` and `DELETE` operations are strictly banned by schema design and verified by automated unit tests. Versioning is monotonic, and each record captures SHA-256 state hashes, rule hits, and full execution trails.
- **Consequences:** Tamper-evident, fully reproducible audit history for every processed claim.

---

## ADR-007: Structured Workflow Telemetry & Information Disclosure Defense

- **Status:** Accepted
- **Context:** Production agentic AI systems require visibility into node latencies, token consumption, and cost telemetry without leaking sensitive patient health information (PHI).
- **Decision:** Every node transition in `graph.py` emits structured JSON telemetry (`timestamp`, `run_id`, `claim_id`, `node_name`, `latency_ms`, `token_usage`, `cost_usd`, `guardrail_triggered`, `final_route`). A strict logging formatter (`StructuredJsonFormatter`) enforces a strict metadata allowlist (`ALLOWED_LOG_KEYS`) and strips raw claim text, clinical notes, and prompts.
- **Consequences:** Complete operational cost monitoring and SIEM compatibility while achieving strict HIPAA/PHI confidentiality.
