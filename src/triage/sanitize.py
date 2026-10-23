"""Sanitization routines for raw claim text per SPEC 11."""

import unicodedata


def sanitize_text(text: str, max_chars: int = 20000) -> tuple[str, bool]:
    """Sanitize raw claim text.

    SPEC 11:
    - Strip control characters (category 'Cc') except newline, carriage return, and tab.
    - Strip Unicode category 'Cf' (format/hidden characters like zero-width spaces).
    - Cap at max_chars characters (default 20,000).
    - Report whether anything was removed or truncated.
    """
    if not text:
        return "", False

    cleaned_chars = []
    removed = False

    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "Cf":
            removed = True
            continue
        if cat == "Cc" and ch not in "\n\t\r":
            removed = True
            continue
        cleaned_chars.append(ch)

    cleaned_text = "".join(cleaned_chars)
    if len(cleaned_text) > max_chars:
        cleaned_text = cleaned_text[:max_chars]
        removed = True

    return cleaned_text, removed
