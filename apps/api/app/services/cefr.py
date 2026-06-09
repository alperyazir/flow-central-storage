"""Shared, language-aware CEFR level resolution.

Used by both the classic vocabulary stage and the unified/chunked analysis so
every path assigns levels the same way:

- English: cefrpy (a curated CEFR database) is the gold standard, then the
  LLM-provided level, then a word-frequency band.
- Other languages: cefrpy is English-only and can even return a misleading
  English level for an ASCII foreign word that happens to be in its database
  (e.g. German ``Hallo``/``Schule``), so it is skipped. A deterministic
  word-frequency band (``wordfreq``) is authoritative because the LLM proved
  unreliable at non-English CEFR (it labelled ``Hallo`` as C2); the LLM value
  is only a fallback for words missing from the frequency list.
"""

from __future__ import annotations

import logging

from cefrpy import CEFRAnalyzer

logger = logging.getLogger(__name__)

_VALID_LEVELS = {"A1", "A2", "B1", "B2", "C1", "C2"}
_POS_TO_CEFRPY = {"noun": "NN", "verb": "VB", "adjective": "JJ", "adverb": "RB"}

_analyzer = CEFRAnalyzer()


def normalize_language(language: str | None) -> str:
    """Reduce a language value to a base code (``de-DE`` -> ``de``)."""
    if not language:
        return "en"
    return language.strip().lower().replace("_", "-").split("-")[0]


def cefrpy_level(word: str, pos: str) -> str:
    """CEFR level from cefrpy (English only). Returns '' when unavailable."""
    word_lower = word.lower()
    # cefrpy packs each char as a single byte; non-ASCII raises struct.error.
    if not word_lower.isascii() or not _analyzer.is_word_in_database(word_lower):
        return ""
    cefrpy_pos = _POS_TO_CEFRPY.get(pos)
    if cefrpy_pos:
        level = _analyzer.get_word_pos_level_CEFR(word_lower, cefrpy_pos)
        if level:
            return level.name if hasattr(level, "name") else str(level)
    avg = _analyzer.get_average_word_level_CEFR(word_lower)
    if avg:
        return avg.name if hasattr(avg, "name") else str(avg)
    return ""


def frequency_level(word: str, language: str) -> str:
    """Approximate a CEFR level from word frequency (language-aware).

    Maps the ``wordfreq`` Zipf score (commonness) to a CEFR band: very common
    words are beginner level, rare words advanced. Returns '' when wordfreq is
    unavailable or the word/language is unknown.
    """
    lang = normalize_language(language)
    try:
        from wordfreq import zipf_frequency
    except Exception:  # pragma: no cover - dependency missing
        return ""
    try:
        zipf = zipf_frequency(word, lang)
    except Exception:
        return ""
    if zipf <= 0:
        return ""
    if zipf >= 5.0:
        return "A1"
    if zipf >= 4.5:
        return "A2"
    if zipf >= 4.0:
        return "B1"
    if zipf >= 3.5:
        return "B2"
    if zipf >= 3.0:
        return "C1"
    return "C2"


def resolve_cefr_level(word: str, pos: str, language: str, llm_level: str = "") -> str:
    """Resolve a word's CEFR level using the best source for its language."""
    llm = (llm_level or "").strip().upper()
    if llm not in _VALID_LEVELS:
        llm = ""
    if normalize_language(language) == "en":
        return cefrpy_level(word, pos) or llm or frequency_level(word, "en")
    # Non-English: frequency is authoritative, LLM only fills the gaps.
    return frequency_level(word, language) or llm
