"""Tests for structured JSON logging and security requirement S7."""

import json
import logging

from triage.logging_setup import StructuredJsonFormatter


def test_structured_json_logging_output():
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="triage.test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Workflow node completed",
        args=(),
        exc_info=None,
    )
    record.claim_id = "CLM70000001"
    record.thread_id = "triage-CLM70000001-v1"
    record.node = "intake"
    record.duration_ms = 42
    record.outcome = "ok"
    record.rule_ids = ["R07"]

    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "triage.test"
    assert parsed["message"] == "Workflow node completed"
    assert parsed["claim_id"] == "CLM70000001"
    assert parsed["thread_id"] == "triage-CLM70000001-v1"
    assert parsed["node"] == "intake"
    assert parsed["duration_ms"] == 42
    assert parsed["outcome"] == "ok"
    assert parsed["rule_ids"] == ["R07"]
    assert "timestamp" in parsed


def test_logging_scrubs_forbidden_text_fields():
    formatter = StructuredJsonFormatter()
    record = logging.LogRecord(
        name="triage.test",
        level=logging.WARNING,
        pathname="test.py",
        lineno=20,
        msg="Warning event",
        args=(),
        exc_info=None,
    )
    record.claim_id = "CLM70000002"
    # Attempt to inject sensitive/forbidden text fields into the log record
    record.raw_text = "Patient suffered acute trauma. Confidential clinical note."
    record.clean_text = "Patient suffered acute trauma."
    record.notes = "Internal hospital nurse note: patient admitted."
    record.prompt = "System prompt: You are a medical triage assistant."
    record.submission_text = "Attached invoice text..."

    output = formatter.format(record)
    parsed = json.loads(output)

    assert parsed["claim_id"] == "CLM70000002"
    # Ensure all forbidden text fields were rejected / not included
    for forbidden in ["raw_text", "clean_text", "notes", "prompt", "submission_text"]:
        assert forbidden not in parsed
    assert "acute trauma" not in output
    assert "Confidential clinical note" not in output
