"""Tests for text sanitization routines."""

from triage.sanitize import sanitize_text


def test_sanitize_clean_text():
    text = "Normal clinical narrative with standard punctuation, 123 numbers."
    cleaned, modified = sanitize_text(text)
    assert cleaned == text
    assert modified is False


def test_sanitize_preserves_newline_and_tab():
    text = "Line 1\nLine 2\twith tab\r\nLine 3"
    cleaned, modified = sanitize_text(text)
    assert cleaned == text
    assert modified is False


def test_sanitize_strips_control_chars():
    # \x00 (NUL), \x07 (BEL), \x1b (ESC) are Cc
    text = "Clean text\x00 with\x07 control\x1b characters"
    cleaned, modified = sanitize_text(text)
    assert cleaned == "Clean text with control characters"
    assert modified is True


def test_sanitize_strips_unicode_cf_chars():
    # \u200b (zero-width space), \u00ad (soft hyphen), \u200e (LRM) are Cf
    text = "Hidden\u200b characters\u00ad in\u200e text"
    cleaned, modified = sanitize_text(text)
    assert cleaned == "Hidden characters in text"
    assert modified is True


def test_sanitize_caps_at_20000_chars():
    long_text = "a" * 25000
    cleaned, modified = sanitize_text(long_text)
    assert len(cleaned) == 20000
    assert modified is True


def test_sanitize_empty_string():
    cleaned, modified = sanitize_text("")
    assert cleaned == ""
    assert modified is False
