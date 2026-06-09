"""Tests for language-aware CEFR level resolution."""

from __future__ import annotations

import pytest

from app.services.cefr import normalize_language, resolve_cefr_level


def test_normalize_language():
    assert normalize_language("de-DE") == "de"
    assert normalize_language("ES_es") == "es"
    assert normalize_language("") == "en"
    assert normalize_language(None) == "en"


def test_non_english_frequency_overrides_wrong_llm_level():
    """The reported bug: LLM said C2 for 'Hallo' — frequency must win."""
    pytest.importorskip("wordfreq")
    assert resolve_cefr_level("Hallo", "interjection", "de", "C2") == "A1"
    assert resolve_cefr_level("casa", "noun", "es", "C1") == "A1"
    assert resolve_cefr_level("maison", "noun", "fr", "B2") == "A1"


def test_non_english_rare_word_is_advanced():
    pytest.importorskip("wordfreq")
    assert resolve_cefr_level("Quantenmechanik", "noun", "de", "") in {"C1", "C2"}


def test_non_english_falls_back_to_llm_when_unknown_word():
    """Word missing from the frequency list keeps a valid LLM level."""
    assert resolve_cefr_level("zzxqfakeword", "noun", "de", "B1") == "B1"
    # Invalid LLM level and unknown word -> empty.
    assert resolve_cefr_level("zzxqfakeword", "noun", "de", "banana") == ""


def test_english_prefers_cefrpy():
    # cefrpy is the curated source for English; it overrides the LLM value.
    assert resolve_cefr_level("beautiful", "adjective", "en", "C2") == "A1"
