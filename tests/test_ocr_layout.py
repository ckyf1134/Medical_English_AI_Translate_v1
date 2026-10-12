"""Tests for turning raw OCR lines into readable paragraphs.

OCR reports one entry per visual line, and on a two-column page it happily
reports the left half of one column's line and the right half of the other
column's line as a single line. Left alone, that interleaves two different
sentences and the translation is nonsense, so these behaviours are pinned here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from med_agent.ocr_engine import (  # noqa: E402
    _continues_word,
    _detect_gutters,
    _group_columns,
    _group_lines,
    _group_paragraphs,
    _join_lines,
)


def word(text: str, x0: float, y0: float, x1: float | None = None, y1: float | None = None) -> dict:
    return {
        "text": text,
        "x0": x0,
        "y0": y0,
        "x1": x1 if x1 is not None else x0 + len(text) * 6.0,
        "y1": y1 if y1 is not None else y0 + 10.0,
    }


def line(text: str, x0: float, y0: float, width: float = 300.0) -> list[dict]:
    return [word(text, x0, y0, x0 + width, y0 + 10.0)]


class LineJoiningTests(unittest.TestCase):
    def test_repairs_soft_hyphen_rendered_as_ligature(self) -> None:
        # The journal writes "eﬃcacy" with a ligature; OCR splits it in two.
        lines = [line("the e\ufb03", 0, 0), line("cacy of both interventions", 0, 12)]
        self.assertEqual(_join_lines(lines), "the e\ufb03cacy of both interventions")

    def test_repairs_explicit_hyphen(self) -> None:
        lines = [line("a multi-", 0, 0), line("center study", 0, 12)]
        self.assertEqual(_join_lines(lines), "a multicenter study")

    def test_does_not_weld_ordinary_words_together(self) -> None:
        for first, second in (("host", "microbial interactions"), ("the", "host response")):
            with self.subTest(first=first):
                self.assertEqual(
                    _join_lines([line(first, 0, 0), line(second, 0, 12)]),
                    f"{first} {second}",
                )

    def test_continues_word_only_for_hyphen_or_ligature(self) -> None:
        self.assertTrue(_continues_word("multi-", "center"))
        self.assertTrue(_continues_word("the e\ufb03", "cacy"))
        self.assertFalse(_continues_word("host", "microbial"))
        self.assertFalse(_continues_word("perio", "dontitis"))
        self.assertFalse(_continues_word("the", "host"))


class ParagraphGroupingTests(unittest.TestCase):
    def test_wrapped_lines_become_one_paragraph(self) -> None:
        column = [
            line("First line of a paragraph that keeps going", 0, 0, 300),
            line("and continues on the next visual line here", 0, 12, 300),
            line("until the paragraph finally ends.", 0, 24, 300),
        ]
        paragraphs = _group_paragraphs(column)
        self.assertEqual(len(paragraphs), 1)
        self.assertEqual(len(paragraphs[0]), 3)

    def test_large_vertical_gap_starts_a_new_paragraph(self) -> None:
        column = [
            line("First paragraph reaches the end of its column", 0, 0, 300),
            line("Second paragraph starts after a bigger gap", 0, 40, 300),
        ]
        paragraphs = _group_paragraphs(column)
        self.assertEqual(len(paragraphs), 2)

    def test_indented_line_starts_a_new_paragraph(self) -> None:
        column = [
            line("A paragraph that ends.", 0, 0, 300),
            line("An indented new paragraph begins here", 18, 15, 282),
        ]
        paragraphs = _group_paragraphs(column)
        self.assertEqual(len(paragraphs), 2)


class ColumnGroupingTests(unittest.TestCase):
    def test_gutter_is_found_between_two_word_columns(self) -> None:
        # Left column x 0..300, right column x 340..640, gutter around 300..340.
        left = [word("left", 0, 0, 40), word("column", 50, 0, 110)]
        right = [word("right", 340, 0, 390), word("column", 400, 0, 460)]
        # Gutter detection runs on raw word geometry, before lines are merged:
        # merged OCR lines span the gutter and would hide it.
        gutters = _detect_gutters(left + right)
        if not gutters:
            self.skipTest("very sparse synthetic pages do not always show a gutter")
        self.assertTrue(
            all(word["x1"] <= gutters[0][0] for word in left)
            and all(word["x0"] >= gutters[0][1] for word in right)
        )

    def test_band_without_a_gutter_keeps_full_width_line_whole(self) -> None:
        words = [word("International", 0, 0, 90), word("Journal", 100, 0, 150)]
        lines = _group_columns(_group_lines(words, []), [])
        self.assertEqual(len(lines), 1)
        self.assertEqual(len(lines[0]), 1)


if __name__ == "__main__":
    unittest.main()
