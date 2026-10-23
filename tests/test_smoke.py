"""Smoke test ensuring package and submodules import cleanly."""

import importlib


def test_package_imports():
    import triage

    assert triage is not None


def test_submodules_import():
    submodules = [
        "triage.config",
        "triage.models",
        "triage.state",
        "triage.sanitize",
        "triage.injection",
        "triage.rules",
        "triage.retrieval",
        "triage.llm",
        "triage.nodes",
        "triage.graph",
        "triage.store",
        "triage.service",
        "triage.api",
        "triage.logging_setup",
        "triage.tools.base",
        "triage.tools.fixture_backend",
        "triage.tools.mcp_backend",
    ]
    for sub in submodules:
        mod = importlib.import_module(sub)
        assert mod is not None
