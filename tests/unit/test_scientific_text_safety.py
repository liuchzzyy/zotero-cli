"""Do not repair ambiguous scientific values or recursively substitute user metadata."""

from __future__ import annotations

import pytest

from zotero_cli.core.notes.templates import format_template
from zotero_cli.core.notes.text import join_spaced_digits


@pytest.mark.parametrize(
    "text",
    ["voltages 1.0 1.2 1.4 V", "Table row 10 20 30", "References [1 2]", "$5 . 6 3 . 7 4 4$", "capacity 1 7 2 . 3 mAh"],
)
def test_ambiguous_numeric_sequences_are_preserved(text: str) -> None:
    assert join_spaced_digits(text) == text


def test_unambiguous_single_math_number_can_be_rejoined() -> None:
    assert join_spaced_digits("capacity $2 0 1 . 6$ mAh") == "capacity $201.6$ mAh"


def test_inserted_metadata_is_not_interpreted_as_another_placeholder() -> None:
    assert format_template("{title} | {authors}", title="literal {authors}", authors="A") == "literal {authors} | A"
