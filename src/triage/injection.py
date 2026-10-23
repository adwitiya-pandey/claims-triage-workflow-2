"""Prompt injection detection heuristics and hidden character scan per SPEC 11."""

import re
import unicodedata

INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}\b(instructions?|rules?|guidelines?|polic(?:y|ies)|prompt)\b",
    r"\bsystem prompt\b",
    r"\bnew instructions?\b",
    r"\byou are now\b",
    r"\bact as\b",
    r"\bdo not (escalate|flag|review)\b",
    r"\bdon[’']?t (escalate|flag|review)\b",
    r"\b(mark|set|route|classify|label|treat)\b[^.\n]{0,30}\bfast[-_ ]?track\w*\b",
    r"</?\s*(system|assistant|instructions?)\s*>",
    r"\[/?INST\]",
    r"\"(tool|function|name)\"\s*:\s*\"[a-z_]+\"",
    r"\bas an ai\b",
    r"\bjailbreak\b",
]

INJ_RE = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]


def has_hidden_chars(text: str) -> bool:
    """Check for format characters (Cf) or non-standard control characters (Cc)."""
    return any(
        unicodedata.category(c) == "Cf"
        or (unicodedata.category(c) == "Cc" and c not in "\n\t\r")
        for c in text
    )


def scan_injection(text: str) -> list[str]:
    """Scan raw claim text for injection attempts and hidden character obfuscation.

    Returns:
        List of flag strings: ['PATTERN'] and/or ['HIDDEN_CHARS'].
    """
    flags: list[str] = []
    if any(r.search(text) for r in INJ_RE):
        flags.append("PATTERN")
    if has_hidden_chars(text):
        flags.append("HIDDEN_CHARS")
    return flags
