# Plain-English Architecture & Implementation Guide

This document explains the claims triage workflow in plain, accessible terms without jargon, documenting each architectural component and implementation step.

---

## T0: Repository Initialization & Scope Contract
- **What was done:** Initialized the Git repository, established `.gitignore` security boundaries (excluding secrets, SQLite databases, and logs), and created the public GitHub repository.
- **Why:** Establishes the project provenance and guarantees that hostile claim text, API keys, and internal state are never accidentally committed.

---

## T1: Scaffold, CI, Settings, SDK Facts
- **What was done:** Initialized the Python package structure using `uv`, defined runtime dependencies (`langgraph`, `langgraph-checkpoint-sqlite`, `google-genai`, `mcp`, `fastapi`, `uvicorn`, `pydantic`, `python-dotenv`), created module stubs with docstrings, configured GitHub Actions CI with Ruff and Pytest, and verified SDK facts against installed packages.
- **Why:** Establishes a reproducible environment and validates toolchains and SDK behaviors before writing business logic.

---

## T2: Models, Config, State, Logging
- **What was done:** Implemented strict Pydantic models with `extra="forbid"`, regex ID patterns with ASCII-only digits, `TriageState` TypedDict with append-reducer `trail`, secure environment configuration with secret masking, and structured JSON logging enforcing security requirement S7 (no claim or note text logged).
- **Review Question - Why State Holds Plain Dicts Instead of Pydantic Objects:**
  LangGraph state is durably saved to SQLite checkpointer tables (`checkpoints` and `writes`) to survive server restarts during human reviews. If state held custom Pydantic classes, deserialization across process boundaries or app restarts would depend on Python class schemas and pickling internals, risking serialization drift or unpickling errors. Using standard JSON-compatible dicts and primitives guarantees bulletproof persistence, auditable records, and easy inspection, while Pydantic strictly validates models at the input and output boundaries.

