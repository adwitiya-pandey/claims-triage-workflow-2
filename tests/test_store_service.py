"""Tests for persistence, service orchestration, idempotency, and reviews."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from triage.config import TriageConfig
from triage.service import TriageService
from triage.tools.fixture_backend import FixtureToolsBackend


@pytest.fixture
def service_env(tmp_path):
    """Provide isolated TriageService configured with temporary databases."""
    triage_db = tmp_path / "triage.db"
    cp_db = tmp_path / "checkpoint.db"
    cfg = TriageConfig(
        TRIAGE_DB_PATH=str(triage_db),
        CHECKPOINT_DB_PATH=str(cp_db),
        LLM_MODE="oracle",
    )
    tools = FixtureToolsBackend(cases_dir="data/cases")
    svc = TriageService(config=cfg, tools=tools, cases_dir="data/cases")
    return svc


def test_decisions_table_is_append_only():
    """Verify codebase contains zero UPDATE or DELETE on decisions table (S10)."""
    src_dir = Path("src/triage")
    py_files = list(src_dir.glob("*.py")) + list(src_dir.glob("**/*.py"))

    forbidden_patterns = [
        re.compile(r"\bUPDATE\s+decisions\b", re.IGNORECASE),
        re.compile(r"\bDELETE\s+FROM\s+decisions\b", re.IGNORECASE),
        re.compile(r"\bDELETE\s+decisions\b", re.IGNORECASE),
    ]

    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        for pat in forbidden_patterns:
            match = pat.search(content)
            assert match is None, (
                f"Forbidden mutating SQL on decisions in {py_file}: '{match.group(0)}'"
            )


def test_versions_increment_on_reprocess(service_env):
    """Verify version increments on reprocess and remains idempotent otherwise."""
    svc = service_env

    # Run 1: initial triage
    r1 = svc.start_triage("CLM70000001", reprocess=False)
    assert r1["status"] == "completed"
    assert r1["decision"]["version"] == 1

    # Run 2: idempotent call without reprocess returns version 1
    r2 = svc.start_triage("CLM70000001", reprocess=False)
    assert r2["status"] == "completed"
    assert r2["decision"]["version"] == 1

    # Run 3: explicit reprocess increments version to 2
    r3 = svc.start_triage("CLM70000001", reprocess=True)
    assert r3["status"] == "completed"
    assert r3["decision"]["version"] == 2

    # Run 4: another reprocess increments version to 3
    r4 = svc.start_triage("CLM70000001", reprocess=True)
    assert r4["status"] == "completed"
    assert r4["decision"]["version"] == 3

    # All decisions are retrievable
    all_dec = svc.get_decisions("CLM70000001")
    assert len(all_dec) == 3
    assert [d["version"] for d in all_dec] == [1, 2, 3]


def test_idempotent_triage_pending_review(service_env):
    """A claim pending review returns existing thread without creating a new one."""
    svc = service_env

    r1 = svc.start_triage("CLM70000009")
    assert r1["status"] == "awaiting_review"
    thread1 = r1["thread_id"]
    assert "triage-CLM70000009-v1" == thread1

    # Call again without reprocess
    r2 = svc.start_triage("CLM70000009")
    assert r2["status"] == "awaiting_review"
    assert r2["thread_id"] == thread1

    # Pending reviews index has exactly 1 entry
    pending = svc.list_pending()
    assert len(pending) == 1
    assert pending[0]["thread_id"] == thread1
    assert pending[0]["claim_id"] == "CLM70000009"


def test_pending_index_and_review_resolution(service_env):
    """Test full review lifecycle: pending index -> submission -> resolution."""
    svc = service_env

    r = svc.start_triage("CLM70000009")
    thread_id = r["thread_id"]

    pending = svc.list_pending()
    assert any(p["thread_id"] == thread_id for p in pending)

    # Resolve review
    action = {
        "reviewer_id": "rev10001",
        "action": "CONFIRM",
        "reason": "Reviewed and confirmed inpatient standard review",
    }
    res = svc.submit_review(thread_id, action)
    assert res["status"] == "completed"
    assert res["decision"]["route_final"] == "STANDARD_REVIEW"

    # Now pending list no longer includes this thread
    pending_after = svc.list_pending()
    assert not any(p["thread_id"] == thread_id for p in pending_after)


def test_invalid_review_for_unknown_or_resolved_thread(service_env):
    """Submitting review for non-existent or resolved thread raises ValueError."""
    svc = service_env

    action = {
        "reviewer_id": "rev10001",
        "action": "CONFIRM",
        "reason": "Valid reason exceeding ten chars",
    }

    # Unknown thread
    with pytest.raises(ValueError, match="not found"):
        svc.submit_review("triage-nonexistent-thread-v1", action)

    # Resolve a real thread
    r = svc.start_triage("CLM70000009")
    thread_id = r["thread_id"]
    svc.submit_review(thread_id, action)

    # Submitting again to resolved thread
    with pytest.raises(ValueError, match="already resolved"):
        svc.submit_review(thread_id, action)


def test_invalid_action_schema_fails_validation(service_env):
    """Submitting malformed review action payload fails Pydantic validation."""
    svc = service_env
    r = svc.start_triage("CLM70000009")
    thread_id = r["thread_id"]

    # Reason too short
    bad_action = {
        "reviewer_id": "rev10001",
        "action": "CONFIRM",
        "reason": "short",
    }
    with pytest.raises(ValidationError):
        svc.submit_review(thread_id, bad_action)
