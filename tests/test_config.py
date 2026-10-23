"""Tests for configuration validation and security (S6)."""

import pytest

from triage.config import TriageConfig


def test_config_defaults(monkeypatch):
    # Clear env vars that might interfere
    for key in [
        "LLM_MODE",
        "GEMINI_API_KEY",
        "CLAIMS_TOOLS_BACKEND",
        "CLAIMS_MCP_URL",
        "CLAIMS_MCP_KEY",
        "CLAIMS_MCP_STDIO_CMD",
    ]:
        monkeypatch.delenv(key, raising=False)

    cfg = TriageConfig.load()
    assert cfg.LLM_MODE == "replay"
    assert cfg.CLAIMS_TOOLS_BACKEND == "fixture"
    assert cfg.HIGH_AMOUNT_THRESHOLD == 10000.0
    assert cfg.TIMELY_FILING_DAYS == 90
    assert cfg.MIN_EXTRACT_CONFIDENCE == 0.7
    assert cfg.TOP_K == 4


def test_config_validation_rules(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "invalid_mode")
    with pytest.raises(ValueError, match="Invalid LLM_MODE"):
        TriageConfig.load()

    monkeypatch.setenv("LLM_MODE", "live")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    with pytest.raises(ValueError, match="GEMINI_API_KEY is required"):
        TriageConfig.load()

    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setenv("CLAIMS_TOOLS_BACKEND", "invalid_backend")
    with pytest.raises(ValueError, match="Invalid CLAIMS_TOOLS_BACKEND"):
        TriageConfig.load()

    monkeypatch.setenv("CLAIMS_TOOLS_BACKEND", "mcp")
    monkeypatch.setenv("CLAIMS_MCP_URL", "")
    monkeypatch.setenv("CLAIMS_MCP_STDIO_CMD", "")
    with pytest.raises(
        ValueError, match="Either CLAIMS_MCP_URL or CLAIMS_MCP_STDIO_CMD"
    ):
        TriageConfig.load()


def test_config_never_echoes_secrets(monkeypatch):
    secret_key = "AIzaSySecretTestKey12345"
    mcp_secret = "secret_mcp_bearer_token"
    triage_secret = "secret_triage_api_token"

    monkeypatch.setenv("GEMINI_API_KEY", secret_key)
    monkeypatch.setenv("CLAIMS_MCP_KEY", mcp_secret)
    monkeypatch.setenv("TRIAGE_API_KEY", triage_secret)
    monkeypatch.setenv("LLM_MODE", "live")

    cfg = TriageConfig.load()
    repr_str = repr(cfg)
    str_str = str(cfg)

    # Verify secrets never appear in string representations
    for secret in [secret_key, mcp_secret, triage_secret]:
        assert secret not in repr_str
        assert secret not in str_str

    assert "GEMINI_API_KEY=***" in repr_str
    assert "CLAIMS_MCP_KEY=***" in repr_str
    assert "TRIAGE_API_KEY=***" in repr_str
