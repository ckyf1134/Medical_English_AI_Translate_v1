"""Tests for PDF text-layer detection and OCR handling."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from med_agent.pdf_extractor import (  # noqa: E402
    NoTextLayerError,
    extract_pdf,
    inspect_text_layer,
    load_ocr_config,
)


def _write_text_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "Sepsis is a life-threatening condition.", fontsize=12)
    page.insert_text((72, 130), "Lactate and platelet count were measured.", fontsize=12)
    document.save(path)
    document.close()


def _write_scanned_pdf(path: Path) -> None:
    """A PDF holding only a full-page image, like a 'Print to PDF' scan."""
    source = pymupdf.open()
    page = source.new_page(width=200, height=120)
    page.insert_text((20, 60), "Published online 2026", fontsize=20)
    pixmap = page.get_pixmap(dpi=150)
    source.close()

    document = pymupdf.open()
    page = document.new_page(width=200, height=120)
    page.insert_image(page.rect, pixmap=pixmap)
    document.save(path)
    document.close()


class TextLayerTests(unittest.TestCase):
    def test_text_pdf_reports_text_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.pdf"
            _write_text_pdf(path)
            report = inspect_text_layer(path)
            self.assertTrue(report["has_text_layer"])
            self.assertEqual(report["pages_without_text"], 0)

    def test_scanned_pdf_reports_missing_text_layer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            _write_scanned_pdf(path)
            report = inspect_text_layer(path)
            self.assertFalse(report["has_text_layer"])
            self.assertEqual(report["total_characters"], 0)

    def test_text_pdf_extracts_without_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.pdf"
            _write_text_pdf(path)
            result = extract_pdf(path, ocr=True)
            self.assertFalse(result["extraction"]["ocr"]["applied"])
            self.assertTrue(result["extraction"]["has_text_layer"])

    def test_scanned_pdf_without_ocr_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            _write_scanned_pdf(path)
            with self.assertRaises(NoTextLayerError) as context:
                extract_pdf(path, ocr=False)
            # The message must explain the cause, not just report a failure.
            self.assertIn("没有文字层", str(context.exception))

    def test_ocr_config_defaults_when_file_missing(self) -> None:
        config = load_ocr_config(Path("does-not-exist.json"))
        self.assertTrue(config["enabled"])
        self.assertGreater(config["dpi"], 0)
        self.assertTrue(config["languages"])

    def test_ocr_config_reads_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ocr.json"
            path.write_text('{"enabled": false, "dpi": 150}', encoding="utf-8")
            config = load_ocr_config(path)
            self.assertFalse(config["enabled"])
            self.assertEqual(config["dpi"], 150)


@unittest.skipUnless(
    Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").is_file(),
    "Windows OCR is only available on Windows",
)
class WindowsOcrTests(unittest.TestCase):
    def test_scanned_pdf_is_ocrd_into_text_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            _write_scanned_pdf(path)
            result = extract_pdf(path, ocr=True)
            extraction = result["extraction"]
            self.assertTrue(extraction["ocr"]["applied"])
            self.assertEqual(extraction["ocr"]["engine"], "windows-media-ocr")
            texts = [
                block["text"]
                for page in result["pages"]
                for block in page["blocks"]
                if block["type"] == "text"
            ]
            self.assertTrue(texts, "OCR should produce text blocks")
            # OCR of a small rendered page is not always letter perfect, so only
            # require that it read the words rather than the digits.
            self.assertIn("Published", " ".join(texts))


if __name__ == "__main__":
    unittest.main()
