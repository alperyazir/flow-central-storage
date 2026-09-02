"""Phase 2 of chunked analysis: language of the fields, and not losing a module.

Two production failures sit behind these tests, both from book 258
(Daumen Hoch 1), both silent — the job reported "completed" either way.

1. A German module came back with every `definition` holding the Turkish
   translation verbatim. The prompt named the language only inside the JSON
   schema, as a placeholder ("clear explanation in the content's language"),
   and its closing bullet was "Provide Turkish translations for every word" —
   the last thing the model reads.

2. Module 6 ended up with zero words. Its answer was cut off mid-JSON at about
   13k characters (max_tokens was 4000), so parsing failed; all three retries
   sent the identical request at the same temperature and failed identically.
   Thirty-six words disappeared and the module still counted as done.
"""

import inspect

import pytest

from app.services.unified_analysis.service import (
    PHASE2_EXTRACT_VOCABULARY_PROMPT,
    UnifiedAnalysisService,
)


def _render(max_words_line: str = "") -> str:
    return PHASE2_EXTRACT_VOCABULARY_PROMPT.format(
        module_title="Essen und Trinken",
        start_page=64,
        end_page=75,
        topics="Essen, Trinken",
        difficulty_level="A1",
        module_text="Ich esse Brot.",
        language="German",
        max_words_line=max_words_line,
    )


# ---------------------------------------------------------------------------
# 1. Which field is in which language
# ---------------------------------------------------------------------------


def test_the_prompt_states_the_language_as_an_instruction_not_a_placeholder():
    prompt = _render()
    # Its own section, ahead of the task — not buried in the schema.
    assert "## Language of each field" in prompt
    assert "Write these in German" in prompt


def test_turkish_is_named_as_the_only_turkish_field():
    prompt = _render()
    assert "ONLY\nTurkish field" in prompt or "ONLY Turkish field" in prompt


def test_the_prompt_forbids_copying_the_translation_into_the_definition():
    # This is exactly what happened: definition == translation, character for
    # character, for all 36 words of the module.
    prompt = _render()
    assert "Never copy `translation` into `definition`" in prompt
    assert "never Turkish" in prompt


def test_the_closing_instruction_is_about_the_definition_language():
    # It used to end on "Provide Turkish translations for every word", which is
    # the worst possible last word for a field that must not be Turkish.
    prompt = _render()
    tail = prompt[-400:]
    assert "re-read your `definition` values" in tail
    assert "must be\n  in German" in tail or "must be in German" in tail


def test_the_schema_placeholders_name_the_language_too():
    prompt = _render()
    assert '"definition": "explanation of the word, in German' in prompt
    assert "NOT the Turkish" in prompt


# ---------------------------------------------------------------------------
# 2. Not losing a module to a truncated answer
# ---------------------------------------------------------------------------


def test_the_token_ceiling_is_above_what_a_full_module_needs():
    # The failing answers were ~13k characters; 4000 tokens could not hold one.
    source = inspect.getsource(UnifiedAnalysisService._phase2_extract_vocabulary)
    assert "max_tokens=8000" in source
    assert "max_tokens=4000" not in source


def test_retries_ask_for_less_rather_than_repeating_the_same_request():
    caps = UnifiedAnalysisService.RETRY_WORD_CAPS
    assert caps[0] is None, "the first attempt should still ask for everything"
    assert all(c is not None for c in caps[1:]), "later attempts must cap"
    assert list(caps[1:]) == sorted(caps[1:], reverse=True), "caps must shrink"


def test_no_cap_means_no_extra_instruction():
    assert "AT MOST" not in _render()


def test_a_cap_tells_the_model_to_return_fewer_words():
    prompt = _render("\n4. Return AT MOST 60 words — the most important ones.")
    assert "AT MOST 60" in prompt


@pytest.mark.parametrize("attempt,expected", [(0, None), (1, 60), (2, 30)])
def test_each_attempt_gets_its_own_cap(attempt, expected):
    assert UnifiedAnalysisService.RETRY_WORD_CAPS[attempt] == expected


def test_phase2_accepts_a_word_cap():
    sig = inspect.signature(UnifiedAnalysisService._phase2_extract_vocabulary)
    assert "max_words" in sig.parameters
    assert sig.parameters["max_words"].default is None
