"""Offline OCR for PDFs that have no text layer.

Scanned documents store their pages as images, so ``pymupdf`` returns no text at
all. This module renders those pages and sends them to the OCR engine that ships
with Windows (``Windows.Media.Ocr`` through ``tools/ocr_windows.ps1``), then turns
word-level results back into positioned text blocks that the rest of the agent
already understands.

The OCR output is normalised a little: OCR commonly renders a decimal point as an
en dash (``1.8`` -> ``1-8``) and pulls a space in front of a word. Those artefacts
would make the quality checker reject perfectly good translations, so they are
repaired here.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable

import pymupdf

ENGINE_NAME = "windows-media-ocr"
DEFAULT_LANGUAGES = ("en-GB", "en-US", "zh-Hans-CN")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OCR_SCRIPT = PROJECT_ROOT / "tools" / "ocr_windows.ps1"

# A line is a run of words whose vertical extents overlap by at least this ratio.
LINE_Y_OVERLAP = 0.35
# A vertical whitespace band crossed by at most this share of words is a column
# gutter. Sample the page width to find these bands.
COLUMN_GUTTER_SAMPLE = 4
# A ligature fragment longer than this is not treated as a chopped word.
SPLIT_WORD_MAX_FRAGMENT = 10
# A vertical gap larger than this share of the page width starts a new band.
BAND_GAP_RATIO = 0.028
# A vertical whitespace band counts as a column gutter when fewer than this
# share of words cross it compared with its neighbourhood.
COLUMN_GUTTER_CROSS_RATIO = 0.02
COLUMN_GUTTER_MAX_WIDTH_RATIO = 0.25
COLUMN_GUTTER_MIN_WIDTH = 12.0
# Below this many words a page is too sparse for the width limit to mean anything.
COLUMN_GUTTER_SPARSE_WORDS = 40
# Pages are rendered at this resolution when no DPI is configured.
DEFAULT_DPI = 200
# A gap this much larger than the usual line pitch starts a new paragraph.
PARAGRAPH_GAP_RATIO = 1.45
# Share of the tightest line gaps used to estimate the baseline line pitch.
PARAGRAPH_PITCH_PERCENTILE = 0.6
# The baseline pitch is at least this multiple of the glyph height, so a column
# with only one (paragraph) gap cannot treat that gap as ordinary line spacing.
PARAGRAPH_PITCH_MIN_FACTOR = 1.3
# Fewer gaps than this are not a distribution, so the glyph height is used.
PARAGRAPH_PITCH_MIN_SAMPLES = 3
# A line stopping this far short of the column edge ends its paragraph.
PARAGRAPH_SHORT_LINE_RATIO = 0.12

_NUMBER_WITH_DASH = re.compile(r"(?<=\d)-(?=\d)")
_FULLWIDTH_PUNCTUATION = {
    "：": ":",
    "，": ",",
    "；": ";",
    "（": "(",
    "）": ")",
    "％": "%",
    "／": "/",
    "－": "-",
}


class OcrError(RuntimeError):
    """Raised when OCR cannot be performed or returns an unusable result."""


def _normalize_word(text: str) -> str:
    """Repair the OCR artefacts that break number and unit checks."""
    cleaned = text.strip()
    if not cleaned:
        return ""
    for source, target in _FULLWIDTH_PUNCTUATION.items():
        cleaned = cleaned.replace(source, target)
    # "1-8" is a decimal point misread by the OCR engine; inside a number it is never a dash.
    cleaned = _NUMBER_WITH_DASH.sub(".", cleaned)
    return cleaned


def _powershell_executable() -> str:
    for name in ("powershell.exe", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    system_root = Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe")
    if system_root.is_file():
        return str(system_root)
    raise OcrError(
        "找不到 PowerShell，无法调用 Windows 自带 OCR。"
        "请在 Windows 上运行，或用 --no-ocr 关闭 OCR。"
    )


def _run_ocr_script(manifest: dict[str, Any], timeout: int) -> dict[str, Any]:
    script = OCR_SCRIPT
    if not script.is_file():
        raise OcrError(f"OCR 脚本缺失：{script}")

    executable = _powershell_executable()
    with tempfile.TemporaryDirectory(prefix="med-agent-ocr-") as work_dir:
        work = Path(work_dir)
        manifest_path = work / "manifest.json"
        output_path = work / "ocr_result.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        command = [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Manifest",
            str(manifest_path),
            "-Output",
            str(output_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise OcrError(f"OCR 超时（超过 {timeout} 秒）") from error
        except OSError as error:
            raise OcrError(f"无法启动 OCR 进程：{error}") from error

        if not output_path.is_file():
            detail = (completed.stderr or completed.stdout or "").strip()[-800:]
            raise OcrError(f"OCR 未返回结果（退出码 {completed.returncode}）。{detail}")

        try:
            payload = json.loads(output_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise OcrError(f"OCR 结果不是有效 JSON：{error}") from error

    if not isinstance(payload, dict):
        raise OcrError("OCR 结果格式不正确")
    if payload.get("error"):
        raise OcrError(f"Windows OCR 失败：{payload['error']}")
    if not payload.get("pages"):
        raise OcrError("Windows OCR 没有识别出任何文字")
    return payload


def _detect_gutters(words: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """Find the vertical whitespace bands that separate text columns.

    This must run on the raw word coordinates, before lines are assembled. Once
    OCR's "left half of column A + right half of column B" lines are merged, their
    bounding boxes span the gutter and the whitespace statistic is destroyed.

    Almost no word crosses a real gutter; a wide title crosses it as a handful of
    words, which the small allowance covers.
    """
    if not words:
        return []

    left = min(word["x0"] for word in words)
    right = max(word["x1"] for word in words)
    if right - left < COLUMN_GUTTER_MIN_WIDTH * 2:
        return []

    sample_points: list[float] = []
    position = left
    while position <= right:
        sample_points.append(position)
        position += COLUMN_GUTTER_SAMPLE
    if len(sample_points) < 3:
        return []

    crossings = [0] * len(sample_points)
    for word in words:
        for index, point in enumerate(sample_points):
            if word["x0"] <= point <= word["x1"]:
                crossings[index] += 1

    allowed = max(1.0, len(words) * COLUMN_GUTTER_CROSS_RATIO)
    is_valley = [value <= allowed for value in crossings]

    accepted: list[tuple[int, int]] = []
    start_index: int | None = None
    for index, valley in enumerate(is_valley):
        if valley and start_index is None:
            start_index = index
        elif not valley and start_index is not None:
            accepted.append((start_index, index - 1))
            start_index = None
    if start_index is not None:
        accepted.append((start_index, len(sample_points) - 1))

    # A gutter is a narrow band, except on a page with very few words where the
    # whitespace legitimately dominates.
    widest = (right - left) * COLUMN_GUTTER_MAX_WIDTH_RATIO
    bands: list[tuple[float, float]] = []
    for first, last in accepted:
        start, end = sample_points[first], sample_points[last]
        if end - start < COLUMN_GUTTER_MIN_WIDTH:
            continue
        if len(words) >= COLUMN_GUTTER_SPARSE_WORDS and end - start > widest:
            continue
        # A gutter must not sit on the page margin.
        if start <= left + 1.0 or start >= right - 1.0:
            continue
        bands.append((start, end))

    # Join bands that are only a sample or two apart.
    merged: list[tuple[float, float]] = []
    for band in bands:
        if merged and band[0] - merged[-1][1] <= COLUMN_GUTTER_SAMPLE * 3:
            merged[-1] = (merged[-1][0], band[1])
        else:
            merged.append(band)
    return merged


def _column_index(piece: list[dict[str, Any]], gutters: list[tuple[float, float]]) -> float:
    """Sort key that keeps columns in reading order (left to right)."""
    center = (min(word["x0"] for word in piece) + max(word["x1"] for word in piece)) / 2
    for index, (start, end) in enumerate(gutters):
        if center <= start:
            return float(index)
    return float(len(gutters))


def _group_lines(
    words: list[dict[str, Any]],
    gutters: list[tuple[float, float]] | None = None,
) -> list[list[dict[str, Any]]]:
    """Collect words into visual lines by their vertical overlap.

    Gutters are only used to stop a word from being pulled across a column that
    was already found; the geometry itself is enough to separate the columns,
    because the two columns of one baseline are never on the same x run.
    """
    active = gutters or []
    lines: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (item["y0"], item["x0"])):
        height = max(1.0, word["y1"] - word["y0"])
        best_line: dict[str, Any] | None = None
        best_overlap = 0.0
        for line in lines:
            if any(line["x1"] <= start and word["x0"] >= end for start, end in active):
                continue
            overlap = min(line["y1"], word["y1"]) - max(line["y0"], word["y0"])
            if overlap <= 0:
                continue
            ratio = overlap / min(height, max(1.0, line["y1"] - line["y0"]))
            if ratio > LINE_Y_OVERLAP and ratio > best_overlap:
                best_overlap = ratio
                best_line = line
        if best_line is None:
            lines.append(
                {
                    "y0": word["y0"],
                    "y1": word["y1"],
                    "x0": word["x0"],
                    "x1": word["x1"],
                    "words": [word],
                }
            )
        else:
            best_line["words"].append(word)
            best_line["y0"] = min(best_line["y0"], word["y0"])
            best_line["y1"] = max(best_line["y1"], word["y1"])
            best_line["x0"] = min(best_line["x0"], word["x0"])
            best_line["x1"] = max(best_line["x1"], word["x1"])

    for line in lines:
        line["words"].sort(key=lambda item: item["x0"])
    lines.sort(key=lambda line: (line["y0"], line["x0"]))
    return [line["words"] for line in lines]


def _detect_bands(lines: list[list[dict[str, Any]]]) -> list[list[list[dict[str, Any]]]]:
    """Split lines into vertical bands separated by generous vertical space.

    Columns are only looked for inside a band, so a full-width banner is never
    cut by a gutter that only exists further down the page.
    """
    if not lines:
        return []

    ordered = sorted(
        lines,
        key=lambda words: (min(word["y0"] for word in words), min(word["x0"] for word in words)),
    )
    page_width = max(
        1.0,
        max(max(word["x1"] for word in line) for line in ordered)
        - min(min(word["x0"] for word in line) for line in ordered),
    )

    bands: list[list[list[dict[str, Any]]]] = []
    current: list[list[dict[str, Any]]] = []
    previous_bottom = 0.0
    for line in ordered:
        top = min(word["y0"] for word in line)
        bottom = max(word["y1"] for word in line)
        gap = max(0.0, top - previous_bottom)
        if current and gap > page_width * BAND_GAP_RATIO:
            bands.append(current)
            current = []
        current.append(line)
        previous_bottom = max(previous_bottom, bottom) if current else bottom
    if current:
        bands.append(current)
    return bands


def _group_columns(
    lines: list[list[dict[str, Any]]],
    gutters: list[tuple[float, float]],
) -> list[list[list[dict[str, Any]]]]:
    """Group lines into columns so multi-column papers keep a sane reading order.

    A line that straddles a gutter is cut into its column parts first, then every
    line is assigned whole to the column holding its centre.
    """
    if not lines:
        return []

    columns: list[list[list[dict[str, Any]]]] = []
    for band in _detect_bands(lines):
        if gutters:
            buckets: dict[float, list[list[dict[str, Any]]]] = {}
            for line in band:
                buckets.setdefault(_column_index(line, gutters), []).append(line)
            for key in sorted(buckets):
                column = buckets[key]
                column.sort(
                    key=lambda words: (
                        min(word["y0"] for word in words),
                        min(word["x0"] for word in words),
                    )
                )
                columns.append(column)
        else:
            band.sort(
                key=lambda words: (
                    min(word["y0"] for word in words),
                    min(word["x0"] for word in words),
                )
            )
            columns.append(band)
    return columns


def _line_text(words: list[dict[str, Any]]) -> str:
    parts = [word["text"] for word in words if word["text"]]
    if not parts:
        return ""
    text = " ".join(parts)
    # OCR drops spaces before punctuation and adds them after opening marks.
    text = re.sub(r"\s+([,.;:!?%)\]}])", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _median(values: Iterable[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def _continues_word(joined: str, text: str) -> bool:
    """True when a line break split a single word in two.

    Only two shapes are repaired, both unambiguous:

    * a trailing hyphen, which always means the word continues;
    * a ligature such as "eﬃ" followed by the rest of the word ("cacy").

    Ordinary ASCII words are deliberately never welded together: "host" + next
    line would become "hosmicrobial", which is worse than leaving the space.
    """
    if not re.match(r"^[^\W\d_]", text, re.UNICODE):
        return False
    if re.search(r"[^\W\d_]-$", joined, re.UNICODE):
        return True
    fragment = re.search(r"[^\W\d_]+$", joined, re.UNICODE)
    if fragment is None:
        return False
    piece = fragment.group(0)
    # A non-ASCII letter means a typographic ligature (ﬃ, ﬁ, ﬂ).
    if piece.isascii() or not piece.islower():
        return False
    return len(piece) <= SPLIT_WORD_MAX_FRAGMENT


def _join_lines(lines: list[list[dict[str, Any]]]) -> str:
    """Join the lines of one paragraph, repairing OCR line-break damage.

    Journals hyphenate at line ends and OCR reports the soft hyphen as a hyphen
    plus a space. It also drops the hyphen entirely, leaving a word chopped into
    "eﬃ" + "cacy", so both shapes have to be closed up or the model is handed a
    broken word and mistranslates it.
    """
    parts = [_line_text(line) for line in lines]
    parts = [text for text in parts if text]
    if not parts:
        return ""
    joined = parts[0]
    for text in parts[1:]:
        if _continues_word(joined, text):
            # Drop the hyphen that marked the break; a ligature stays intact.
            joined = joined[:-1] + text if joined.endswith("-") else joined + text
        else:
            joined = f"{joined} {text}"
    # Space between Chinese and Latin text, but never inside an existing number.
    joined = re.sub(r"([\u4e00-\u9fff])(?=[A-Za-z0-9])", r"\1 ", joined)
    joined = re.sub(r"(?<=[A-Za-z0-9])(?=[\u4e00-\u9fff])", " ", joined)
    return re.sub(r"\s{2,}", " ", joined).strip()


def _group_paragraphs(
    lines: list[list[dict[str, Any]]],
) -> list[list[list[dict[str, Any]]]]:
    """Merge wrapped OCR lines into paragraphs.

    A paragraph continues while the next line sits at the normal line pitch and
    starts at the column's left edge; it ends at a larger gap, an indented first
    line, or a line that stops well short of the column's right edge.
    """
    if not lines:
        return []

    # A line stopping well short of the column edge ends its paragraph.
    right_edge = _percentile([max(word["x1"] for word in line) for line in lines], 0.9)
    gaps: list[float] = []
    breaks: list[bool] = [False] * len(lines)
    for index, line in enumerate(lines):
        if index == 0:
            continue
        previous = lines[index - 1]
        gaps.append(max(0.0, min(word["y0"] for word in line) - max(word["y1"] for word in previous)))
        previous_left = min(word["x0"] for word in previous)
        if min(word["x0"] for word in line) > previous_left + 2.0:
            breaks[index] = True
        elif max(word["x1"] for word in previous) < right_edge - (
            right_edge - min(word["x0"] for word in line)
        ) * PARAGRAPH_SHORT_LINE_RATIO:
            breaks[index] = True

    # The baseline line pitch is the typical gap between wrapped lines. Only the
    # tighter part of the gap distribution is used, because the paragraph gaps
    # themselves would otherwise drag the baseline up and hide the very breaks
    # being looked for. With only a couple of lines there is no distribution to
    # learn from, so the glyph height is used instead.
    line_heights = [max(word["y1"] for word in line) - min(word["y0"] for word in line) for line in lines]
    glyph_height = max(1.0, _median(line_heights))
    pitch = glyph_height * PARAGRAPH_PITCH_MIN_FACTOR
    if len(gaps) >= PARAGRAPH_PITCH_MIN_SAMPLES:
        ordered = sorted(gaps)
        tight = ordered[: max(1, int(len(ordered) * PARAGRAPH_PITCH_PERCENTILE))]
        pitch = max(pitch, _median(tight))

    paragraphs: list[list[list[dict[str, Any]]]] = []
    current: list[list[dict[str, Any]]] = []
    for index, line in enumerate(lines):
        if current:
            previous = lines[index - 1]
            gap = max(0.0, min(word["y0"] for word in line) - max(word["y1"] for word in previous))
            if breaks[index] or gap > pitch * PARAGRAPH_GAP_RATIO:
                paragraphs.append(current)
                current = []
        current.append(line)
    if current:
        paragraphs.append(current)
    return paragraphs


def _to_text_block(
    words: list[dict[str, Any]],
    scale: float,
    page: int,
    *,
    text: str | None = None,
) -> dict[str, Any] | None:
    body = text if text is not None else _line_text(words)
    if not body:
        return None

    x0 = min(word["x0"] for word in words) * scale
    y0 = min(word["y0"] for word in words) * scale
    x1 = max(word["x1"] for word in words) * scale
    y1 = max(word["y1"] for word in words) * scale
    if x1 - x0 <= 0.5 or y1 - y0 <= 0.5:
        return None

    # Tesseract-free OCR gives no font size; approximate it from the glyph height.
    glyph_height = _median([(word["y1"] - word["y0"]) * scale for word in words])
    font_size = round(max(4.0, min(28.0, glyph_height * 1.05)), 2)

    return {
        "type": "text",
        "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
        "text": body,
        "style": {
            "dominant_font_size": font_size,
            "max_font_size": font_size,
            "fonts": [ENGINE_NAME],
            "is_bold": False,
        },
        "page": page,
        "source": ENGINE_NAME,
    }


def _build_blocks(page_payload: dict[str, Any], page_number: int, dpi: int) -> list[dict[str, Any]]:
    raw_words = page_payload.get("words") or []
    words: list[dict[str, Any]] = []
    for raw in raw_words:
        text = _normalize_word(str(raw.get("text", "")))
        if not text:
            continue
        try:
            x0, y0 = float(raw["x0"]), float(raw["y0"])
            x1, y1 = float(raw["x1"]), float(raw["y1"])
        except (KeyError, TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        words.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1})

    if not words:
        return []

    # The engine may downscale a very large page; rescale to PDF points from the
    # geometry it reports so the blocks still line up with the rendered page.
    reported_width = float(page_payload.get("width") or 0.0)
    rendered_width = max(word["x1"] for word in words)
    scale = 72.0 / dpi
    if reported_width > 0 and rendered_width > 0:
        ratio = reported_width / rendered_width
        if ratio > 1.01:
            scale = 72.0 / dpi / ratio

    blocks: list[dict[str, Any]] = []
    gutters = _detect_gutters(words)
    for column in _group_columns(_group_lines(words, gutters), gutters):
        for paragraph in _group_paragraphs(column):
            paragraph_words = [word for line in paragraph for word in line]
            block = _to_text_block(
                paragraph_words,
                scale,
                page_number,
                text=_join_lines(paragraph),
            )
            if block is not None:
                blocks.append(block)
    return blocks


def ocr_pdf_pages(
    pdf_path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    languages: list[str] | tuple[str, ...] | None = None,
    timeout: int = 900,
    max_pages: int = 0,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """OCR every page of a PDF and return positioned text blocks per page."""
    path = Path(pdf_path).expanduser().resolve()
    if not path.is_file():
        raise OcrError(f"PDF 文件不存在：{path}")

    language_list = [tag for tag in (languages or DEFAULT_LANGUAGES) if tag]
    try:
        document = pymupdf.open(path)
    except Exception as error:  # pymupdf raises its own exception types
        raise OcrError(f"无法打开 PDF：{error}") from error

    with tempfile.TemporaryDirectory(prefix="med-agent-ocr-pages-") as render_dir:
        render_root = Path(render_dir)
        images: list[dict[str, str]] = []
        try:
            page_count = document.page_count
            if max_pages and page_count > max_pages:
                document.close()
                raise OcrError(
                    f"PDF 共 {page_count} 页，超过 OCR 上限 {max_pages} 页。"
                    "请在 configs/ocr.json 中调大 max_pages。"
                )
            for index in range(page_count):
                page = document[index]
                image_path = render_root / f"page-{index + 1:04d}.png"
                page.get_pixmap(dpi=dpi).save(image_path)
                images.append({"id": str(index + 1), "path": str(image_path)})
                if progress is not None:
                    progress(f"渲染第 {index + 1}/{page_count} 页")
        finally:
            document.close()

        if not images:
            raise OcrError("PDF 没有可处理的页面")

        if progress is not None:
            progress(f"OCR 识别 {len(images)} 页（{ENGINE_NAME}）")
        payload = _run_ocr_script(
            {"languages": language_list, "images": images},
            timeout=timeout,
        )

    pages_payload = payload.get("pages") or {}
    pages: list[dict[str, Any]] = []
    for key, page_payload in pages_payload.items():
        try:
            page_number = int(key)
        except (TypeError, ValueError):
            continue
        pages.append(
            {
                "page": page_number,
                "blocks": _build_blocks(page_payload, page_number, dpi),
            }
        )
    pages.sort(key=lambda item: item["page"])

    block_count = sum(len(page["blocks"]) for page in pages)
    if block_count == 0:
        raise OcrError("OCR 未识别出可用文字，无法继续翻译")

    return {
        "engine": payload.get("engine") or ENGINE_NAME,
        "language": payload.get("language"),
        "pages": pages,
        "block_count": block_count,
    }
