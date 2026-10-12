"""Tests for structure rules that OCR output tends to break."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from med_agent.context_retriever import is_translatable_text  # noqa: E402
from med_agent.document_structurer import _heading_level  # noqa: E402


class HeadingDetectionTests(unittest.TestCase):
    def test_recognizes_real_numbered_headings(self) -> None:
        for text in ("1. Introduction", "2. Methods", "3. Results", "4.2.1 Deep dive"):
            with self.subTest(text=text):
                self.assertIsNotNone(_heading_level(text))

    def test_recognizes_known_section_names(self) -> None:
        for text in ("Abstract", "METHODS", "references", "Discussion"):
            with self.subTest(text=text):
                self.assertEqual(_heading_level(text), 1)

    def test_ignores_chart_axis_noise(self) -> None:
        # These are the OCR readings of figure axis labels that used to be
        # promoted to document headings and sent to the translator.
        for text in ("2 500 2.0", "6 ooo", "10 urn 1.0", "500 0.0", "2020 results"):
            with self.subTest(text=text):
                self.assertIsNone(_heading_level(text))


class TranslatableTextTests(unittest.TestCase):
    def test_keeps_prose_headings_and_captions(self) -> None:
        for text in (
            "Sepsis is a life-threatening condition.",
            "Abstract",
            "Methods",
            "Figure 1",
            "Table 2",
            "Patients were enrolled between 2019 and 2021.",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_translatable_text(text))

    def test_drops_symbols_units_and_axis_labels(self) -> None:
        for text in ("***", "20 nm", "2 500 2.0", "6 ooo", "500 0.0", "1.8"):
            with self.subTest(text=text):
                self.assertFalse(is_translatable_text(text))

    def test_keeps_chinese_and_caption_text(self) -> None:
        self.assertTrue(is_translatable_text("图 1 患者入组流程图"))
        self.assertTrue(is_translatable_text("Fig. 1 TLR9 mediates NETS recognition."))

    def test_skips_links_and_dois(self) -> None:
        # A DOI must stay byte-identical. Sending it to the translator made the
        # model reflow the digits, which then failed the number check and could
        # not be repaired, blocking the whole document at the approval gate.
        for text in (
            "www.nature.com/ijos\nInternational Journal of Oral Science",
            "International Journal of Oral Science (2026) 18:57\n; https://doi.org/10.1038/s41368-026-00454-3",
            "doi:10.1038/s41591-020-0001-2",
            "See https://example.org/data for details.",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_translatable_text(text))

    def test_skips_reference_entries_but_keeps_prose(self) -> None:
        # Reference lists carry the volume(issue):pages shape.
        for text in (
            "1. Tonetti MS, Greenwell H, Kornman KS. Staging and grading of "
            "periodontitis. J Clin Periodontol. 2018;45(Suppl 20):S149-S161.",
            "2. Hajishengallis G. Periodontitis: from microbial immune subversion "
            "to systemic inflammation. Nat Rev Immunol. 2015;15(1):30-44.",
        ):
            with self.subTest(text=text):
                self.assertFalse(is_translatable_text(text))

    def test_keeps_number_heavy_prose(self) -> None:
        # A raw digit-density rule condemned these; they are ordinary results text.
        for text in (
            "The 28-day mortality rate was 12.5% in the treatment group and 18.9% in controls.",
            "Median lactate was 2.4 mmol/L (IQR 1.6-3.8) and the AUC was 0.82 (95% CI 0.76-0.88).",
            "Patients were enrolled between 2019 and 2021, a total of 1248 cases were analyzed.",
            "A total of 120 patients (60 in each group) completed the 12-week follow-up.",
            "The study was conducted in 2019: 45 patients were enrolled and 30 completed.",
            "The dose ranged from 2-4 mg/kg and the interval was 10-20 days.",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_translatable_text(text))


if __name__ == "__main__":
    unittest.main()
