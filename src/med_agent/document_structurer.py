"""Turn extracted PDF blocks into a structured medical document."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

KNOWN_HEADINGS = {
    "abstract",
    "background",
    "introduction",
    "methods",
    "method",
    "materials and methods",
    "results",
    "discussion",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgment",
    "acknowledgments",
    "acknowledgement",
    "acknowledgements",
}

NUMBERED_HEADING = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)[.)]?\s+(\S.*)$")
# A heading title contains a real word, not digits, units, or stretched OCR noise.
HEADING_WORD = re.compile(r"[A-Za-z]{3,}")
CJK_CHARACTER = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
# "ooo" (misread "000") and "urn" (misread "um") carry no vowel-like letter.
WORD_LIKE = re.compile(r"[a-zA-Z][aeiouy]", re.IGNORECASE)
# OCR stretches a zero into a repeated letter ("500" -> "5 ooo", "1000" -> "1 ooo").
REPEATED_LETTERS = re.compile(r"(?i)\b([a-z])\1{2,}\b")
TABLE_CAPTION = re.compile(r"^table\s+\d+\b", re.IGNORECASE)
FIGURE_CAPTION = re.compile(r"^(figure|fig\.)\s*\d+\b", re.IGNORECASE)
REFERENCE_ENTRY = re.compile(r"^\s*(?:\[\d+\]|\d+[.)])\s+")
AUTHOR_MARKER = re.compile(r"\b(MD|PhD|MSc|MBBS|DO|RN)\b", re.IGNORECASE)


def _normalize_heading_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).strip(" .:")


def _heading_level(text: str) -> int | None:
    cleaned = text.strip()
    match = NUMBERED_HEADING.match(cleaned)
    if match:
        # A real numbered heading has a title after the number ("3. Results").
        # Chart axes and figure labels OCR as bare numbers ("2 500 2.0") or as
        # stretched zeros ("6 ooo"); treating those as headings would send
        # figure text to the translator and pollute the section list.
        title = match.group(2)
        has_title_word = bool(HEADING_WORD.search(title) and WORD_LIKE.search(title))
        has_cased_letter = any(character.islower() or character.isupper() for character in title)
        if (
            (has_title_word or CJK_CHARACTER.search(title))
            and has_cased_letter
            and not REPEATED_LETTERS.search(title)
        ):
            return match.group(1).count(".") + 1

    if _normalize_heading_key(cleaned) in KNOWN_HEADINGS:
        return 1

    return None


def _looks_like_heading(block: dict[str, Any]) -> bool:
    text = block.get("text", "").strip()
    if not text or block.get("type") != "text":
        return False

    if _heading_level(text) is not None:
        return True

    style = block.get("style", {})
    max_size = float(style.get("max_font_size", 0.0))
    is_bold = bool(style.get("is_bold", False))
    words = text.split()
    return is_bold and max_size >= 11 and len(words) <= 12 and len(text.splitlines()) <= 2


def _looks_like_authors(block: dict[str, Any]) -> bool:
    text = block.get("text", "").strip()
    if not text or len(text) > 300 or len(text.splitlines()) > 3:
        return False

    if AUTHOR_MARKER.search(text):
        return True

    if ";" in text or " and " in text.lower():
        return True

    return False


def _looks_like_table_body(block: dict[str, Any]) -> bool:
    text = block.get("text", "")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False

    spaced_lines = sum(1 for line in lines if len(re.findall(r"\s{2,}", line)) >= 1)
    numeric_lines = sum(1 for line in lines if re.search(r"\d", line))
    return spaced_lines >= 2 and numeric_lines >= 2


def _section_id(index: int) -> str:
    return f"sec-{index:03d}"


def structure_document(extracted: dict[str, Any]) -> dict[str, Any]:
    """Classify blocks and attach section information while preserving coordinates."""
    pages: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    current_section_id: str | None = None
    in_references = False
    title: str | None = None
    authors: list[str] = []
    title_seen = False
    section_number = 0
    text_block_number = 0

    for page in extracted.get("pages", []):
        structured_blocks: list[dict[str, Any]] = []

        for block in page.get("blocks", []):
            text_block_number += 1
            structured = dict(block)
            structured["id"] = f"blk-{text_block_number:05d}"
            structured["page"] = page["page"]

            text = structured.get("text", "").strip()
            role = "image" if structured.get("type") == "image" else "paragraph"
            heading_level: int | None = None

            if structured.get("type") == "text":
                if not title_seen and page["page"] == 1 and text:
                    title = text.replace("\n", " ")
                    role = "title"
                    title_seen = True
                elif role == "paragraph" and title_seen and not sections and not authors and _looks_like_authors(structured):
                    role = "authors"
                    authors = [part.strip() for part in re.split(r";|,", text) if part.strip()]
                else:
                    if in_references:
                        role = "reference"
                    else:
                        heading_level = _heading_level(text)
                        if heading_level is not None:
                            role = "heading"
                        elif TABLE_CAPTION.match(text):
                            role = "table_caption"
                        elif FIGURE_CAPTION.match(text):
                            role = "figure_caption"
                        elif len(structured_blocks) >= 1 and structured_blocks[-1].get("role") == "table_caption":
                            role = "table"
                        elif _looks_like_table_body(structured):
                            role = "table"

                if role == "heading":
                    section_number += 1
                    current_section_id = _section_id(section_number)
                    section_name = text.replace("\n", " ").strip()
                    in_references = _normalize_heading_key(section_name) == "references"
                    sections.append(
                        {
                            "id": current_section_id,
                            "heading": section_name,
                            "level": heading_level or 1,
                            "page_start": page["page"],
                            "block_id": structured["id"],
                        }
                    )

            structured["role"] = role
            structured["section_id"] = current_section_id
            if heading_level is not None:
                structured["heading_level"] = heading_level
            structured_blocks.append(structured)

        pages.append({**{key: value for key, value in page.items() if key != "blocks"}, "blocks": structured_blocks})

    inferred_metadata = {
        "title": title,
        "authors": authors,
        "section_count": len(sections),
    }

    return {
        "source": extracted.get("source"),
        "metadata": {
            "pdf": extracted.get("metadata", {}),
            "inferred": inferred_metadata,
        },
        "sections": sections,
        "pages": pages,
    }


def load_and_structure(extracted_path: str | Path) -> dict[str, Any]:
    """Read an extracted JSON file and return its structured form."""
    import json

    path = Path(extracted_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file:
        extracted = json.load(file)
    return structure_document(extracted)
