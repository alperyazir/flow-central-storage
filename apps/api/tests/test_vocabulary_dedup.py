"""Tests for the shared vocabulary deduplication utility."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.vocabulary_extraction.dedup import deduplicate_by_word


@dataclass
class _Item:
    word: str
    tag: str = ""


def test_deduplicate_preserves_first_occurrence():
    items = [
        _Item(word="ant", tag="first"),
        _Item(word="ant", tag="second"),
        _Item(word="dog", tag="first"),
    ]
    result = deduplicate_by_word(items, lambda i: i.word)
    assert [i.tag for i in result] == ["first", "first"]


def test_deduplicate_is_case_insensitive():
    items = [_Item(word="Ant"), _Item(word="ant"), _Item(word="ANT")]
    result = deduplicate_by_word(items, lambda i: i.word)
    assert len(result) == 1
    assert result[0].word == "Ant"


def test_deduplicate_strips_whitespace():
    items = [_Item(word=" ant "), _Item(word="ant")]
    result = deduplicate_by_word(items, lambda i: i.word)
    assert len(result) == 1


def test_deduplicate_skips_empty_words():
    items = [_Item(word=""), _Item(word="   "), _Item(word="ant")]
    result = deduplicate_by_word(items, lambda i: i.word)
    assert len(result) == 1
    assert result[0].word == "ant"


def test_deduplicate_empty_input():
    assert deduplicate_by_word([], lambda i: i.word) == []


def test_deduplicate_preserves_order():
    items = [_Item(word="zebra"), _Item(word="ant"), _Item(word="dog")]
    result = deduplicate_by_word(items, lambda i: i.word)
    assert [i.word for i in result] == ["zebra", "ant", "dog"]


def test_deduplicate_works_with_tuples():
    # Mirrors unified_analysis usage: (word, module_id, module_title).
    entries = [
        ("ant", 1, "Animals"),
        ("ant", 2, "Nature"),
        ("dog", 1, "Animals"),
    ]
    result = deduplicate_by_word(entries, lambda e: e[0])
    assert result == [("ant", 1, "Animals"), ("dog", 1, "Animals")]
