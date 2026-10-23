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
