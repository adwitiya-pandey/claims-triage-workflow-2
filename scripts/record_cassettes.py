"""Script to record Gemini LLM responses for all 43 cases into cassette.jsonl."""

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from triage.config import TriageConfig
from triage.llm import generate_json, load_prompt
from triage.models import Assessment, Extraction
from triage.retrieval import load_corpus, retrieve
from triage.sanitize import sanitize_text


def record_all_cassettes(
    cases_dir: str | Path = "data/cases",
    cassette_path: str | Path = "data/cassettes/cassette.jsonl",
    sleep_between_calls: float = 2.0,
) -> None:
    """Record live Gemini responses for extraction and assessment across all cases."""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: GEMINI_API_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    c_dir = Path(cases_dir)
    case_files = sorted(c_dir.glob("*.json"))
    if not case_files:
        print(f"ERROR: No scenario cases found in {cases_dir}", file=sys.stderr)
        sys.exit(1)

    cass_file = Path(cassette_path)
    cass_file.parent.mkdir(parents=True, exist_ok=True)
    if cass_file.exists():
        cass_file.unlink()

    cfg = TriageConfig(
        GEMINI_API_KEY=api_key,
        LLM_MODE="record",
        CASSETTE_PATH=str(cass_file),
    )

    corpus = load_corpus("data/policy/clauses.md")
    extract_prompt = load_prompt("extract_v1")
    assess_prompt = load_prompt("assess_v1")

    print(f"Starting cassette recording: {len(case_files)} cases -> {cassette_path}")
    print(f"Model: {cfg.GEMINI_MODEL} | Delay: {sleep_between_calls}s per call\n")

    for idx, cf in enumerate(case_files, start=1):
        case_data = json.loads(cf.read_text(encoding="utf-8"))
        claim_id = case_data["claim_id"]
        category = case_data.get("category")
        raw_text = case_data.get("submission_text", "")
        clean_text, _ = sanitize_text(raw_text)

        print(f"[{idx}/{len(case_files)}] Recording {claim_id} ({category})...")

        # 1. Record Extract node
        try:
            generate_json(
                node="extract",
                system_prompt=extract_prompt,
                user_content=clean_text,
                schema=Extraction.model_json_schema(),
                prompt_version="extract_v1",
                config=cfg,
            )
        except Exception as exc:
            print(f"  Warning: extract call for {claim_id} encountered {exc}")

        time.sleep(sleep_between_calls)

        # 2. Record Assess node
        gold_extract = case_data.get("gold_extraction") or {}
        procedure_code = gold_extract.get("procedure_code", "")
        query = f"Procedure {procedure_code} claim submission"
        chunks = retrieve(query, corpus=corpus, procedure_code=procedure_code, top_k=4)

        assess_user_content = json.dumps(
            {
                "claim_text": clean_text,
                "procedure_code": procedure_code,
                "chunks": [c.model_dump(mode="json") for c in chunks],
            }
        )

        try:
            generate_json(
                node="assess",
                system_prompt=assess_prompt,
                user_content=assess_user_content,
                schema=Assessment.model_json_schema(),
                prompt_version="assess_v1",
                config=cfg,
            )
        except Exception as exc:
            print(f"  Warning: assess call for {claim_id} encountered {exc}")

        time.sleep(sleep_between_calls)

    print(f"\nRecording completed successfully! Cassette saved to {cassette_path}")


if __name__ == "__main__":
    record_all_cassettes()
