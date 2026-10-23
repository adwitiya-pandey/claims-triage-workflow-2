"""Structured JSON logging configuration following security requirement S7."""

import json
import logging
from datetime import datetime, timezone
from typing import Any

ALLOWED_LOG_KEYS = {
    "timestamp",
    "level",
    "logger",
    "message",
    "claim_id",
    "thread_id",
    "node",
    "duration_ms",
    "outcome",
    "rule_ids",
    "detail_codes",
    "run_id",
    "node_name",
    "latency_ms",
    "token_usage",
    "cost_usd",
    "guardrail_triggered",
    "final_route",
}

FORBIDDEN_KEYS = {
    "raw_text",
    "clean_text",
    "submission_text",
    "notes",
    "claim_text",
    "prompt",
    "response_text",
    "summary",
    "quote",
}


class StructuredJsonFormatter(logging.Formatter):
    """JSON formatter restricting output to approved metadata fields only (S7)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Extract only explicitly allowed contextual metadata from record.__dict__
        for key in ALLOWED_LOG_KEYS:
            if hasattr(record, key) and key not in log_entry:
                val = getattr(record, key)
                log_entry[key] = val

        # Explicitly scrub or reject any forbidden keys if passed accidentally
        for forbidden in FORBIDDEN_KEYS:
            log_entry.pop(forbidden, None)

        return json.dumps(log_entry)


def setup_logging(log_level: str = "INFO") -> logging.Logger:
    """Configure root logger to emit structured JSON to stdout."""
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger = logging.getLogger("triage")
    logger.setLevel(level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(StructuredJsonFormatter())
        logger.addHandler(handler)
        logger.propagate = False

    return logger
