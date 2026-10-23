# AGENTS.md

> **AUTONOMOUS END-TO-END EXECUTION RULE (SINGLE-CHAT WORKFLOW)**:
> Execute **all tasks in `TASKS.md` back-to-back from `T0`/`H0` through `v0.1.0` GitHub push** in a single continuous run.
> - **Do NOT stop after individual tasks** to wait for human PR merges. Commit each task cleanly with Conventional Commits (`Refs: <TaskID>`), merge branches automatically, update `PROGRESS.md`, and continue straight to the next task.
> - **Write 100% of the code yourself** (including `queries.py`, `graph.py`, `policy.py`, `approvals.py`, `sqlsafe.py`, and the `H9` merge conflict).
> - **API Keys**: Use `GEMINI_API_KEY` from the project's `.env` file for live Gemini steps. Never require `ANTHROPIC_API_KEY` or `LANGSMITH_API_KEY`.


Read SPEC.md (the contract), the TASKS.md task you were given, and the SPEC sections that task cites, before touching code. This file holds only what is not obvious from the code.

## What this is
A claims-triage workflow on LangGraph for a fictional health-insurance TPA. Given a `claim_id` it recommends a route (`FAST_TRACK`, `STANDARD_REVIEW`, `ESCALATE`), pauses for a human when the route is not fast-track, and stores an auditable decision record. It reads claims data only through Project A's MCP tools (separate repo, `claims-mcp-server`). Python 3.12, `uv`. LLM calls: Gemini via `google-genai`, in two nodes only.

## Environment and commands
- The owner works in WSL2 Ubuntu (repo under `~/projects`, never under `/mnt/c` or `/mnt/d`) and sometimes opens it from Windows. CI runs Linux. Use `pathlib`; no shell-specific paths. Tests must run offline with no API key set. Line endings are LF (`.gitattributes`).
- Install: `uv sync`
- Check: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -m "not integration"`
- Eval gate: `uv run python -m evals.run_eval --mode oracle` (non-zero exit when a gate in `evals/gates.json` fails)
- Run each task's own acceptance command from TASKS.md before committing.
- Run only the commands above, the task's own commands, and `git`/`gh` for the task's branch and PR. Ask before anything else.

## Gotchas that will bite you
1. **The LLM never chooses the route.** Only `rules.py` does, as a pure function. The LLM extracts fields and labels clauses; its output can raise caution, and `supports` can help only if its verbatim quote verifies. Never feed the LLM's summary or confidence into a route decision other than R13 (confidence) and R15/R17 (validated clauses).
2. **LangGraph `interrupt()` rules.** On resume the node restarts from its first line, so the `review` node builds a packet, calls `interrupt()`, validates the answer, and returns. No side effects before it, no broad `try/except` around it, no `while` loop around it. Pass only JSON-serialisable values. Resume with `Command(resume=...)` and the same `thread_id`. Do not use `Command(update=...)` as input. Do not use the `response_schema=` option of `interrupt()`.
3. **State holds plain JSON-able data only** (dicts, lists, str, numbers, bool, None). Convert Pydantic models with `.model_dump(mode="json")` going in and re-validate coming out.
4. **Do not invent SDK or framework APIs.** `docs/sdk_notes.md` holds what was verified on 17 Oct 2026 (langgraph 1.2.16, langgraph-checkpoint-sqlite 3.1.2, google-genai 2.31.0, mcp 2.3.0). Re-run its probes against the installed versions before coding against them, and correct the file if anything differs. If a pattern is not documented, say so there and choose the simplest documented one.
5. **Gemini only, no LangChain wrappers.** Only `llm.py` imports `google.genai`. Our code imports `langgraph.*` but never `langchain*`, `langchain_google_genai`, `openai` or `anthropic`; a test enforces this. Do not "switch providers".
6. **Cassettes are evidence.** Never edit a cassette by hand, never re-record without the owner (task T16), and never make replay fall back to live. A `CassetteMiss` means the prompt or inputs changed: stop and report it.
7. **Claim text is hostile data.** Sanitise and scan before any LLM call, wrap it in the fixed markers, never put it in a system prompt, never log it, never copy it into the decision record or a review packet.
8. **Regex:** `re.fullmatch` and `[0-9]` for IDs (`\d` matches non-ASCII digits; `$` matches before a trailing newline). Add failing test cases before changing a pattern. Never loosen a pattern to make a test pass.
9. **Project A is a separate repo and read-only to you.** Never edit it. Never import its code. Talk to it only through the tools interface (error wire format: SPEC 8).
10. **SQL in `store.py` is parameterised with `?`.** No f-strings, `.format()` or concatenation with values. `decisions` is append-only: no `UPDATE` or `DELETE` on it.

## Never
- Write `src/triage/graph.py` completely in T11 after creating the tests in T10, and explain the LangGraph workflow and `interrupt()` checkpointing in `docs/EXPLAIN-PLAIN-ENGLISH.md`.
- Keep unit tests (`T1`–`T15`) 100% offline. In `T16`, use `GEMINI_API_KEY` from the project's `.env` file to run `scripts/record_cassettes.py` (with rate-limit-friendly sleep intervals for the free/Pro Gemini tier), run `--mode replay`, start Project A (`~/projects/claims-mcp-server`) in the background for the MCP integration test, and write `docs/eval_findings.md`.
- Never add a deny, approve or pay action anywhere. Never add a write method to the tools interface. Never add a route other than the three.
- Never print or commit `.env` (keep `.env` in `.gitignore`; load it automatically via `python-dotenv` / `pydantic-settings`), real keys, `*.db` files, or logs. Create `.env.example` only, with dummy values. The owner stores `GEMINI_API_KEY` in the project's local `.env` file (which is gitignored and loaded via `python-dotenv` / `pydantic-settings`). Never print or commit `.env`.
- Never add a dependency that is not in SPEC section 19 without asking. Never add LangChain model wrappers, vector databases, `rank_bm25`, or sentence-transformers.
- Never change SPEC.md, a rule ID, a threshold default, a gate, or a security requirement to make a test pass. Fix the code, or stop and ask.
- Never skip, delete or weaken a safety, injection or eval-gate test.
- Never use real personal data, even in examples. Everything is synthetic.

## Work style: autonomous, with a short list of reasons to stop
- One task per branch (`task/T6-rules`), execute all tasks in order continuously, updating PROGRESS.md after each task. Your first message: the task ID, the files you will touch, the tests you will write first, the acceptance command. Keep it short, then proceed.
- Tests first for T5 (sanitise, injection), T6 (rules), T7 (llm), T10 (graph): write the failing tables from the SPEC, watch them fail, then implement.
- Commits follow Conventional Commits: `type(scope): imperative summary`, at most 72 characters, no task ID in the subject. Types: `feat`, `fix`, `test`, `docs`, `ci`, `chore`, `refactor`. Put `Refs: T6` as a trailer. Example: `feat(rules): add deterministic rule engine with scenario tests`.
- Commit with a conventional title (`Refs: T<N>`), merge into `main` automatically, and proceed immediately to the next task. Do not ask permission for routine work.
- Two-step git in any instructions you write for the owner: `git branch <name>` then `git switch <name>`; never `git checkout -b`.
- Facts only from official docs (LangGraph docs.langchain.com/oss/python/langgraph, Gemini API, MCP Python SDK, FastAPI, Pydantic, pytest, uv) and the installed packages. No blog posts.
- T11 (wiring `graph.py`) and T16 (recording cassettes with `GEMINI_API_KEY` from the environment) are executed automatically by the agent.
- Model choice (Copilot): Gemini 3.8 Flash High (Antigravity) / Copilot Auto for every task. T7, T10, T12 and T15 are where subtle mistakes hide: re-read your diff against the SPEC before opening the PR.
- Keep replies short: what changed, the last lines of the acceptance command, what the owner should read.

**Stop and ask the owner before:** adding or upgrading a dependency; changing SPEC.md, a rule, a threshold or a gate; re-recording cassettes; any network access other than package installs and official docs; deleting or renaming files outside the task's scope; anything that weakens fail-closed behaviour, the human gate, or the injection defences.

If SPEC is ambiguous, choose the safer reading (more escalation, less logging), record the question in `docs/open_questions.md`, and continue.

## Definition of done for every task
The acceptance command passes; ruff is clean; new behaviour has a test (written first for rules, injection, sanitising, and graph behaviour); `docs/decisions.md` is updated if a decision changed; the code reads clearly to a reviewer (short functions, a comment saying why, no cleverness). End the PR body with three lines: what changed, how it was verified, what the owner should read to understand it.

## Harness
This file is read by VS Code Copilot (setting `chat.useAgentsMdFile`) and by Antigravity (1.20.5 or later). If it is not in your context, say so before doing anything else. Keep this file under 12,000 characters (Antigravity's per-rules-file limit).
