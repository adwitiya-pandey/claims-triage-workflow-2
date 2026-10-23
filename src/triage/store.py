"""Persistence layer for triage decisions and review records per SPEC 13."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decisions (
  claim_id        TEXT NOT NULL,
  version         INTEGER NOT NULL,
  route_recommended TEXT NOT NULL CHECK (
    route_recommended IN ('FAST_TRACK','STANDARD_REVIEW','ESCALATE')
  ),
  route_final     TEXT NOT NULL CHECK (
    route_final IN ('FAST_TRACK','STANDARD_REVIEW','ESCALATE')
  ),
  record_json     TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  PRIMARY KEY (claim_id, version)
);
CREATE TABLE IF NOT EXISTS reviews (
  thread_id    TEXT PRIMARY KEY,
  claim_id     TEXT NOT NULL,
  version      INTEGER NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('awaiting','done')),
  created_at   TEXT NOT NULL,
  resolved_at  TEXT,
  reviewer_id  TEXT
);
"""


def init_db(
    target: str | Path | sqlite3.Connection = "data/triage.db",
) -> sqlite3.Connection:
    """Initialize triage database tables using parameterized schema."""
    if isinstance(target, sqlite3.Connection):
        conn = target
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        return conn

    p = Path(target)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def save_decision(
    conn: sqlite3.Connection,
    decision: dict[str, Any],
) -> bool:
    """Save decision record into decisions table (S10 append-only)."""
    sql = """
        INSERT OR IGNORE INTO decisions (
            claim_id,
            version,
            route_recommended,
            route_final,
            record_json,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
    """
    record_json_str = json.dumps(decision, sort_keys=True)
    cur = conn.execute(
        sql,
        (
            decision["claim_id"],
            decision["version"],
            decision["route_recommended"],
            decision["route_final"],
            record_json_str,
            decision["created_at"],
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def get_latest_decision(
    conn: sqlite3.Connection,
    claim_id: str,
) -> dict[str, Any] | None:
    """Retrieve the most recent decision record for claim_id."""
    sql = """
        SELECT record_json FROM decisions
        WHERE claim_id = ?
        ORDER BY version DESC
        LIMIT 1
    """
    row = conn.execute(sql, (claim_id,)).fetchone()
    if row is None:
        return None
    return json.loads(row["record_json"])  # type: ignore[no-any-return]


def get_all_decisions(
    conn: sqlite3.Connection,
    claim_id: str,
) -> list[dict[str, Any]]:
    """Retrieve all decision versions for claim_id, oldest first."""
    sql = """
        SELECT record_json FROM decisions
        WHERE claim_id = ?
        ORDER BY version ASC
    """
    rows = conn.execute(sql, (claim_id,)).fetchall()
    return [json.loads(r["record_json"]) for r in rows]


def get_latest_version(
    conn: sqlite3.Connection,
    claim_id: str,
) -> int:
    """Return latest decision version number for claim_id, or 0 if none."""
    sql = "SELECT MAX(version) AS max_v FROM decisions WHERE claim_id = ?"
    row = conn.execute(sql, (claim_id,)).fetchone()
    if row is None or row["max_v"] is None:
        return 0
    return int(row["max_v"])


def create_review(
    conn: sqlite3.Connection,
    thread_id: str,
    claim_id: str,
    version: int,
) -> bool:
    """Create a new awaiting review record in reviews table."""
    sql = """
        INSERT OR IGNORE INTO reviews (
            thread_id,
            claim_id,
            version,
            status,
            created_at
        ) VALUES (?, ?, ?, 'awaiting', ?)
    """
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(sql, (thread_id, claim_id, version, now))
    conn.commit()
    return cur.rowcount > 0


def get_review(
    conn: sqlite3.Connection,
    thread_id: str,
) -> dict[str, Any] | None:
    """Fetch review record by thread_id."""
    sql = """
        SELECT thread_id, claim_id, version, status,
               created_at, resolved_at, reviewer_id
        FROM reviews
        WHERE thread_id = ?
    """
    row = conn.execute(sql, (thread_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def get_pending_review_by_claim(
    conn: sqlite3.Connection,
    claim_id: str,
) -> dict[str, Any] | None:
    """Fetch pending awaiting review for claim_id, if any."""
    sql = """
        SELECT thread_id, claim_id, version, status,
               created_at, resolved_at, reviewer_id
        FROM reviews
        WHERE claim_id = ? AND status = 'awaiting'
        ORDER BY version DESC
        LIMIT 1
    """
    row = conn.execute(sql, (claim_id,)).fetchone()
    if row is None:
        return None
    return dict(row)


def list_pending_reviews(
    conn: sqlite3.Connection,
) -> list[dict[str, Any]]:
    """List all pending awaiting reviews ordered by creation date."""
    sql = """
        SELECT thread_id, claim_id, version, status,
               created_at, resolved_at, reviewer_id
        FROM reviews
        WHERE status = 'awaiting'
        ORDER BY created_at ASC
    """
    rows = conn.execute(sql).fetchall()
    return [dict(r) for r in rows]


def resolve_review(
    conn: sqlite3.Connection,
    thread_id: str,
    reviewer_id: str,
) -> bool:
    """Mark an awaiting review as done with timestamp and reviewer_id."""
    sql = """
        UPDATE reviews
        SET status = 'done', resolved_at = ?, reviewer_id = ?
        WHERE thread_id = ? AND status = 'awaiting'
    """
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(sql, (now, reviewer_id, thread_id))
    conn.commit()
    return cur.rowcount > 0
