"""Script to build scenario SQLite database from case JSON fixtures."""

import argparse
import json
import sqlite3
from pathlib import Path


def build_scenario_database(cases_dir: Path, schema_path: Path, out_path: Path) -> None:
    """Read all scenario cases and insert rows into SQLite database per schema."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    conn = sqlite3.connect(str(out_path))
    conn.execute("PRAGMA foreign_keys = ON;")

    schema_sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema_sql)

    seen_plans = set()
    seen_members = set()
    seen_providers = set()
    seen_coverage_rules = set()
    seen_claims = set()
    seen_notes = set()

    case_files = sorted(cases_dir.glob("*.json"))
    for cf in case_files:
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Skipping malformed JSON {cf}: {e}")
            continue

        db = data.get("database", {})

        for p in db.get("plans", []):
            pid = p["plan_id"]
            if pid not in seen_plans:
                seen_plans.add(pid)
                conn.execute(
                    "INSERT INTO plans "
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

        for m in db.get("members", []):
            mid = m["member_id"]
            if mid not in seen_members:
                seen_members.add(mid)
                conn.execute(
                    "INSERT INTO members "
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

        for pr in db.get("providers", []):
            prid = pr["provider_id"]
            if prid not in seen_providers:
                seen_providers.add(prid)
                conn.execute(
                    "INSERT INTO providers "
                    "(provider_id, specialty, state, network_status) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        pr["provider_id"],
                        pr["specialty"],
                        pr["state"],
                        pr["network_status"],
                    ),
                )

        for cr in db.get("coverage_rules", []):
            key = (cr["plan_id"], cr["procedure_code"])
            if key not in seen_coverage_rules:
                seen_coverage_rules.add(key)
                conn.execute(
                    "INSERT INTO coverage_rules "
                    "(plan_id, procedure_code, covered, requires_preauth, max_allowed) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        cr["plan_id"],
                        cr["procedure_code"],
                        int(cr["covered"]),
                        int(cr["requires_preauth"]),
                        cr.get("max_allowed"),
                    ),
                )

        for cl in db.get("claims", []):
            cid = cl["claim_id"]
            if cid not in seen_claims:
                seen_claims.add(cid)
                conn.execute(
                    "INSERT INTO claims "
                    "(claim_id, member_id, provider_id, procedure_code, "
                    "diagnosis_code, service_date, submitted_on, "
                    "billed_amount, allowed_amount, paid_amount, status) "
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

        for n in db.get("claim_notes", []):
            nid = n.get("note_id")
            if nid is not None and nid in seen_notes:
                continue
            if nid is not None:
                seen_notes.add(nid)
                conn.execute(
                    "INSERT INTO claim_notes "
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
                conn.execute(
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

    conn.commit()
    conn.close()
    print(f"Successfully generated scenario database at: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build scenario SQLite DB from JSON cases."
    )
    parser.add_argument("--cases-dir", type=Path, default=Path("data/cases"))
    parser.add_argument(
        "--schema", type=Path, default=Path("data/schema_from_project_a.sql")
    )
    parser.add_argument("--out", type=Path, default=Path("data/scenario.db"))
    args = parser.parse_args()

    build_scenario_database(args.cases_dir, args.schema, args.out)


if __name__ == "__main__":
    main()
