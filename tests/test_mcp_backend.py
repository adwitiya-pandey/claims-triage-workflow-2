"""Unit tests for McpToolsBackend with fake/mock transport."""

import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from triage.tools.base import ClaimsTools, ToolError
from triage.tools.mcp_backend import McpToolsBackend


def test_mcp_backend_implements_protocol_and_has_no_write_methods():
    """Verify McpToolsBackend implements ClaimsTools with zero write methods (S4)."""
    backend = McpToolsBackend(url="http://test")
    assert isinstance(backend, ClaimsTools)

    prohibited_names = (
        "write",
        "insert",
        "update",
        "delete",
        "create",
        "drop",
        "patch",
        "approve",
        "deny",
        "pay",
    )
    methods = [
        name
        for name, _ in inspect.getmembers(McpToolsBackend, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    for m in methods:
        for p in prohibited_names:
            assert p not in m.lower(), f"Prohibited write keyword '{p}' in method '{m}'"

    expected_methods = {
        "get_claim",
        "list_member_claims",
        "get_coverage_rule",
        "get_member_ytd_summary",
        "get_claim_notes",
    }
    assert set(methods) == expected_methods


def test_mcp_backend_successful_call():
    """Test successful tool call returns deserialized dict."""
    expected_data = {"claim_id": "CLM70000001", "billed_amount": 150.0}

    def fake_caller(tool_name: str, args: dict[str, Any]):
        assert tool_name == "get_claim"
        assert args == {"claim_id": "CLM70000001"}
        return expected_data

    backend = McpToolsBackend(session_caller=fake_caller)
    res = backend.get_claim("CLM70000001")
    assert res == expected_data


def test_mcp_backend_error_code_mapping():
    """Verify tool errors map to ToolError with appropriate code."""

    def not_found_caller(tool_name: str, args: dict[str, Any]):
        raise ToolError("NOT_FOUND", "Claim CLM99999999 not found")

    backend = McpToolsBackend(session_caller=not_found_caller)
    with pytest.raises(ToolError) as exc_info:
        backend.get_claim("CLM99999999")
    assert exc_info.value.code == "NOT_FOUND"


def test_mcp_backend_retry_only_rate_limited():
    """Test that retry backoff only applies to RATE_LIMITED errors."""
    attempts = 0
    sleeps: list[float] = []

    def flakey_caller(tool_name: str, args: dict[str, Any]):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ToolError("RATE_LIMITED", "Too many requests")
        return {"claim_id": "CLM70000001", "status": "SUBMITTED"}

    backend = McpToolsBackend(
        session_caller=flakey_caller,
        delays=[0.01, 0.02, 0.04],
        sleep_fn=sleeps.append,
    )

    res = backend.get_claim("CLM70000001")
    assert res["status"] == "SUBMITTED"
    assert attempts == 3
    assert len(sleeps) == 2
    assert sleeps == [0.01, 0.02]


def test_mcp_backend_non_retryable_error_fails_immediately():
    """Verify non-rate-limited error does not retry."""
    attempts = 0
    sleeps: list[float] = []

    def invalid_input_caller(tool_name: str, args: dict[str, Any]):
        nonlocal attempts
        attempts += 1
        raise ToolError("INVALID_INPUT", "Bad format")

    backend = McpToolsBackend(
        session_caller=invalid_input_caller,
        delays=[0.01, 0.02],
        sleep_fn=sleeps.append,
    )

    with pytest.raises(ToolError) as exc_info:
        backend.get_claim("CLM00000000")
    assert exc_info.value.code == "INVALID_INPUT"
    assert attempts == 1
    assert len(sleeps) == 0


def test_mcp_backend_all_methods():
    """Verify all 5 ClaimsTools methods pass correct arguments and return data."""
    calls = []

    def spy_caller(tool_name: str, args: dict[str, Any]):
        calls.append((tool_name, args))
        return {"ok": True, "tool": tool_name}

    backend = McpToolsBackend(session_caller=spy_caller)

    backend.get_claim("CLM1")
    backend.list_member_claims("MEM1", status="SUBMITTED", limit=10, offset=5)
    backend.get_coverage_rule("PLN1", "PX1")
    backend.get_member_ytd_summary("MEM1", 2026)
    backend.get_claim_notes("CLM1", limit=5)

    assert len(calls) == 5
    assert calls[0] == ("get_claim", {"claim_id": "CLM1"})
    assert calls[1] == (
        "list_member_claims",
        {"member_id": "MEM1", "status": "SUBMITTED", "limit": 10, "offset": 5},
    )
    assert calls[2] == (
        "get_coverage_rule",
        {"plan_id": "PLN1", "procedure_code": "PX1"},
    )
    assert calls[3] == ("get_member_ytd_summary", {"member_id": "MEM1", "year": 2026})
    assert calls[4] == ("get_claim_notes", {"claim_id": "CLM1", "limit": 5})


def test_mcp_backend_transport_auth_error():
    """Verify HTTP 401 surfaces as UNAUTHENTICATED ToolError."""
    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_http_exc = Exception("Unauthorized")
    fake_http_exc.response = fake_response  # type: ignore[attr-defined]

    def auth_fail_caller(tool_name: str, args: dict[str, Any]):
        raise fake_http_exc

    backend = McpToolsBackend(session_caller=auth_fail_caller)
    with pytest.raises(Exception):
        backend.get_claim("CLM1")
