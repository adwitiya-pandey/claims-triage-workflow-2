"""Tests for policy corpus parsing, BM25 indexing, and retrieval engine."""

import math

import pytest

from triage.retrieval import (
    BM25Index,
    PolicyClause,
    compute_corpus_sha256,
    load_corpus,
    parse_clauses_markdown,
    retrieve,
    tokenize,
)


def test_tokenize_drops_stop_words_and_normalizes():
    tokens = tokenize("The quick brown Fox jumps over the lazy Dog! 123")
    assert "the" not in tokens
    assert "over" not in tokens
    assert "fox" in tokens
    assert "dog" in tokens
    assert "123" in tokens


def test_loader_errors_on_malformed_clauses():
    # Missing kind
    bad_md1 = """## POL-TEST-01
procedure_code:
---
Valid body text describing a test policy clause.
"""
    with pytest.raises(ValueError, match="invalid or missing kind"):
        parse_clauses_markdown(bad_md1)

    # Duplicate chunk ID
    bad_md2 = """## POL-TEST-01
kind: coverage
---
First clause text.

## POL-TEST-01
kind: coverage
---
Duplicate clause text.
"""
    with pytest.raises(ValueError, match="Duplicate clause ID"):
        parse_clauses_markdown(bad_md2)

    # Procedure clause missing procedure_code
    bad_md3 = """## POL-PX0001
kind: procedure
procedure_code:
---
Procedure clause missing code.
"""
    with pytest.raises(ValueError, match="must have procedure_code set"):
        parse_clauses_markdown(bad_md3)


def test_full_corpus_loader_and_sha256():
    clauses = load_corpus("data/policy/clauses.md")
    assert len(clauses) >= 29

    # Verify procedure clauses PX0001..PX0012 exist
    proc_codes = {c.procedure_code for c in clauses if c.kind == "procedure"}
    expected_codes = {f"PX{i:04d}" for i in range(1, 13)}
    assert expected_codes.issubset(proc_codes)

    sha1 = compute_corpus_sha256(clauses)
    sha2 = compute_corpus_sha256(clauses)
    assert sha1 == sha2
    assert len(sha1) == 64


def test_bm25_toy_corpus_and_hand_computation():
    toy_clauses = [
        PolicyClause("POL-D0", "coverage", None, "knee surgery joint reconstruction"),
        PolicyClause("POL-D1", "coverage", None, "physical therapy rehabilitative"),
        PolicyClause("POL-D2", "coverage", None, "knee replacement joint prosthesis"),
        PolicyClause("POL-D3", "coverage", None, "cardiac catheterization heart"),
        PolicyClause("POL-D4", "procedure", "PX0001", "general consultation visit"),
    ]
    index = BM25Index(toy_clauses, k1=1.5, b=0.75)

    # Document frequencies: "knee" appears in D0 and D2 -> n = 2. N = 5.
    # idf = ln(1 + (5 - 2 + 0.5) / (2 + 0.5)) = ln(1 + 3.5 / 2.5) = ln(2.4)
    expected_idf_knee = math.log(1.0 + 3.5 / 2.5)
    assert abs(index.idf("knee") - expected_idf_knee) < 1e-6

    # "cardiac" appears in D3 only -> n = 1. N = 5.
    # idf = ln(1 + (5 - 1 + 0.5) / (1 + 0.5)) = ln(1 + 4.5 / 1.5) = ln(4.0)
    expected_idf_cardiac = math.log(4.0)
    assert abs(index.idf("cardiac") - expected_idf_cardiac) < 1e-6

    # Query for "knee joint" should rank D0 and D2 highest, others 0
    query_tokens = ["knee", "joint"]
    score_d0 = index.score(query_tokens, 0)
    score_d1 = index.score(query_tokens, 1)
    score_d2 = index.score(query_tokens, 2)
    score_d3 = index.score(query_tokens, 3)

    assert score_d0 > 0.0
    assert score_d2 > 0.0
    assert score_d1 == 0.0
    assert score_d3 == 0.0


def test_retrieve_always_includes_procedure_clause_and_tie_breaks():
    toy_clauses = [
        PolicyClause("POL-Z", "coverage", None, "unrelated text alpha"),
        PolicyClause("POL-A", "coverage", None, "unrelated text beta"),
        PolicyClause("POL-M", "coverage", None, "unrelated text gamma"),
        PolicyClause(
            "POL-PX0004",
            "procedure",
            "PX0004",
            "arthroscopy surgical knee reconstruction",
        ),
    ]

    results = retrieve(
        query="consultation inquiry",
        procedure_code="PX0004",
        top_k=2,
        clauses=toy_clauses,
    )

    # First result must always be the procedure clause
    assert results[0]["chunk_id"] == "POL-PX0004"
    assert results[0]["kind"] == "procedure"

    # Remaining items tie at score 0, so should be sorted alphabetically by chunk_id
    remaining_ids = [r["chunk_id"] for r in results[1:]]
    assert remaining_ids == ["POL-A", "POL-M"]


def test_retrieve_is_deterministic():
    res1 = retrieve("knee pain and surgery", "PX0004", top_k=3)
    res2 = retrieve("knee pain and surgery", "PX0004", top_k=3)
    assert res1 == res2
