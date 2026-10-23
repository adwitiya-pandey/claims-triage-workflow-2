# Claims Triage Workflow (Project B)

[![CI](https://github.com/adwitiya-pandey/claims-triage-workflow-2/actions/workflows/ci.yml/badge.svg)](https://github.com/adwitiya-pandey/claims-triage-workflow-2/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/badge/managed%20by-uv-orange.svg)](https://github.com/astral-sh/uv)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Security: STRIDE Covered](https://img.shields.io/badge/security-STRIDE%20audited-green.svg)](THREAT_MODEL.md)

Production-grade agentic claims triage workflow built with **LangGraph**, **Pydantic v2**, and **MCP (Model Context Protocol)** for a fictional health-insurance Third-Party Administrator (TPA). Given a claim submission, the workflow evaluates policy rules, validates clinical facts, and recommends a route (`FAST_TRACK`, `STANDARD_REVIEW`, or `ESCALATE`), pausing durably for human oversight on all non-fast-track claims while maintaining an immutable audit trail.

---

## 1. System Architecture

The workflow enforces a zero-trust architecture where **the Large Language Model never chooses the route**. LLMs extract entities and label clauses; deterministic Python rules alone determine workflow routing.

```mermaid
flowchart TD
    subgraph INTAKE_BOUNDARY ["1. Intake & Ingestion Layer"]
        START([Claim Submission]) --> intake["intake\n(Sanitize control chars & scan 13 injection regexes)"]
        intake --> extract["extract\n(Gemini 3.8 Flash via strict Pydantic schema)"]
    end

    subgraph MCP_BOUNDARY ["2. External MCP Fact Layer (Project A)"]
        extract --> fetch_facts["fetch_facts\n(Queries claims-mcp-server over HTTP)"]
        mcp_server[("Project A: Claims MCP Server\n- get_claim\n- list_member_claims\n- get_coverage_rule\n- get_member_ytd_summary\n- get_claim_notes")]
        fetch_facts <-->|Read-Only HTTP / SSE| mcp_server
    end

    subgraph POLICY_LAYER ["3. Policy Retrieval & Assessment"]
        fetch_facts --> retrieve["retrieve\n(BM25 ranking over 30 policy clauses)"]
        retrieve --> assess["assess\n(Gemini labels clauses + strict citation check)"]
    end

    subgraph RULE_GATE ["4. Deterministic Guardrail Policy Gate"]
        assess --> decide{"decide\n(Deterministic pure Python rules R01-R18)"}
    end

    subgraph HUMAN_GATE ["5. Human Review Oversight"]
        decide -->|FAST_TRACK| finalize["finalize\n(Idempotent SQLite decision store)"]
        decide -->|STANDARD_REVIEW or ESCALATE| review["review\n(LangGraph durable interrupt() checkpoint)"]
        reviewer([Human Reviewer]) -->|CONFIRM or ESCALATE_FURTHER| review
        review --> finalize
    end

    subgraph PERSISTENCE ["6. Storage & Telemetry"]
        finalize --> END([Workflow Complete])
        finalize -.-> sqlite_db[("SQLite triage.db\nAppend-Only decisions")]
        finalize -.-> telemetry[("Structured JSON Telemetry\nCost, Latency, Token Usage")]
    end

    classDef node fill:#f9f9f9,stroke:#333,stroke-width:1.5px;
    classDef gate fill:#fff3cd,stroke:#856404,stroke-width:2px;
    classDef mcp fill:#e2e3e5,stroke:#383d41,stroke-width:1.5px;
    class intake,extract,fetch_facts,retrieve,assess,review,finalize node;
    class decide gate;
    class mcp_server,sqlite_db,telemetry mcp;
```

---

## 2. Repository Structure

```
claims-triage-workflow-2/
├── .github/
│   └── workflows/
│       └── ci.yml               # Automated CI: ruff lint, format, pytest, oracle & replay eval
├── data/
│   ├── cases/                   # 43 synthetic scenario test cases (JSON)
│   │   ├── case-01.json         # Clean fast-track scenario
│   │   ├── case-14.json         # Coverage rule unknown (escalate)
│   │   ├── case-20.json         # High dollar threshold (escalate)
│   │   ├── case-34.json         # Prompt injection in submission text
│   │   ├── case-38.json         # Adversarial prompt injection in clinical notes
│   │   └── ...                  # 43 total scenarios covering all categories
│   ├── cassettes/               # Deterministic LLM response cassettes
│   │   ├── cassette.jsonl       # 85 canonical recorded requests/responses
│   │   └── test_cassette.jsonl  # Fixture for unit testing cassette replay
│   ├── policy/                  # Policy corpus and requirements
│   │   ├── clauses.md           # 30 health insurance policy clauses
│   │   └── required_documents.json # Document requirements per procedure code
│   └── schema_from_project_a.sql# Schema definition copied from Project A
├── docs/
│   ├── EXPLAIN-PLAIN-ENGLISH.md # Plain-English explanation of the LangGraph workflow
│   ├── decisions.md             # Architectural Decision Records (ADRs)
│   ├── eval_findings.md         # Detailed oracle vs replay evaluation findings report
│   ├── running_with_project_a.md# Integration runbook for running adjacent to Project A
│   └── sdk_notes.md             # Framework facts verified against installed packages
├── evals/
│   ├── gates.json               # CI pass/fail threshold criteria for M1 through M8
│   ├── run_eval.py              # Quantitative evaluation engine (oracle / replay / live)
│   └── reports/                 # Evaluation test output summaries (JSON and Markdown)
├── prompts/
│   ├── assess_v1.txt            # Versioned prompt template for policy assessment
│   └── extract_v1.txt           # Versioned prompt template for entity extraction
├── scripts/
│   ├── build_scenario_db.py     # Compiles data/cases/*.json into data/scenario.db
│   └── record_cassettes.py      # Records Gemini LLM calls into data/cassettes/cassette.jsonl
├── src/
│   └── triage/
│       ├── __init__.py          # Package root
│       ├── __main__.py          # CLI entry point for uvicorn server
│       ├── api.py               # FastAPI REST endpoints with Bearer auth & error masking
│       ├── config.py            # Pydantic/dataclass configuration and secret handling
│       ├── graph.py             # LangGraph StateGraph topology, routing, and telemetry
│       ├── injection.py         # 13 regex heuristics + zero-width character scanner
│       ├── llm.py               # Canonical JSON hashing, Gemini client, cassette replay
│       ├── logging_setup.py     # Structured JSON log formatter (S7 PHI/PII protection)
│       ├── models.py            # Strict Pydantic v2 schemas (extra="forbid")
│       ├── nodes.py             # 8 workflow node implementations (intake to finalize)
│       ├── retrieval.py         # BM25 policy clause ranking and grounding engine
│       ├── rules.py             # Deterministic pure Python routing rule engine
│       ├── sanitize.py          # Control character stripping and length limiting
│       ├── service.py           # TriageService orchestrator, locking, and idempotency
│       ├── state.py             # LangGraph TriageState schema (plain JSON-serializable)
│       ├── store.py             # Append-only SQLite store with SHA-256 state hashing
│       └── tools/
│           ├── base.py          # ClaimsTools typing protocol & ToolError definitions
│           ├── fixture_backend.py # In-memory / JSON fixture backend for offline tests
│           └── mcp_backend.py   # Streamable HTTP MCP client with backoff retry
├── tests/
│   ├── test_api.py              # FastAPI endpoint auth, routes, and error handling
│   ├── test_contract_mcp.py     # Live Project A MCP integration contract tests
│   ├── test_eval_gates.py       # Automated eval gate checks during pytest
│   ├── test_graph.py            # LangGraph topology, pause/resume, and restart safety
│   ├── test_injection.py        # 13 regex injection test tables
│   ├── test_llm.py              # Canonical request hashing and cassette replay tests
│   ├── test_logging_telemetry.py# Structured JSON telemetry and token cost verification
│   ├── test_models.py           # Pydantic model validation and ID regex tests
│   ├── test_nodes_assess.py     # Policy assessment node and verbatim quote checks
│   ├── test_nodes_extract.py    # Entity extraction and retry behavior tests
│   ├── test_nodes_fetch_facts.py# MCP error code mapping and note scanning tests
│   ├── test_retrieval.py        # BM25 determinism and tie-breaking tests
│   ├── test_rules.py            # All 18 deterministic rule scenarios
│   ├── test_sanitize.py         # Text sanitization and character stripping tests
│   ├── test_security.py         # S1-S14 security requirements verification
│   └── test_store_service.py    # Append-only SQLite, versioning, and idempotency tests
├── AGENTS.md                    # Autonomous workflow contract and developer constraints
├── PROGRESS.md                  # Task-by-task execution and completion status
├── pyproject.toml               # Project dependencies, build config, and ruff settings
├── SPEC.md                      # Complete engineering specification and data contracts
├── TASKS.md                     # Step-by-step implementation tasks (T0 through T19)
└── THREAT_MODEL.md              # Full STRIDE security analysis and trust boundary audit
```

---

## 3. Core Invariants & Security Guarantees

1. **The LLM Never Chooses the Route:** Only `rules.py` chooses a route as a pure mathematical function of verified facts and sanitized assessments. LLM summaries and rationales are never fed into routing logic.
2. **Deterministic Citation Verification:** In `assess_node`, every cited policy clause must exist in retrieved chunks, and quotes supporting or restricting coverage must be at least 20 characters long and match the source text verbatim. Hallucinated quotes are dropped before rule evaluation.
3. **Indirect Prompt-Injection Defense:** Clinical notes fetched from MCP and patient submission text are scanned across 13 adversarial patterns and sanitized for zero-width unicode characters before prompt injection. Any flagged claim triggers immediate escalation to human review.
4. **Append-Only Decision Store:** The SQLite `decisions` table allows `INSERT` operations only. `UPDATE` and `DELETE` operations are strictly forbidden to ensure immutable regulatory audit trails.
5. **Durable Human Oversight:** LangGraph `interrupt()` pauses all non-fast-track claims durably in SQLite (`checkpoint.db`). Claims resume cleanly across process restarts using `Command(resume=...)` without side effects.
6. **Structured Telemetry & PHI Protection:** Logs strictly adhere to `ALLOWED_LOG_KEYS`. Raw claim text, clinical notes, and model prompts are barred from log output.

---

## 4. Evaluation Results & Honest Limits

Quantitative evaluation runs across **43 labeled synthetic cases** using `evals/run_eval.py` against strict CI gates in `evals/gates.json`:

| Metric ID | Description | CI Gate Threshold | Oracle Mode | Replay Mode | Status |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **M1** | **False Fast-Track Rate** | $\le 0.0\%$ (Strict Zero) | **0.00%** | **0.00%** | **PASS** |
| **M2** | Route Accuracy | $\ge 95.0\%$ | **100.00%** | **100.00%** | **PASS** |
| **M3** | Over-Escalation Rate | Informational ($\le 25.0\%$) | **0.00%** | **0.00%** | **INFO** |
| **M4** | Injection Containment | $= 100.0\%$ (Strict) | **100.00%** | **100.00%** | **PASS** |
| **M5** | Citation Validity | $\ge 95.0\%$ | **100.00%** | **100.00%** | **PASS** |
| **M6** | Rule-ID Match | $\ge 95.0\%$ | **100.00%** | **100.00%** | **PASS** |
| **M7** | Extraction Field Accuracy | Informational | **100.00%** | **100.00%** | **INFO** |
| **M8** | Reason Coverage | $= 100.0\%$ (Strict) | **100.00%** | **100.00%** | **PASS** |

### Honest Evaluation Limits (Disclosures):
- **Oracle Mode:** Feeds gold extractions and assessments directly into the rule engine and graph topology. It proves that rules, state reducers, and gates function with mathematical precision, but does not evaluate model capability. $M1 = 0.0\%$ is expected by construction.
- **Replay Mode:** Evaluates recorded Gemini LLM output captured in `data/cassettes/cassette.jsonl`. It confirms real LLM parsing and citation extraction without external network dependencies.
- **Synthetic Cases:** The 43 scenarios prove performance against defined edge cases and adversarial attacks, but do not represent a production clinical distribution.
- **Consistency vs. Ground Truth:** Expected labels are derived from the rule specification; evaluation measures compliance against the specification rather than independent clinical consensus.

---

## 5. Quickstart & Operational Runbook

### Prerequisites
- Linux / WSL2 Ubuntu with Python 3.12+ installed.
- `uv` package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

### Installation
```bash
# Clone repository
git clone https://github.com/adwitiya-pandey/claims-triage-workflow-2.git
cd claims-triage-workflow-2

# Install dependencies into virtual environment
uv sync
```

### Running Offline Quality Checks
```bash
# 1. Lint and code formatting
uv run ruff check .
uv run ruff format --check .

# 2. Run the 100% offline unit test suite (141 tests)
uv run pytest -q -m "not integration"

# 3. Run deterministic replay evaluation against CI gates
uv run python -m evals.run_eval --mode replay

# 4. Run oracle baseline evaluation
uv run python -m evals.run_eval --mode oracle
```

### Running the API Server
```bash
# Copy environment configuration
cp .env.example .env

# Start FastAPI server on port 8800
TRIAGE_API_KEY="sec_local_dev_key_123" uv run python -m triage --port 8800
```

### Interacting with the API
```bash
# Health check
curl -s http://127.0.0.1:8800/health

# Trigger triage for a claim
curl -s -X POST http://127.0.0.1:8800/triage \
  -H "Authorization: Bearer sec_local_dev_key_123" \
  -H "Content-Type: application/json" \
  -d '{"claim_id": "CLM70000001"}'

# List claims awaiting human review
curl -s http://127.0.0.1:8800/reviews/pending \
  -H "Authorization: Bearer sec_local_dev_key_123"

# Submit human review decision (durable LangGraph resume)
curl -s -X POST http://127.0.0.1:8800/reviews/<THREAD_ID> \
  -H "Authorization: Bearer sec_local_dev_key_123" \
  -H "Content-Type: application/json" \
  -d '{
    "reviewer_id": "rev10001",
    "action": "CONFIRM",
    "reason": "Verified clinical records and confirmed route."
  }'
```

### Running with Project A (MCP Server Integration)
To verify contract parity against live Project A (`claims-mcp-server`):
```bash
# 1. Build the scenario database
uv run python scripts/build_scenario_db.py --out data/scenario.db

# 2. In an adjacent terminal, launch Project A over HTTP
cd ~/projects/claims-mcp-server
CLAIMS_DB_PATH=~/projects/claims-triage-workflow-2/data/scenario.db \
CLAIMS_RATE_LIMIT_PER_MIN=10000 \
uv run python -m claims_mcp --transport http --port 8000

# 3. Return to Project B and run integration contract test
cd ~/projects/claims-triage-workflow-2
CLAIMS_MCP_URL="http://127.0.0.1:8000/mcp" \
CLAIMS_MCP_KEY="<PLAINTEXT_KEY>" \
uv run pytest tests/test_contract_mcp.py -m integration -v
```

---

## 6. Telemetry & Cost Telemetry Specification

Every workflow node transition in `graph.py` logs structured JSON containing execution latency, token expenditure, and cost estimates based on Google Gemini pricing ($0.075 / 1M input tokens, $0.30 / 1M output tokens):

```json
{
  "timestamp": "2026-10-23T15:20:43.856007+00:00",
  "run_id": "thread-test-telemetry",
  "claim_id": "CLM70000001",
  "node_name": "assess",
  "latency_ms": 2.87,
  "token_usage": {
    "input_tokens": 340,
    "output_tokens": 110
  },
  "cost_usd": 0.000059,
  "guardrail_triggered": false,
  "final_route": null
}
```

---

## 7. Security & Threat Modeling

For complete details on threat classification, trust boundary auditing, and security requirement cross-references, see [THREAT_MODEL.md](THREAT_MODEL.md).
