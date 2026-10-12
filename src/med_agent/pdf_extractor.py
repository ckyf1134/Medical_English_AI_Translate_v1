"""PDF reading and layout extraction."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import pymupdf

from med_agent.ocr_engine import DEFAULT_DPI, DEFAULT_LANGUAGES, OcrError, ocr_pdf_pages

# A page needs at least this many real characters to count as having a text layer.
MIN_TEXT_CHARACTERS_PER_PAGE = 20
TOTAL_TEXT_CHARACTERS = 40
CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "ocr.json"

NO_TEXT_LAYER_HINT = (
    "这个 PDF 没有文字层（很可能是扫描件或整页图片导出）。"
    "文本提取得到 0 个字符，直接翻译只会原样输出未翻译的文件。"
    "请开启 OCR（默认已开启），或改用带文字层的 PDF。"
)


class NoTextLayerError(RuntimeError):
    """Raised when a PDF has no extractable text and OCR is unavailable."""

    def __init__(self, message: str, *, page_count: int = 0) -> None:
        super().__init__(message)
        self.page_count = page_count


def load_ocr_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read OCR settings, falling back to safe defaults when absent."""
    config_path = Path(path) if path is not None else CONFIG_PATH
    defaults: dict[str, Any] = {
        "enabled": True,
        "engine": "windows",
        "dpi": DEFAULT_DPI,
        "languages": list(DEFAULT_LANGUAGES),
        "timeout_seconds": 900,
        "max_pages": 300,
    }
    try:
        raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults

    languages = raw.get("languages")
    if not isinstance(languages, list) or not languages:
        languages = defaults["languages"]
    def _positive_int(key: str) -> int:
        try:
            value = int(raw.get(key, defaults[key]))
        except (TypeError, ValueError):
            return int(defaults[key])
        return value if value > 0 else int(defaults[key])

    return {
        "enabled": bool(raw.get("enabled", defaults["enabled"])),
        "engine": str(raw.get("engine") or defaults["engine"]),
        "dpi": _positive_int("dpi"),
        "languages": [str(tag) for tag in languages],
        "timeout_seconds": _positive_int("timeout_seconds"),
        "max_pages": _positive_int("max_pages"),
    }


def _has_meaningful_text(text: str) -> bool:
    return sum(1 for character in text if character.isalnum()) >= MIN_TEXT_CHARACTERS_PER_PAGE


def inspect_text_layer(pdf_path: str | Path) -> dict[str, Any]:
    """Report how much real text a PDF contains, which reveals scanned pages."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF file not found: {path}")

    document = pymupdf.open(path)
    try:
        page_count = document.page_count
        pages_without_text = 0
        total_characters = 0
        total_images = 0
        for page in document:
            text = page.get_text().strip()
            characters = sum(1 for character in text if character.isalnum())
            total_characters += characters
            total_images += len(page.get_images(full=True))
            if not _has_meaningful_text(text):
                pages_without_text += 1
    finally:
        document.close()

    return {
        "page_count": page_count,
        "pages_without_text": pages_without_text,
        "total_characters": total_characters,
        "total_images": total_images,
        "has_text_layer": total_characters >= TOTAL_TEXT_CHARACTERS and pages_without_text < max(1, page_count),
    }


def _round_bbox(bbox: tuple[float, float, float, float] | list[float]) -> list[float]:
    return [round(float(value), 2) for value in bbox]


def _rounded_sizes(sizes: list[float]) -> list[float]:
    return [round(float(size), 1) for size in sizes]


def _extract_text_block(raw_block: dict[str, Any]) -> dict[str, Any] | None:
    lines: list[str] = []
    font_sizes: list[float] = []
    fonts: list[str] = []

    for line in raw_block.get("lines", []):
        span_texts: list[str] = []
        for span in line.get("spans", []):
            text = span.get("text", "")
            if text:
                span_texts.append(text)
                font_sizes.append(float(span.get("size", 0.0)))
                font = str(span.get("font", ""))
                if font:
                    fonts.append(font)

        line_text = "".join(span_texts).strip()
        if line_text:
            lines.append(line_text)

    text = "\n".join(lines).strip()
    if not text:
        return None

    size_counts = Counter(_rounded_sizes(font_sizes))
    dominant_size = size_counts.most_common(1)[0][0] if size_counts else 0.0
    max_size = round(max(font_sizes), 2) if font_sizes else 0.0
    is_bold = any("bold" in font.lower() or "black" in font.lower() for font in fonts)

    return {
        "type": "text",
        "bbox": _round_bbox(raw_block.get("bbox", (0, 0, 0, 0))),
        "text": text,
        "style": {
            "dominant_font_size": dominant_size,
            "max_font_size": max_size,
            "fonts": sorted(set(fonts)),
            "is_bold": is_bold,
        },
    }


def _extract_image_block(raw_block: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "image",
        "bbox": _round_bbox(raw_block.get("bbox", (0, 0, 0, 0))),
        "text": "",
        "image": {
            "width": raw_block.get("width"),
            "height": raw_block.get("height"),
            "extension": raw_block.get("ext"),
        },
    }


def extract_pdf(
    pdf_path: str | Path,
    *,
    ocr: bool | None = None,
    ocr_dpi: int | None = None,
    ocr_languages: list[str] | None = None,
    require_text: bool = True,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Extract text, basic style, images, and coordinates from a PDF.

    A PDF whose pages are scanned images yields no text at all. Instead of
    silently returning an empty document (which would later be republished as an
    untranslated copy), the pages are sent to OCR. When OCR is switched off, a
    :class:`NoTextLayerError` explains the situation instead of failing quietly.
    """
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"PDF file not found: {path}")

    config = load_ocr_config()
    ocr_enabled = bool(config["enabled"]) if ocr is None else bool(ocr)
    dpi = int(ocr_dpi or config["dpi"])
    languages = ocr_languages or list(config["languages"])

    document = pymupdf.open(path)
    pages: list[dict[str, Any]] = []
    image_count = 0

    try:
        if document.needs_pass:
            raise NoTextLayerError(
                f"这个 PDF 已加密，需要密码才能解析：{path.name}", page_count=document.page_count
            )

        for page_number, page in enumerate(document, start=1):
            page_dict = page.get_text("dict", sort=True)
            blocks: list[dict[str, Any]] = []

            for raw_block in page_dict.get("blocks", []):
                block_type = raw_block.get("type")
                if block_type == 0:
                    block = _extract_text_block(raw_block)
                elif block_type == 1:
                    block = _extract_image_block(raw_block)
                    image_count += 1
                else:
                    block = None

                if block is not None:
                    blocks.append(block)

            pages.append(
                {
                    "page": page_number,
                    "width": round(float(page.rect.width), 2),
                    "height": round(float(page.rect.height), 2),
                    "rotation": int(page.rotation),
                    "blocks": blocks,
                }
            )

        metadata = {
            key: value
            for key, value in document.metadata.items()
            if value not in (None, "")
        }
    finally:
        document.close()

    character_count = sum(
        1
        for page in pages
        for block in page["blocks"]
        for character in str(block.get("text", ""))
        if character.isalnum()
    )
    has_text_layer = character_count >= TOTAL_TEXT_CHARACTERS

    result: dict[str, Any] = {
        "source": str(path),
        "metadata": metadata,
        "pages": pages,
        "extraction": {
            "page_count": len(pages),
            "text_characters": character_count,
            "image_blocks": image_count,
            "has_text_layer": has_text_layer,
            "ocr": {"applied": False, "engine": None, "language": None, "blocks": 0},
        },
    }

    if has_text_layer:
        return result

    if not ocr_enabled:
        raise NoTextLayerError(
            f"{NO_TEXT_LAYER_HINT}（{path.name}：{len(pages)} 页，{image_count} 个图片块）",
            page_count=len(pages),
        )

    if progress is not None:
        progress("未检测到文字层，改用 OCR 识别扫描页面")

    try:
        ocr_result = ocr_pdf_pages(
            path,
            dpi=dpi,
            languages=languages,
            timeout=int(config["timeout_seconds"]),
            max_pages=int(config["max_pages"]),
            progress=progress,
        )
    except OcrError as error:
        raise NoTextLayerError(f"{NO_TEXT_LAYER_HINT} OCR 也失败了：{error}", page_count=len(pages)) from error

    ocr_by_page = {page["page"]: page["blocks"] for page in ocr_result["pages"]}
    merged_pages: list[dict[str, Any]] = []
    for page in pages:
        ocr_blocks = ocr_by_page.get(page["page"], [])
        merged_blocks = [
            block for block in page["blocks"] if block.get("type") != "image"
        ] + ocr_blocks
        merged_blocks.sort(key=lambda block: (block["bbox"][1], block["bbox"][0]))
        merged_pages.append({**page, "blocks": merged_blocks})

    result["pages"] = merged_pages
    result["extraction"]["text_characters"] = ocr_result["block_count"]
    result["extraction"]["ocr"] = {
        "applied": True,
        "engine": ocr_result["engine"],
        "language": ocr_result["language"],
        "blocks": ocr_result["block_count"],
        "dpi": dpi,
    }
    result["metadata"] = {**metadata, "ocr_engine": str(ocr_result["engine"])}

    if require_text and ocr_result["block_count"] == 0:
        raise NoTextLayerError(NO_TEXT_LAYER_HINT, page_count=len(pages))

    return result
