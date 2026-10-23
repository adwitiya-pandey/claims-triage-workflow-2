"""Fixture-backed implementation of ClaimsTools per SPEC 8."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from triage.tools.base import ClaimsTools, ToolError


class FixtureToolsBackend(ClaimsTools):
    """Claims tools backend serving data directly from scenario cases or DB."""

    def __init__(
        self,
        cases_dir: str | Path = "data/cases",
        db_path: str | Path | None = None,
    ) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        schema_path = Path("data/schema_from_project_a.sql")
        if not schema_path.exists():
            schema_path = (
                Path(__file__).parents[3] / "data" / "schema_from_project_a.sql"
            )
        if not schema_path.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_path}")

        schema_sql = schema_path.read_text(encoding="utf-8")
        self.conn.executescript(schema_sql)

        if db_path is not None and Path(db_path).exists():
            source_conn = sqlite3.connect(str(db_path))
            source_conn.backup(self.conn)
            source_conn.close()
        else:
            self._load_from_cases_dir(cases_dir)

    def _load_from_cases_dir(self, cases_dir: str | Path) -> None:
        """Load scenario data from JSON case files into SQLite database."""
        c_dir = Path(cases_dir)
        if not c_dir.exists():
            return

        seen_plans = set()
        seen_members = set()
        seen_providers = set()
        seen_coverage_rules = set()
        seen_claims = set()
        seen_notes = set()

        for json_file in sorted(c_dir.glob("*.json")):
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            db_data = data.get("database", {})

            for p in db_data.get("plans", []):
                pid = p["plan_id"]
                if pid not in seen_plans:
                    seen_plans.add(pid)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO plans "
                        "(plan_id, plan_name, annual_limit, deductible, copay_pct) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            p["plan_id"],
                            p["plan_name"],
                            p["annual_limit"],
                            p["deductible"],
                            p["copay_pct"],
                        ),
                    )

            for m in db_data.get("members", []):
                mid = m["member_id"]
                if mid not in seen_members:
                    seen_members.add(mid)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO members "
                        "(member_id, plan_id, age_band, state, enrolled_on) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            m["member_id"],
                            m["plan_id"],
                            m["age_band"],
                            m["state"],
                            m["enrolled_on"],
                        ),
                    )

            for pr in db_data.get("providers", []):
                prid = pr["provider_id"]
                if prid not in seen_providers:
                    seen_providers.add(prid)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO providers "
                        "(provider_id, specialty, state, network_status) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            pr["provider_id"],
                            pr["specialty"],
                            pr["state"],
                            pr["network_status"],
                        ),
                    )

            for cr in db_data.get("coverage_rules", []):
                key = (cr["plan_id"], cr["procedure_code"])
                if key not in seen_coverage_rules:
                    seen_coverage_rules.add(key)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO coverage_rules "
                        "(plan_id, procedure_code, covered, "
                        "requires_preauth, max_allowed) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            cr["plan_id"],
                            cr["procedure_code"],
                            int(cr["covered"]),
                            int(cr["requires_preauth"]),
                            cr.get("max_allowed"),
                        ),
                    )

            for cl in db_data.get("claims", []):
                cid = cl["claim_id"]
                if cid not in seen_claims:
                    seen_claims.add(cid)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO claims "
                        "(claim_id, member_id, provider_id, "
                        "procedure_code, diagnosis_code, "
                        "service_date, submitted_on, billed_amount, "
                        "allowed_amount, paid_amount, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            cl["claim_id"],
                            cl["member_id"],
                            cl["provider_id"],
                            cl["procedure_code"],
                            cl["diagnosis_code"],
                            cl["service_date"],
                            cl["submitted_on"],
                            cl["billed_amount"],
                            cl.get("allowed_amount"),
                            cl.get("paid_amount"),
                            cl["status"],
                        ),
                    )

            for n in db_data.get("claim_notes", []):
                nid = n.get("note_id")
                if nid is not None and nid in seen_notes:
                    continue
                if nid is not None:
                    seen_notes.add(nid)
                    self.conn.execute(
                        "INSERT OR IGNORE INTO claim_notes "
                        "(note_id, claim_id, created_on, author_type, note_text) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            nid,
                            n["claim_id"],
                            n["created_on"],
                            n["author_type"],
                            n["note_text"],
                        ),
                    )
                else:
                    self.conn.execute(
                        "INSERT INTO claim_notes "
                        "(claim_id, created_on, author_type, note_text) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            n["claim_id"],
                            n["created_on"],
                            n["author_type"],
                            n["note_text"],
                        ),
                    )

        self.conn.commit()

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        """Fetch claim by ID, joined with provider network status and plan."""
        sql = """
            SELECT
                c.claim_id,
                c.member_id,
                m.plan_id,
                c.provider_id,
                pr.network_status AS provider_network_status,
                c.procedure_code,
                c.diagnosis_code,
                c.service_date,
                c.submitted_on,
                c.billed_amount,
                c.allowed_amount,
                c.paid_amount,
                c.status
            FROM claims c
            JOIN members m ON c.member_id = m.member_id
            JOIN providers pr ON c.provider_id = pr.provider_id
            WHERE c.claim_id = ?
        """
        cursor = self.conn.execute(sql, (claim_id,))
        row = cursor.fetchone()
        if row is None:
            raise ToolError("NOT_FOUND", f"Claim not found: {claim_id}")
        return dict(row)

    def list_member_claims(
        self,
        member_id: str,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List historical claims for a member ordered by service_date desc."""
        member_check = "SELECT 1 FROM members WHERE member_id = ?"
        if self.conn.execute(member_check, (member_id,)).fetchone() is None:
            raise ToolError("NOT_FOUND", f"Member not found: {member_id}")

        if status is None:
            count_sql = "SELECT count(*) FROM claims WHERE member_id = ?"
            total = self.conn.execute(count_sql, (member_id,)).fetchone()[0]

            items_sql = """
                SELECT claim_id, service_date, procedure_code, billed_amount, status
                FROM claims
                WHERE member_id = ?
                ORDER BY service_date DESC, claim_id ASC
                LIMIT ? OFFSET ?
            """
            rows = self.conn.execute(items_sql, (member_id, limit, offset)).fetchall()
        else:
            count_sql = "SELECT count(*) FROM claims WHERE member_id = ? AND status = ?"
            total = self.conn.execute(count_sql, (member_id, status)).fetchone()[0]

            items_sql = """
                SELECT claim_id, service_date, procedure_code, billed_amount, status
                FROM claims
                WHERE member_id = ? AND status = ?
                ORDER BY service_date DESC, claim_id ASC
                LIMIT ? OFFSET ?
            """
            rows = self.conn.execute(
                items_sql, (member_id, status, limit, offset)
            ).fetchall()

        return {
            "total": int(total),
            "limit": int(limit),
            "offset": int(offset),
            "items": [
                {
                    "claim_id": str(r["claim_id"]),
                    "service_date": str(r["service_date"]),
                    "procedure_code": str(r["procedure_code"]),
                    "billed_amount": float(r["billed_amount"]),
                    "status": str(r["status"]),
                }
                for r in rows
            ],
        }

    def get_coverage_rule(self, plan_id: str, procedure_code: str) -> dict[str, Any]:
        """Fetch coverage rule for a plan and procedure."""
        sql = """
            SELECT plan_id, procedure_code, covered, requires_preauth, max_allowed
            FROM coverage_rules
            WHERE plan_id = ? AND procedure_code = ?
        """
        row = self.conn.execute(sql, (plan_id, procedure_code)).fetchone()
        if row is None:
            raise ToolError(
                "NOT_FOUND",
                f"Coverage rule not found for {plan_id} and {procedure_code}",
            )

        return {
            "plan_id": str(row["plan_id"]),
            "procedure_code": str(row["procedure_code"]),
            "covered": bool(row["covered"]),
            "requires_preauth": bool(row["requires_preauth"]),
            "max_allowed": (
                float(row["max_allowed"]) if row["max_allowed"] is not None else None
            ),
        }

    def get_member_ytd_summary(self, member_id: str, year: int) -> dict[str, Any]:
        """Calculate year-to-date claim totals and remaining annual limit."""
        member_sql = """
            SELECT m.member_id, m.plan_id, p.annual_limit, p.deductible
            FROM members m
            JOIN plans p ON m.plan_id = p.plan_id
            WHERE m.member_id = ?
        """
        member_row = self.conn.execute(member_sql, (member_id,)).fetchone()
        if member_row is None:
            raise ToolError("NOT_FOUND", f"Member not found: {member_id}")

        plan_id = str(member_row["plan_id"])
        annual_limit = float(member_row["annual_limit"])
        deductible = float(member_row["deductible"])

        year_str = str(year)
        agg_sql = """
            SELECT
                status,
                count(*) AS claim_count,
                sum(billed_amount) AS total_billed,
                sum(coalesce(paid_amount, 0.0)) AS total_paid
            FROM claims
            WHERE member_id = ? AND strftime('%Y', service_date) = ?
            GROUP BY status
            ORDER BY status ASC
        """
        rows = self.conn.execute(agg_sql, (member_id, year_str)).fetchall()

        by_status = []
        paid_claims_total_paid = 0.0
        for r in rows:
            st = str(r["status"])
            c_count = int(r["claim_count"])
            t_billed = float(r["total_billed"] or 0.0)
            t_paid = float(r["total_paid"] or 0.0)
            by_status.append(
                {
                    "status": st,
                    "claim_count": c_count,
                    "total_billed": t_billed,
                    "total_paid": t_paid,
                }
            )
            if st == "PAID":
                paid_claims_total_paid = t_paid

        remaining_annual_limit = max(0.0, annual_limit - paid_claims_total_paid)

        return {
            "member_id": str(member_id),
            "year": int(year),
            "plan_id": plan_id,
            "annual_limit": annual_limit,
            "deductible": deductible,
            "by_status": by_status,
            "remaining_annual_limit": remaining_annual_limit,
        }

    def get_claim_notes(self, claim_id: str, limit: int = 10) -> dict[str, Any]:
        """Fetch clinical/administrative notes for a claim."""
        claim_check = "SELECT 1 FROM claims WHERE claim_id = ?"
        if self.conn.execute(claim_check, (claim_id,)).fetchone() is None:
            raise ToolError("NOT_FOUND", f"Claim not found: {claim_id}")

        notes_sql = """
            SELECT note_id, created_on, author_type, note_text
            FROM claim_notes
            WHERE claim_id = ?
            ORDER BY created_on DESC, note_id DESC
            LIMIT ?
        """
        rows = self.conn.execute(notes_sql, (claim_id, limit)).fetchall()
        notes = [
            {
                "note_id": int(r["note_id"]),
                "created_on": str(r["created_on"]),
                "author_type": str(r["author_type"]),
                "text": str(r["note_text"])[:1000],
                "truncated": len(str(r["note_text"])) > 1000,
            }
            for r in rows
        ]
        return {
            "claim_id": str(claim_id),
            "untrusted": True,
            "notes": notes,
        }
