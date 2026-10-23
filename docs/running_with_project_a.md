# Running Claims Triage Workflow with Project A (MCP Server)

This guide provides step-by-step instructions to integrate Project B (`claims-triage-workflow`) with Project A (`claims-mcp-server`) over streamable HTTP.

---

## Prerequisites
- Both repositories cloned adjacent to each other:
  - Project A: `~/projects/claims-mcp-server`
  - Project B: `~/projects/claims-triage-workflow-2`
- Python 3.12+ and `uv` installed.

---

## Step 1: Build the Scenario Database
From the Project B repository root (`~/projects/claims-triage-workflow-2`):
```bash
uv run python scripts/build_scenario_db.py --out data/scenario.db
```
This builds `data/scenario.db` containing the synthetic claims, members, plans, coverage rules, and notes corresponding to the 43 test scenario cases in `data/cases/*.json`.

---

## Step 2: Configure Project A with Scenario Data & API Key
Switch to Project A directory:
```bash
cd ~/projects/claims-mcp-server
```

Generate a dedicated API key for Project B with access strictly restricted to the 5 read-only tools:
```bash
uv run python scripts/make_key.py --id triage-workflow --tools get_claim,list_member_claims,get_coverage_rule,get_member_ytd_summary,get_claim_notes
```
Copy the printed plaintext key (e.g. `sec_abc123...`).

---

## Step 3: Start Project A Server over HTTP
Launch Project A over streamable HTTP, pointing to the scenario database:
```bash
CLAIMS_DB_PATH=~/projects/claims-triage-workflow-2/data/scenario.db \
CLAIMS_HTTP_PORT=8000 \
uv run python -m claims_mcp --transport http
```
The server will bind to `http://127.0.0.1:8000/sse`.

---

## Step 4: Run the Contract Integration Test
In a separate terminal, return to Project B:
```bash
cd ~/projects/claims-triage-workflow-2
```

Export the MCP connection details and run the integration contract tests:
```bash
CLAIMS_MCP_URL="http://127.0.0.1:8000/sse" \
CLAIMS_MCP_KEY="<PLAINTEXT_KEY_FROM_STEP_2>" \
uv run pytest tests/test_contract_mcp.py -v -m integration
```

This verifies that live responses from Project A match `FixtureToolsBackend` for all 43 scenario cases across every tool method.
