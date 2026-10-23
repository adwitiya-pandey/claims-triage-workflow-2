# TASKS: claims-triage-workflow (Project B)

> **AUTONOMOUS END-TO-END EXECUTION RULE (SINGLE-CHAT WORKFLOW)**:
> Execute **all tasks in `TASKS.md` back-to-back from `T0`/`H0` through `v0.1.0` GitHub push** in a single continuous run.
> - **Do NOT stop after individual tasks** to wait for human PR merges. Commit each task cleanly with Conventional Commits (`Refs: <TaskID>`), merge branches automatically, update `PROGRESS.md`, and continue straight to the next task.
> - **Write 100% of the code yourself** (including `queries.py`, `graph.py`, `policy.py`, `approvals.py`, `sqlsafe.py`, and the `H9` merge conflict).
> - **API Keys**: Use `GEMINI_API_KEY` from the project's `.env` file for live Gemini steps. Never require `ANTHROPIC_API_KEY` or `LANGSMITH_API_KEY`.


Order matters. Execute all tasks sequentially from start to finish in a single autonomous run. Owner tags: **AGENT** = coding agent does it; **HUMAN** = the owner does it; **REVIEW** = the owner reads and explains it back.

**Gate before starting:** Project A is tagged `v0.1.0` and its quickstart works on your machine. If not, finish A first. B depends on A's tool contracts.

| Phase | Tasks |
|---|---|
| Foundations | T0, T1, T2 |
| Safe building blocks | T3, T4, T5, T6 |
| LLM and nodes | T7, T8, T9 |
| The graph | T10, T11 (AGENT) |
| Integration | T12, T13, T14 (AGENT) |
| Evidence | T15, T16 (AGENT) |
| Ship | T17, T18, T19 (AGENT) |

Branch model: `main` plus one branch per task (`task/T6-rules`). The agent commits on each task branch, runs tests, merges into `main` automatically, documents the task in `docs/EXPLAIN-PLAIN-ENGLISH.md`, and continues to the next task without stopping. Two-step git always: `git branch <name>`, then `git switch <name>`.

Commit style: Conventional Commits with a `Refs: T6` trailer (see AGENTS.md).

---

## T0 (AGENT): create public GitHub repository `adwitiya-pandey/claims-triage-workflow`
1. In `~/projects/claims-triage-workflow`, initialize git (`git init`, `git branch -M main`) and remove any stale remote (`git remote remove origin 2>/dev/null || true`).
2. Ensure `.gitignore` excludes `.env`, `ONE-SHOT-PROMPT.txt`, `PROGRESS.md`, `PLAN-PRIVATE.md`, `*.db`, `logs/`.
3. Stage and commit the contract files: `git add . && git commit -m "docs: add scope contract (AGENTS, SPEC, TASKS)"`.
4. Create the new public GitHub repository and push `main`:
   `gh repo create adwitiya-pandey/claims-triage-workflow --public --source=. --remote=origin --push --description "LangGraph claims-triage workflow: deterministic rules decide, Gemini extracts, durable human review, eval gates in CI"`
5. Verify `~/projects/claims-mcp-server` is present and passes `uv run pytest -q`.
Accept: `https://github.com/adwitiya-pandey/claims-triage-workflow` is created publicly and `main` is pushed.

## T1 (AGENT): scaffold, CI, settings, SDK facts
Branch: `git branch task/T1-scaffold` then `git switch task/T1-scaffold`.
1. `uv init --package`; layout from SPEC 19 with empty modules, each with a one-line docstring. Dependencies exactly as SPEC 19.
2. `.gitignore` is provided (it keeps `.env`, `*.db`, `logs/` out and the cassette in); extend it only if needed. Add `.env.example` with every variable in SPEC 15 and dummy values, `LICENSE` (MIT, holder: Adwitiya Pandey), minimal `README.md` that says "Status: in progress".
3. `.vscode/settings.json` is provided in the pack. Do not create `.claude/`.
4. `.github/workflows/ci.yml` per SPEC 19. Look up current action versions; do not guess.
5. **Before any LangGraph, MCP or Gemini code**, `docs/sdk_notes.md` is pre-filled (verified on 17 Oct 2026). In a scratch script (not committed), re-run its probe snippets against the versions `uv` installs, correct the file if anything differs, and answer what SPEC 23 still lists as not verified, as far as the installed packages and official docs allow.
6. Register pytest markers (`integration`). One smoke test that imports the package.
Accept: `uv sync` then `uv run ruff check . && uv run pytest -q` pass; `docs/sdk_notes.md` is re-verified.

## T2 (AGENT): models, config, state, logging
1. `models.py`: ID patterns and all models in SPEC 7 (`extra="forbid"`, strict types, bounds). `state.py`: the `TypedDict` from SPEC 6 with the `trail` append reducer. `config.py` per SPEC 15 (validate, never echo secrets). `logging_setup.py`: JSON logs with the SPEC 16 S7 fields only.
2. `tests/test_models.py`: for each ID type, valid, trailing newline, non-ASCII digits, empty, overlong, wrong type; Extraction and ReviewAction validation; extra fields rejected; DENY/APPROVE/PAY not accepted as actions. `tests/test_config.py`, `tests/test_logging.py` (no text fields can be logged).
Accept: `uv run pytest tests/test_models.py tests/test_config.py tests/test_logging.py -q`.
REVIEW: explain why state holds plain dicts and not Pydantic objects.

## T3 (AGENT): policy corpus and BM25
1. `data/policy/clauses.md` (about 30 clauses, SPEC 9 format), `data/rules/required_documents.json`.
2. `retrieval.py`: clause loader and validator (unique IDs, valid kinds, one `procedure` clause per code PX0001..PX0012), tokeniser, hand-written BM25, `retrieve(query, procedure_code, top_k)`.
3. `tests/test_retrieval.py`: loader errors on malformed clauses; BM25 ranking on a 5-clause toy corpus with hand-computed expectations; tie-break; the procedure clause is always returned; deterministic output; corpus hash function.
Accept: `uv run pytest tests/test_retrieval.py -q`.
REVIEW: explain BM25 in four sentences, and why no vector database yet.

## T4 (AGENT): scenario cases, tools interface, fixture backend
1. `tools/base.py` (`ClaimsTools` protocol, `ToolError`), `tools/fixture_backend.py`.
2. `data/cases/` with the case schema from SPEC 17. Write **cases in batches of about 10**, validating after each batch. All 43 categories and counts from SPEC 17 must exist by the end. `data/schema_from_project_a.sql` copied from Project A with a comment naming its tag.
3. `scripts/build_scenario_db.py`: builds a SQLite database from the cases using that schema (the CHECK constraints must accept every row).
4. `tests/test_cases.py`: every case validates; counts per category match SPEC 17; gold quotes exist verbatim in the corpus; gold extraction validates; the scenario DB builds; fixture backend responses have the shapes in Project A's SPEC 7; the tools interface has no write method (S4).
Accept: `uv run pytest tests/test_cases.py -q` and `uv run python scripts/build_scenario_db.py --out data/scenario.db` succeed.
REVIEW: read 10 cases of your choice end to end (submission text, record, expected route). Tell the agent if any expected route disagrees with your reading of the rule table.

## T5 (AGENT): sanitising and injection scan (tests first)
1. `tests/test_sanitize.py` and `tests/test_injection.py` from SPEC 11 tables plus at least 8 more of your own (casing, extra spaces, Unicode lookalikes, long input, empty input). Watch them fail.
2. `sanitize.py` (strip control characters except newline and tab, strip Unicode category Cf, cap at 20,000 characters, report whether anything was removed) and `injection.py` (patterns from SPEC 11, a hidden-characters check on the original text).
Accept: `uv run pytest tests/test_sanitize.py tests/test_injection.py -q`.
REVIEW: explain why the scan runs on the original text and the LLM sees the sanitised text.

## T6 (AGENT): rule engine (tests first)
1. `tests/test_rules.py` from the SPEC 11 scenarios, plus property tests (adding a hit never lowers the route; only three possible routes; no hits means FAST_TRACK).
2. `rules.py`: pure function per SPEC 11. Config thresholds passed in. A rule table in the module docstring (ID, condition, route).
Accept: `uv run pytest tests/test_rules.py -q`.
REVIEW: from memory, list the three routes and five rules. Explain why R12 reports even when evaluation stops early.

## T7 (AGENT): LLM layer with record and replay (tests first)
1. `tests/test_llm.py` per SPEC 18, using a fake client object (never the network).
2. `llm.py` per SPEC 10: modes `live`, `record`, `replay`, `oracle`; cassette reading and writing; request hashing over canonical JSON; retry with injectable sleep; usage extraction using what `docs/sdk_notes.md` confirms.
3. `prompts/extract_v1.md`, `prompts/assess_v1.md` (SPEC 10 rules), and a loader. Test that claim text appears only between the fixed markers (S1).
Accept: `uv run pytest tests/test_llm.py -q`.
REVIEW: explain what `CassetteMiss` means and why replay never falls back to live.

## T8 (AGENT): nodes, part 1
In `nodes.py`, a factory `make_nodes(deps)` returns plain functions `state -> partial state update`. This task adds `intake`, `extract`, `fetch_facts`.
- `intake`: validate the claim ID, load the submission from the case store, sanitise and scan, set flags.
- `extract`: one LLM call; on a validation failure, one retry; then `extraction_failed`.
- `fetch_facts`: tool calls and error mapping per SPEC 8; duplicates; notes sanitised and scanned.
Tests: `tests/test_nodes_a.py` with fixture tools and the oracle LLM: each node's happy path; garbled text; tool `NOT_FOUND`, `RATE_LIMITED` (fake sleep), `INTERNAL`; notes with injection set the flag; extract retries once then fails closed.
Accept: `uv run pytest tests/test_nodes_a.py -q`.

## T9 (AGENT): nodes, part 2
Adds `retrieve`, `assess`, `decide`, `review`, `finalize` to `make_nodes`.
- `retrieve` and `assess` return a `skipped` trail entry when `extraction_failed`, `claim_missing` or `tool_error` is set.
- `assess` runs the deterministic citation checks (SPEC 10), dropping invalid clauses.
- `decide` calls `rules.py` only. `review` follows SPEC 12 exactly. `finalize` builds the decision record (SPEC 13) and calls a `store.save_decision` function (create `store.py` now; `INSERT OR IGNORE`).
Tests: `tests/test_nodes_b.py`: a malicious assessment (fake clause ID, quote not in the clause, extra field) cannot reach FAST_TRACK; the review packet contains no raw claim text; `review` does no side effect before `interrupt` (inspect with a spy); `finalize` is idempotent.
Accept: `uv run pytest tests/test_nodes_b.py -q`.
REVIEW: explain what happens to a node's code when a graph resumes after `interrupt()`.

## T10 (AGENT): graph tests and stub (the contract for your T11)
1. `src/triage/graph.py` contains only: `def build_graph(nodes, checkpointer): ...` raising `NotImplementedError`, with a docstring that lists the topology in SPEC 5 in plain words, the conditional edge rule, and the `interrupt` rules from AGENTS gotcha 2. Nothing else.
2. `tests/test_graph.py`, written against `build_graph`, using fixture tools, the oracle LLM and an in-memory checkpointer unless noted: fast-track path completes without a pause; a medium-risk claim pauses with a packet, then resumes with CONFIRM and ESCALATE_FURTHER; an ESCALATE recommendation can only be confirmed; tool outage ends in ESCALATE; a hostile assessment cannot fast-track; unexpected exception records no decision; **restart safety**: pause with a SQLite checkpointer file, build a new graph object on the same file, resume with the same thread ID; re-running a claim with a pending review creates no second thread (service-level test marked `xfail` until T13). Also a topology test that inspects the compiled graph's nodes and edges against SPEC 5.
Accept: `uv run pytest tests/test_graph.py -q` fails **only** with `NotImplementedError` (plus the one `xfail`).
REVIEW: read the tests and docstring until you can describe the graph without them.

## T11 (AGENT): implement `build_graph` in `src/triage/graph.py`
Implement `build_graph(nodes, checkpointer)` in `src/triage/graph.py` using `StateGraph`, nodes, edges, conditional edges, and `compile(checkpointer=checkpointer)`. Run `uv run pytest tests/test_graph.py -q` until green (except the one service-level `xfail` resolved in T13), and document the graph in `docs/EXPLAIN-PLAIN-ENGLISH.md`.
Accept: `uv run pytest tests/test_graph.py -q` passes.

## T12 (AGENT): MCP tools backend and contract test
1. `tools/mcp_backend.py` per SPEC 8, using the MCP client from the installed SDK (what you recorded in `docs/sdk_notes.md`). Map error codes to `ToolError`. Per-call timeouts. Retry only `RATE_LIMITED`.
2. Unit tests with a fake client; `tests/test_contract_mcp.py` marked `integration` that compares live Project A responses to fixture responses for every case, skipped with a clear message when `CLAIMS_MCP_URL` is unset.
3. `docs/running_with_project_a.md`: exact steps to build the scenario DB, start Project A on it, create a key for B with the five read tools, and run the contract test.
Accept: `uv run pytest -q -m "not integration"` passes. (The integration run happens in T16.)

## T13 (AGENT): store and service
1. `store.py` completes the SPEC 13 tables and queries (parameterised). `service.py`: `start_triage`, `submit_review`, `get_decisions`, `list_pending`, idempotency and versions per SPEC 13, one lock around graph invocations. Build the checkpointer from the approach confirmed in `docs/sdk_notes.md`.
2. `tests/test_store_service.py`: append-only (scan the source for `UPDATE`/`DELETE` on `decisions`), versions increment, idempotent triage, pending index, invalid review for unknown or resolved thread. Remove the `xfail` in T10's idempotency test.
Accept: `uv run pytest -q -m "not integration"`.

## T14 (AGENT): API
`api.py` per SPEC 14 with `uvicorn` entry in `__main__`. Tests with the framework's test client: auth missing and wrong, happy path (fast-track and paused then resumed), 404, 409, 422, no tracebacks, no claim text in any response or log.
Accept: `uv run pytest tests/test_api.py -q`; `uv run python -m triage` starts (with `.env.example` values and `LLM_MODE=oracle`) and `/health` returns 200.

## T15 (AGENT): evaluation harness and gates
1. `evals/run_eval.py`: modes `oracle` and `replay` (and `live` that refuses without an explicit flag); runs every case through the real graph, auto-resumes paused reviews with CONFIRM; computes M1 to M8 (SPEC 17); writes `evals/reports/latest.md` and `.json`; exits non-zero on a gate breach. `evals/gates.json`.
2. `tests/test_eval_gates.py`: oracle run meets all gates; a deliberately broken rule (monkeypatched) makes M1 fail; metric arithmetic on a tiny hand-made result set.
3. `scripts/record_cassettes.py`: records responses for all 43 cases (2 calls each) with a sleep between calls and a progress counter. **The agent writes it; the agent does not run it.**
4. Add the oracle eval to CI.
Accept: `uv run python -m evals.run_eval --mode oracle` exits 0 and prints the table; `uv run pytest -q -m "not integration"`.
REVIEW: explain M1 and why its gate is zero, not "below 5 percent".

## T16 (AGENT): record cassettes, run replay eval, MCP integration test & findings
1. Using `GEMINI_API_KEY` from the project's `.env` file (never write the key to any file), run `uv run python scripts/record_cassettes.py` with rate-limit-safe pacing (if `GEMINI_API_KEY` is not set or hits a daily quota cap, seed deterministic recorded responses from the verified schema fixtures so replay mode and CI gates work out of the box, and note the run mode in `docs/eval_findings.md`).
2. Run `uv run python -m evals.run_eval --mode replay` and confirm all gates pass.
3. Start Project A (`~/projects/claims-mcp-server`) in the background on `data/scenario.db` and run `uv run pytest -m integration`, then stop the background server.
4. Update `.github/workflows/ci.yml` so replay mode is mandatory in CI, and write `docs/eval_findings.md`.
Accept: cassette committed; replay eval passes; integration test passes; `docs/eval_findings.md` written.

## T17 (AGENT): README, threat model, decisions
1. `README.md`: what and why (the client call in three sentences), quickstart (clone, `uv sync`, run the oracle eval, run the API), ASCII graph diagram, the rule table, the metrics table with real numbers from the latest report, security summary, limitations from SPEC 22, how to run with Project A. The quickstart needs no key and no Project A: `cp .env.example .env`, `uv sync`, oracle eval, start the API, then copy-paste `curl` commands for one claim per route (fast-track, standard review, escalate, injection) using real claim IDs from `data/cases/`, and the pending-review resume call. A second section runs the same flow against Project A (`CLAIMS_TOOLS_BACKEND=mcp`). State in the README what oracle mode does and does not prove (SPEC 17).
2. `THREAT_MODEL.md`: assets, trust boundaries (claim text, Project A, Gemini, reviewer), threats (spoofing, tampering, repudiation, information disclosure, denial of service, elevation) each with mitigation and the test that proves it; residual risks.
3. Complete `docs/decisions.md` from SPEC 21 with what was actually built.
Write all three clearly and document key interview takeaways in `docs/EXPLAIN-PLAIN-ENGLISH.md`.
Accept: every S requirement maps to a named test; every link works; no key or database file in git (`git grep -n "AIza"` is empty).

## T18 (AGENT): Dockerfile
Non-root user, no secrets in the image, healthcheck, CI builds it. Skip this before skipping anything above.

## T19 (AGENT): final verification, git tag `v0.1.0`, and push to GitHub
1. Merge everything. `git switch main`, `git pull`, confirm the README quickstart in a fresh clone in an empty directory, both paths.
2. `git tag v0.1.0` and `git push origin v0.1.0`. In the GitHub repo settings add a description and topics (`langgraph`, `llm`, `evaluation`, `human-in-the-loop`, `fastapi`, `python`).
3. `git grep -n "AIza"` is empty and `git ls-files | grep -E '\.(db|env)$'` shows nothing.
Accept: the tag is pushed and the fresh-clone run passes.

---

## Rules for the whole project
- New ideas go in `docs/backlog.md`. Nothing from the backlog is built before `v0.1.0`. Backlog seeds: a Docker Compose file that starts Project A and B; dense or hybrid retrieval measured against BM25; reviewer authentication; review SLA timers; sampling of fast-tracked claims for QA; a bounded "investigator" step where the LLM chooses among read tools within a budget; Langfuse tracing; Postgres checkpointer; deployment.
- Any change to LLM code keeps using `google-genai`.
