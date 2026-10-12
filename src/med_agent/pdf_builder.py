"""Rebuild a PDF while keeping original text-block coordinates."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pymupdf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OCR_CONFIG_PATH = PROJECT_ROOT / "configs" / "ocr.json"
BUILTIN_CJK_FONT = "china-s"
# The built-in CJK font renders Latin letters and digits full width, which makes
# mixed Chinese/English text look broken, so prefer a real CJK font when present.
DEFAULT_FONT_CANDIDATES = (
    "vendor/fonts/NotoSansSC-Regular.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/Deng.ttf",
)
RESOLVED_CJK_FONT: tuple[str, str | None] | None = None

TRANSLATABLE_ROLES = {
    "title",
    "heading",
    "paragraph",
    "table_caption",
    "figure_caption",
}

# OCR recognises single text lines, so its boxes are a tight fit for Chinese
# text and stay well short of the column width. Scanned pages also keep the
# original English as pixels, so the box has to be big enough to white it out.
OCR_COLUMN_GAP_RATIO = 0.12
OCR_MIN_COLUMN_WIDTH = 80.0
OCR_BAND_GAP = 2.2
OCR_WIDTH_MARGIN = 4.0
OCR_MIN_HEIGHT_FACTOR = 1.6
OCR_MIN_FONT_SIZE = 5.5
MIN_FONT_SIZE = 4.5
MAX_FONT_SIZE = 18.0


def _is_ocr_block(block: dict[str, Any]) -> bool:
    return "windows-media-ocr" in (block.get("style", {}).get("fonts") or [])


def _configured_font_settings() -> dict[str, Any]:
    try:
        raw = json.loads(OCR_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    font = raw.get("font")
    return font if isinstance(font, dict) else {}


def resolve_cjk_font() -> tuple[str, str | None]:
    """Pick a CJK font that renders Latin text proportionally.

    Returns ``(fontname, fontfile)``: a built-in font name, or a reference name
    that must be registered from ``fontfile`` on the page before use.
    """
    global RESOLVED_CJK_FONT
    if RESOLVED_CJK_FONT is not None:
        return RESOLVED_CJK_FONT

    settings = _configured_font_settings()
    candidates = settings.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        candidates = list(DEFAULT_FONT_CANDIDATES)
    fallback = str(settings.get("fallback") or BUILTIN_CJK_FONT)

    for entry in candidates:
        if not entry:
            continue
        path = Path(str(entry)).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                handle.read(1)
        except OSError:
            continue
        # Fail loudly here rather than in the middle of writing a page.
        try:
            probe = pymupdf.open()
            page = probe.new_page()
            page.insert_font(fontname="cjkprobe", fontfile=str(path))
            probe.close()
        except Exception as error:  # pymupdf raises several types for bad fonts
            print(f"字体不可用，已跳过：{path}（{error}）", file=sys.stderr)
            continue
        RESOLVED_CJK_FONT = ("cjk", str(path))
        return RESOLVED_CJK_FONT

    RESOLVED_CJK_FONT = (fallback, None)
    return RESOLVED_CJK_FONT


def apply_cjk_font(page: pymupdf.Page) -> str:
    """Register the resolved font on a page and return the font name to use."""
    fontname, fontfile = resolve_cjk_font()
    if fontfile:
        page.insert_font(fontname=fontname, fontfile=fontfile)
    return fontname


def _ocr_bands(blocks: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split OCR lines into bands separated by generous vertical whitespace.

    A page title or a wide banner spans the whole text width; keeping it in its
    own band stops the column logic from stretching narrow lines across it.
    """
    ordered = sorted(blocks, key=lambda block: (block["bbox"][1], block["bbox"][0]))
    bands: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_bottom: float | None = None
    for block in ordered:
        top, bottom = float(block["bbox"][1]), float(block["bbox"][3])
        height = max(1.0, bottom - top)
        if previous_bottom is not None and current and (top - previous_bottom) > height * OCR_BAND_GAP:
            bands.append(current)
            current = []
        current.append(block)
        previous_bottom = bottom if previous_bottom is None else max(previous_bottom, bottom)
    if current:
        bands.append(current)
    return bands


def _ocr_lines_in_band(band: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group the lines of one band into columns by their horizontal gaps."""
    ordered = sorted(band, key=lambda block: (block["bbox"][0], block["bbox"][1]))
    columns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_right = 0.0
    for block in ordered:
        left, right = float(block["bbox"][0]), float(block["bbox"][2])
        if (
            current
            and (left - current_right) > max(OCR_MIN_COLUMN_WIDTH * OCR_COLUMN_GAP_RATIO, 12.0)
        ):
            columns.append(current)
            current = []
            current_right = 0.0
        current.append(block)
        current_right = max(current_right, right)
    if current:
        columns.append(current)
    return columns


def _ocr_target_rects(blocks: list[dict[str, Any]]) -> dict[str, pymupdf.Rect]:
    """Work out, for each OCR line, the column box its translation should fill."""
    targets: dict[str, pymupdf.Rect] = {}
    for band in _ocr_bands(blocks):
        band_left = min(float(block["bbox"][0]) for block in band)
        band_right = max(float(block["bbox"][2]) for block in band)
        band_width = band_right - band_left
        for column in _ocr_lines_in_band(band):
            left = min(float(block["bbox"][0]) for block in column)
            right = max(float(block["bbox"][2]) for block in column)
            # A line that already spans the band is a title or banner; keep it wide.
            is_full_width = band_width > 0 and (right - left) / band_width >= 0.85
            column_right = band_right if is_full_width else right

            for index, block in enumerate(column):
                x0, y0, x1, _ = (float(value) for value in block["bbox"])
                width_right = max(column_right, x1)
                next_top = (
                    float(column[index + 1]["bbox"][1]) if index + 1 < len(column) else None
                )
                limit = next_top - 1.0 if next_top is not None else None

                target_left = max(0.0, left - OCR_WIDTH_MARGIN)
                target_right = max(width_right, target_left + 40.0)
                target_height = max((float(block["bbox"][3]) - y0), 1.0) * OCR_MIN_HEIGHT_FACTOR
                target_bottom = y0 + target_height
                if limit is not None:
                    target_bottom = min(target_bottom, max(y0 + 4.0, limit))
                targets[block["id"]] = pymupdf.Rect(target_left, y0 - 1.0, target_right, target_bottom)
    return targets


def _block_id_map(structured: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        block["id"]: block
        for page in structured.get("pages", [])
        for block in page.get("blocks", [])
        if block.get("id")
    }


def _fits(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    text: str,
    size: float,
    fontname: str | None = None,
) -> bool:
    fontname = fontname or apply_cjk_font(page)
    try:
        result = page.insert_textbox(
            rect,
            text,
            fontname=fontname,
            fontsize=size,
            align=pymupdf.TEXT_ALIGN_LEFT,
        )
        return result >= 0
    except (RuntimeError, ValueError):
        return False


def _insert_fitted_text(
    page: pymupdf.Page,
    target_rect: pymupdf.Rect,
    text: str,
    max_size: float,
    min_size: float = MIN_FONT_SIZE,
    erase: pymupdf.Rect | None = None,
) -> tuple[float | None, str | None]:
    fontname = apply_cjk_font(page)

    # Test on a temporary page so a failed insertion never erases source text.
    candidate_sizes = [max_size]
    while candidate_sizes[-1] > min_size:
        candidate_sizes.append(round(max(min_size, candidate_sizes[-1] - 0.5), 1))

    for size in candidate_sizes:
        temp_doc = pymupdf.open()
        temp_page = temp_doc.new_page(width=max(target_rect.width, 1), height=max(target_rect.height, 1))
        temp_rect = pymupdf.Rect(0, 0, target_rect.width, target_rect.height)
        fits = _fits(temp_page, temp_rect, text, size)
        temp_doc.close()
        if not fits:
            continue

        # Erase the original text (and, for scanned pages, the underlying pixels)
        # before writing the translation into the same place.
        page.draw_rect(erase or target_rect, color=None, fill=(1, 1, 1), overlay=True)
        page.insert_textbox(
            target_rect,
            text,
            fontname=fontname,
            fontsize=size,
            align=pymupdf.TEXT_ALIGN_LEFT,
        )
        warning = None
        if size <= 6.0:
            warning = f"字体已缩小到 {size}pt 才放入原位置"
        return size, warning

    return None, "译文过长，无法放入原文本块"


def build_translated_pdf(
    original_pdf: str | Path,
    structured: dict[str, Any],
    approved_translations: dict[str, Any],
    output_pdf: str | Path,
) -> dict[str, Any]:
    source_path = Path(original_pdf).expanduser().resolve()
    output_path = Path(output_pdf).expanduser().resolve()
    if not approved_translations.get("approved"):
        raise ValueError("Translations have not passed the automatic approval gate.")

    document = pymupdf.open(source_path)
    block_map = _block_id_map(structured)
    translations = {item["block_id"]: item for item in approved_translations.get("items", [])}
    warnings: list[dict[str, Any]] = []

    # Scanned pages keep the original English as pixels, so hold a readable font
    # size instead of shrinking until the text only just fits.
    ocr_font_sizes = [
        float(block.get("style", {}).get("max_font_size", 0.0) or 0.0)
        for block in block_map.values()
        if block.get("role") in TRANSLATABLE_ROLES
        and _is_ocr_block(block)
        and block.get("style", {}).get("max_font_size")
    ]
    ocr_min_font = MIN_FONT_SIZE
    if ocr_font_sizes:
        ocr_font_sizes.sort()
        text_min = ocr_font_sizes[len(ocr_font_sizes) // 2]
        ocr_min_font = max(OCR_MIN_FONT_SIZE, round(min(text_min, MAX_FONT_SIZE), 1))

    try:
        for page_number, page in enumerate(document, start=1):
            page_blocks = [
                block
                for block in block_map.values()
                if block.get("page") == page_number
            ]
            ocr_page_blocks = [block for block in page_blocks if _is_ocr_block(block)]
            ocr_targets = _ocr_target_rects(ocr_page_blocks) if ocr_page_blocks else {}
            page_rect = page.rect

            for block in page_blocks:
                item = translations.get(block["id"])
                if not item or block.get("role") not in TRANSLATABLE_ROLES:
                    continue

                text = item.get("translation", "").strip()
                if not text:
                    continue

                rect = pymupdf.Rect(*block["bbox"])
                if rect.width <= 1 or rect.height <= 1:
                    continue

                is_ocr = _is_ocr_block(block)
                target_rect = ocr_targets.get(block["id"], rect) if is_ocr else rect
                target_rect = pymupdf.Rect(target_rect) & page_rect
                if target_rect.is_empty or target_rect.width <= 1 or target_rect.height <= 1:
                    target_rect = rect
                max_size = float(block.get("style", {}).get("max_font_size", 9.0) or 9.0)
                max_size = min(max(max_size, 6.0), MAX_FONT_SIZE)
                min_size = min(ocr_min_font, max_size) if is_ocr else MIN_FONT_SIZE
                erase_rect = pymupdf.Rect(rect) | target_rect if is_ocr else rect
                size, warning = _insert_fitted_text(
                    page,
                    target_rect,
                    text,
                    max_size,
                    min_size=min_size,
                    erase=erase_rect,
                )
                if warning:
                    warnings.append(
                        {
                            "block_id": block["id"],
                            "page": page_number,
                            "role": block.get("role"),
                            "warning": warning,
                            "font_size": size,
                        }
                    )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        document.save(output_path, garbage=4, deflate=True)
    finally:
        document.close()

    return {
        "output_pdf": str(output_path),
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def build_bilingual_pdf(
    original_pdf: str | Path,
    translated_pdf: str | Path,
    output_pdf: str | Path,
) -> str:
    original = pymupdf.open(Path(original_pdf).expanduser().resolve())
    translated = pymupdf.open(Path(translated_pdf).expanduser().resolve())
    output_path = Path(output_pdf).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = pymupdf.open()
    try:
        for index in range(len(original)):
            source_page = original[index]
            translated_page = translated[index]
            width = source_page.rect.width + translated_page.rect.width + 12
            height = max(source_page.rect.height, translated_page.rect.height)
            page = result.new_page(width=width, height=height)
            page.show_pdf_page(pymupdf.Rect(0, 0, source_page.rect.width, source_page.rect.height), original, index)
            page.show_pdf_page(
                pymupdf.Rect(source_page.rect.width + 12, 0, width, translated_page.rect.height),
                translated,
                index,
            )
            page.draw_line(
                pymupdf.Point(source_page.rect.width + 6, 0),
                pymupdf.Point(source_page.rect.width + 6, height),
                color=(0.7, 0.7, 0.7),
                width=0.5,
            )
        result.save(output_path, garbage=4, deflate=True)
    finally:
        result.close()
        original.close()
        translated.close()

    return str(output_path)


def document_uses_ocr(structured: dict[str, Any]) -> bool:
    """True when the document text came from OCR of scanned page images."""
    for page in structured.get("pages", []):
        for block in page.get("blocks", []):
            if _is_ocr_block(block):
                return True
    return False


def _ordered_translations(
    structured: dict[str, Any],
    approved_translations: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return approved translations in page, column, and top-to-bottom order."""
    translations = {item["block_id"]: item for item in approved_translations.get("items", [])}
    items: list[dict[str, Any]] = []
    for page in structured.get("pages", []):
        blocks = [
            block
            for block in page.get("blocks", [])
            if block.get("role") in TRANSLATABLE_ROLES
            and block.get("id") in translations
            and _is_ocr_block(block)
        ]
        for block in sorted(blocks, key=lambda item: (item["bbox"][1], item["bbox"][0])):
            item = translations[block["id"]]
            text = str(item.get("translation", "")).strip()
            if text:
                items.append({"block_id": block["id"], "text": text})
    return items


def build_flowing_translated_pdf(
    structured: dict[str, Any],
    approved_translations: dict[str, Any],
    output_pdf: str | Path,
) -> dict[str, Any]:
    """Lay out a scanned document's translation as clean, readable pages.

    Overlaying Chinese text onto a scan cannot work: the original English stays
    visible as pixels and OCR line boxes are far too tight for CJK text. The
    translation is therefore typeset on fresh pages in reading order.
    """
    output_path = Path(output_pdf).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    items = _ordered_translations(structured, approved_translations)
    if not items:
        raise ValueError("Translations have not passed the automatic approval gate.")

    page_width, page_height = pymupdf.paper_size("a4")
    margin = 56.0
    body_size = 10.5
    first_size = 13.0
    usable_width = page_width - margin * 2

    warnings: list[dict[str, Any]] = []
    document = pymupdf.open()
    try:
        state: dict[str, Any] = {"page": None, "y": margin}

        def rect_for(page: pymupdf.Page, top: float) -> pymupdf.Rect:
            return pymupdf.Rect(margin, top, margin + usable_width, page_height - margin)

        for index, item in enumerate(items):
            size = first_size if index == 0 else body_size
            line_height = size * 1.45
            text = item["text"]

            # Start a page when the current one has no room for another line.
            if state["page"] is None or (page_height - margin - state["y"]) < line_height:
                state["page"] = document.new_page(width=page_width, height=page_height)
                state["y"] = margin
            page = state["page"]
            fontname = apply_cjk_font(page)

            rect = rect_for(page, state["y"])
            # insert_textbox returns the unused height on success and a negative
            # value when the text does not fit into the box at all.
            used = page.insert_textbox(
                rect,
                text,
                fontname=fontname,
                fontsize=size,
                align=pymupdf.TEXT_ALIGN_LEFT,
            )
            if used >= 0:
                consumed = rect.height - used
                state["y"] = rect.y0 + max(consumed, line_height) + line_height * 0.35
                continue

            # Retry on a fresh page: the block may simply not fit in what is left.
            page = document.new_page(width=page_width, height=page_height)
            fontname = apply_cjk_font(page)
            state["page"] = page
            state["y"] = margin
            rect = rect_for(page, margin)
            used = page.insert_textbox(
                rect,
                text,
                fontname=fontname,
                fontsize=size,
                align=pymupdf.TEXT_ALIGN_LEFT,
            )
            if used >= 0:
                consumed = rect.height - used
                state["y"] = rect.y0 + max(consumed, line_height) + line_height * 0.35
                continue

            # Even an empty page cannot hold this block; shrink it rather than lose it.
            warnings.append(
                {"block_id": item["block_id"], "warning": "内容过长，已缩小字号排版"}
            )
            fitted = body_size - 0.5
            while fitted > 5.0:
                used = page.insert_textbox(
                    rect,
                    text,
                    fontname=fontname,
                    fontsize=fitted,
                    align=pymupdf.TEXT_ALIGN_LEFT,
                )
                if used >= 0:
                    break
                fitted = round(fitted - 0.5, 1)
            state["y"] = page_height - margin

        document.save(output_path, garbage=4, deflate=True)
    finally:
        document.close()

    return {
        "output_pdf": str(output_path),
        "warning_count": len(warnings),
        "warnings": warnings,
    }


def build_flowing_bilingual_pdf(
    original_pdf: str | Path,
    translated_pdf: str | Path,
    output_pdf: str | Path,
) -> str:
    """Stack each scanned page above its typeset translation."""
    original = pymupdf.open(Path(original_pdf).expanduser().resolve())
    translated = pymupdf.open(Path(translated_pdf).expanduser().resolve())
    output_path = Path(output_pdf).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    gap = 16.0

    result = pymupdf.open()
    try:
        for index in range(len(original)):
            source_page = original[index]
            width, height = source_page.rect.width, source_page.rect.height
            translation_index = min(index, len(translated) - 1) if len(translated) else -1
            page = result.new_page(width=width, height=height * 2 + gap)
            page.show_pdf_page(pymupdf.Rect(0, 0, width, height), original, index)
            if translation_index >= 0:
                top = height + gap
                page.show_pdf_page(
                    pymupdf.Rect(0, top, width, top + height),
                    translated,
                    translation_index,
                )
                page.draw_line(
                    pymupdf.Point(0, height + gap / 2),
                    pymupdf.Point(width, height + gap / 2),
                    color=(0.7, 0.7, 0.7),
                    width=0.5,
                )
        result.save(output_path, garbage=4, deflate=True)
    finally:
        result.close()
        original.close()
        translated.close()

    return str(output_path)


def load_approved_translations(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
