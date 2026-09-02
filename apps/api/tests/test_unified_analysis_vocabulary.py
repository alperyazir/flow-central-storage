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


# ---------------------------------------------------------------------------
# 3. The language is named, not spelled as a code
#
# Phase 1 answers with an ISO code and that value went straight into phase 2's
# prompt, so the instruction read "Write these in de". The German books limped
# along on that; a Spanish book would read "Write these in es", which is weaker
# still — "es" is a word in several languages and a code in none of them to a
# reader. The code is mapped to a name before it reaches the prompt.
# ---------------------------------------------------------------------------


from app.services.unified_analysis.service import (  # noqa: E402
    UNIFIED_ANALYSIS_PROMPT,
    language_label,
)


@pytest.mark.parametrize(
    "code,expected",
    [
        ("de", "German (Deutsch)"),
        ("es", "Spanish (Español)"),
        ("fr", "French (Français)"),
        ("tr", "Turkish (Türkçe)"),
        ("en", "English"),
    ],
)
def test_a_code_becomes_a_name(code, expected):
    assert language_label(code) == expected


def test_regional_codes_resolve_to_the_same_name():
    assert language_label("de-DE") == "German (Deutsch)"
    assert language_label("es_ES") == "Spanish (Español)"


def test_a_name_from_phase_one_is_accepted_too():
    # Phase 1 is asked for a code but does not always answer with one.
    assert language_label("German") == "German (Deutsch)"


def test_an_unknown_language_is_passed_through_not_defaulted():
    # Telling the model the wrong language is worse than telling it a code it
    # might still recognise.
    assert language_label("xx") == "xx"


def test_a_missing_language_reads_as_english():
    assert "English" in language_label(None)
    assert "English" in language_label("")


def test_the_prompt_receives_the_name_not_the_code():
    prompt = _render()  # rendered with language="German"
    assert "in de" not in prompt
    assert "Write these in German" in prompt


def test_a_spanish_book_would_get_spanish_named_in_every_slot():
    prompt = PHASE2_EXTRACT_VOCABULARY_PROMPT.format(
        module_title="La comida",
        start_page=1,
        end_page=10,
        topics="comida",
        difficulty_level="A1",
        module_text="Como pan.",
        language=language_label("es"),
        max_words_line="",
    )
    assert prompt.count("Spanish (Español)") >= 5
    assert "Turkish" in prompt  # the translation field is still Turkish


# ---------------------------------------------------------------------------
# The legacy single-call prompt had the same imbalance: Turkish emphasised with
# "ALL", the definition rule stated once and softly.
# ---------------------------------------------------------------------------


def test_the_legacy_prompt_names_the_definition_rule_before_the_turkish_one():
    definition_at = UNIFIED_ANALYSIS_PROMPT.index('The "definition" field')
    translation_at = UNIFIED_ANALYSIS_PROMPT.index('The "translation" field')
    assert definition_at < translation_at


def test_the_legacy_prompt_forbids_copying_the_translation():
    assert "never copy it into" in UNIFIED_ANALYSIS_PROMPT
    assert "ONLY field in Turkish" in UNIFIED_ANALYSIS_PROMPT


def test_the_legacy_prompt_gives_a_non_german_example_too():
    # So the rule does not read as being about German specifically.
    assert "a Spanish book Spanish ones" in UNIFIED_ANALYSIS_PROMPT
