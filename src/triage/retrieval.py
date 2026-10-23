"""Policy corpus loader, validator, BM25 indexing, and retrieval engine."""

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_KINDS = {"coverage", "exclusion", "procedure", "documentation", "process"}
STOP_WORDS = {
    "a",
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "by",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "he",
    "her",
    "here",
    "hers",
    "herself",
    "him",
    "himself",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "itself",
    "me",
    "more",
    "most",
    "my",
    "myself",
    "no",
    "nor",
    "not",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "other",
    "ought",
    "our",
    "ours",
    "ourselves",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "theirs",
    "them",
    "themselves",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "to",
    "too",
    "under",
    "until",
    "up",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "with",
    "would",
    "you",
    "your",
    "yours",
    "yourself",
    "yourselves",
}


@dataclass(frozen=True)
class PolicyClause:
    """A policy clause from the markdown corpus."""

    chunk_id: str
    kind: str
    procedure_code: str | None
    text: str


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase alphanumeric terms excluding stop words."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return [t for t in tokens if t not in STOP_WORDS]


def parse_clauses_markdown(content: str) -> list[PolicyClause]:
    """Parse clauses markdown content into PolicyClause objects and validate."""
    blocks = re.split(r"(?m)^##\s+", content)
    clauses: list[PolicyClause] = []
    seen_ids: set[str] = set()

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        header = lines[0].strip()
        chunk_id = header

        metadata_lines = []
        body_lines = []
        in_body = False

        for line in lines[1:]:
            if line.strip() == "---":
                in_body = True
                continue
            if in_body:
                body_lines.append(line)
            else:
                metadata_lines.append(line)

        kind: str | None = None
        procedure_code: str | None = None

        for m_line in metadata_lines:
            m_line = m_line.split("#")[0].strip()  # strip inline comments
            if not m_line:
                continue
            if ":" in m_line:
                k, v = m_line.split(":", 1)
                k = k.strip()
                v = v.strip()
                if k == "kind":
                    kind = v
                elif k == "procedure_code":
                    procedure_code = v if v else None

        text = "\n".join(body_lines).strip()
        if not chunk_id:
            raise ValueError("Malformed clause: missing chunk_id header")
        if chunk_id in seen_ids:
            raise ValueError(f"Duplicate clause ID found: {chunk_id}")
        seen_ids.add(chunk_id)

        if not kind or kind not in VALID_KINDS:
            raise ValueError(f"Clause {chunk_id} has invalid or missing kind: {kind}")

        if kind == "procedure":
            if not procedure_code:
                raise ValueError(
                    f"Procedure clause {chunk_id} must have procedure_code set"
                )
        else:
            if procedure_code is not None:
                raise ValueError(
                    f"Non-procedure clause {chunk_id} should not have procedure_code"
                )

        if not text:
            raise ValueError(f"Clause {chunk_id} has empty body text")

        clauses.append(
            PolicyClause(
                chunk_id=chunk_id,
                kind=kind,
                procedure_code=procedure_code,
                text=text,
            )
        )

    # Validate that every procedure code PX0001..PX0012 has exactly one clause
    proc_codes = {f"PX{i:04d}" for i in range(1, 13)}
    found_proc_codes = {c.procedure_code for c in clauses if c.kind == "procedure"}
    missing = proc_codes - found_proc_codes
    if missing:
        raise ValueError(f"Missing procedure clauses for codes: {sorted(missing)}")

    return clauses


def load_corpus(path: str | Path = "data/policy/clauses.md") -> list[PolicyClause]:
    """Load policy corpus from disk."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Policy corpus file not found: {path}")
    content = p.read_text(encoding="utf-8")
    return parse_clauses_markdown(content)


def compute_corpus_sha256(clauses: list[PolicyClause]) -> str:
    """Compute deterministic SHA256 hash over canonical representation of clauses."""
    hasher = hashlib.sha256()
    sorted_clauses = sorted(clauses, key=lambda c: c.chunk_id)
    for c in sorted_clauses:
        chunk_rep = f"{c.chunk_id}|{c.kind}|{c.procedure_code or ''}|{c.text}\n"
        hasher.update(chunk_rep.encode("utf-8"))
    return hasher.hexdigest()


class BM25Index:
    """Hand-written BM25 ranking implementation per SPEC 9."""

    def __init__(
        self, clauses: list[PolicyClause], k1: float = 1.5, b: float = 0.75
    ) -> None:
        self.k1 = k1
        self.b = b
        self.clauses = clauses
        self.N = len(clauses)

        self.doc_tokens: list[list[str]] = [tokenize(c.text) for c in clauses]
        self.doc_lens: list[int] = [len(tokens) for tokens in self.doc_tokens]
        self.avg_doc_len: float = sum(self.doc_lens) / self.N if self.N > 0 else 1.0

        # Document frequencies for terms
        self.df: dict[str, int] = {}
        for tokens in self.doc_tokens:
            for term in set(tokens):
                self.df[term] = self.df.get(term, 0) + 1

    def idf(self, term: str) -> float:
        """SPEC 9: idf = ln(1 + (N - n + 0.5) / (n + 0.5))."""
        n = self.df.get(term, 0)
        return math.log(1.0 + (self.N - n + 0.5) / (n + 0.5))

    def score(self, query_tokens: list[str], doc_idx: int) -> float:
        """Calculate BM25 score for a document given query tokens."""
        doc_tokens = self.doc_tokens[doc_idx]
        doc_len = self.doc_lens[doc_idx]
        if doc_len == 0:
            return 0.0

        # Term frequencies in document
        tf_dict: dict[str, int] = {}
        for t in doc_tokens:
            tf_dict[t] = tf_dict.get(t, 0) + 1

        total_score = 0.0
        for term in query_tokens:
            if term not in tf_dict:
                continue
            tf = tf_dict[term]
            term_idf = self.idf(term)
            numerator = tf * (self.k1 + 1.0)
            denominator = tf + self.k1 * (
                1.0 - self.b + self.b * (doc_len / self.avg_doc_len)
            )
            total_score += term_idf * (numerator / denominator)

        return total_score


# Global cached corpus and index for fast reuse
_CACHED_CORPUS: list[PolicyClause] | None = None
_CACHED_INDEX: BM25Index | None = None


def get_default_index() -> BM25Index:
    """Get or load default BM25 index."""
    global _CACHED_CORPUS, _CACHED_INDEX
    if _CACHED_INDEX is None:
        _CACHED_CORPUS = load_corpus()
        _CACHED_INDEX = BM25Index(_CACHED_CORPUS)
    return _CACHED_INDEX


def retrieve(
    query: str,
    procedure_code: str,
    top_k: int = 4,
    clauses: list[PolicyClause] | None = None,
) -> list[dict[str, Any]]:
    """Retrieve policy chunks relevant to the claim.

    SPEC 9:
    Query = extracted procedure_description + procedure_code.
    Returns: procedure clause (always) + top_k other clauses ranked by BM25.
    Ties break by chunk_id ascending.
    """
    if clauses is None:
        index = get_default_index()
        all_clauses = index.clauses
    else:
        index = BM25Index(clauses)
        all_clauses = clauses

    query_tokens = tokenize(f"{query} {procedure_code}")

    # Find the procedure clause
    proc_clause_idx = -1
    for idx, c in enumerate(all_clauses):
        if c.kind == "procedure" and c.procedure_code == procedure_code:
            proc_clause_idx = idx
            break

    results: list[dict[str, Any]] = []

    # Score other clauses
    scored_candidates = []
    for idx, c in enumerate(all_clauses):
        if idx == proc_clause_idx:
            continue
        sc = index.score(query_tokens, idx)
        scored_candidates.append((sc, c.chunk_id, c))

    # Sort descending by score, tie-break ascending by chunk_id
    scored_candidates.sort(key=lambda item: (-item[0], item[1]))

    # Always add procedure clause first
    if proc_clause_idx >= 0:
        proc_clause = all_clauses[proc_clause_idx]
        proc_score = index.score(query_tokens, proc_clause_idx)
        results.append(
            {
                "chunk_id": proc_clause.chunk_id,
                "kind": proc_clause.kind,
                "text": proc_clause.text,
                "score": round(proc_score, 4),
            }
        )

    # Add top_k other clauses
    for sc, _, c in scored_candidates[:top_k]:
        results.append(
            {
                "chunk_id": c.chunk_id,
                "kind": c.kind,
                "text": c.text,
                "score": round(sc, 4),
            }
        )

    return results
