"""Shared vocabulary deduplication utility."""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def deduplicate_by_word(
    items: list[T],
    get_word: Callable[[T], str],
) -> list[T]:
    """Deduplicate items by their word value (case-insensitive, whitespace-stripped).

    Keeps the first occurrence of each word. Items whose word is empty after
    stripping are skipped.

    Args:
        items: Items to deduplicate.
        get_word: Callable that returns the word string for an item.

    Returns:
        Deduplicated list preserving the order of first occurrence.
    """
    seen: set[str] = set()
    result: list[T] = []
    for item in items:
        key = get_word(item).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result
