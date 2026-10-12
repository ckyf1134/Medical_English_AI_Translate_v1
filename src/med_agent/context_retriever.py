"""Build context bundles for terminology-constrained translation."""

from __future__ import annotations

import re
from typing import Any

TEXT_WORD_PATTERN = re.compile(r"[A-Za-z]{2,}")
CAPTION_PATTERN = re.compile(r"^(table|figure|fig\.?|scheme|chart)\s*\d*", re.IGNORECASE)
# Short labels without a real word are figure axes, units, and OCR noise.
SHORT_LABEL_LIMIT = 30
# Section names are translated even though they are short and word-free-looking.
KNOWN_HEADING_PATTERN = re.compile(
    r"^(abstract|background|introduction|methods?|materials and methods|results|"
    r"discussion|conclusions?|references|acknowledg(?:e)?ments?)$"
)
CJK_TEXT = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
# Links, DOIs and publisher banners must stay byte-identical, never translated.
URL_PATTERN = re.compile(r"https?://|www\.|doi\.org|\bdoi:|\bdoi\s+10\.", re.IGNORECASE)
# Journal citations: "2018;45(Suppl 20):S149-S161" or "Nat Rev Immunol. 2015;15(1):30-44".
# These shapes are a far more reliable signal than raw number density, which
# wrongly condemned ordinary prose such as "AUC 0.82 (95% CI 0.76-0.88)".
REFERENCE_PATTERNS = (
    re.compile(r"\b(?:19|20)\d{2}\s*;\s*\d{1,4}\s*[:(]"),
    re.compile(r"\b\d{1,4}\s*\(\s*(?:Suppl\s*)?\d{1,3}\s*\)\s*[:.]?\s*[A-Za-z]?\d"),
)

TRANSLATABLE_ROLES = {
    "title",
    "heading",
    "paragraph",
    "table_caption",
    "figure_caption",
    "table",
}


def _all_blocks(structured: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for page in structured.get("pages", [])
        for block in page.get("blocks", [])
        if block.get("type") == "text"
    ]


def _contains_term(text: str, term: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _is_link_or_reference(text: str) -> bool:
    """True for URLs, DOIs, citations and other text that must not be translated.

    A DOI or link has to survive unchanged, but a translation model may reflow or
    "fix" it. Left in the pipeline it fails the number check, cannot be repaired,
    and blocks the whole document at the approval gate.
    """
    if URL_PATTERN.search(text):
        return True
    return any(pattern.search(text) for pattern in REFERENCE_PATTERNS)


def is_translatable_text(text: str, role: str | None = None) -> bool:
    """Skip page numbers, axis labels, symbols, and short table/figure labels."""
    cleaned = (text or "").strip()
    # Text that already contains CJK characters needs no Latin word to qualify.
    if CJK_TEXT.search(cleaned):
        return len(cleaned) > 1
    if _is_link_or_reference(cleaned):
        return False
    if not TEXT_WORD_PATTERN.search(cleaned):
        return False
    if len(cleaned) <= 20 and not re.search(r"[A-Za-z]{4,}", cleaned):
        return False
    if role == "paragraph":
        words = re.findall(r"[A-Za-z0-9]+", cleaned)
        if len(words) <= 4 and not re.search(r"[.!?;:]", cleaned):
            return False
    # Keep real headings and captions; drop short labels that carry no word of
    # four letters, which is what chart axes and units look like after OCR.
    if (
        len(cleaned) <= SHORT_LABEL_LIMIT
        and not re.search(r"[A-Za-z]{4,}", cleaned)
        and not CAPTION_PATTERN.match(cleaned)
        and not KNOWN_HEADING_PATTERN.match(cleaned.strip().lower().strip(" .:"))
    ):
        return False
    # Normal prose needs a real word; this drops OCR fragments from figures such
    # as "Hoechst / Sytox green / LysoTracker" that would only add noise.
    if not re.search(r"[A-Za-z]{4,}", cleaned) and not CAPTION_PATTERN.match(cleaned) and not CJK_TEXT.search(cleaned):
        return False
    return True


def _matching_terms(text: str, terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []

    for term in terms:
        names = [term["source"], *term.get("aliases", [])]
        if any(_contains_term(text, name) for name in names):
            matches.append(
                {
                    "source": term["source"],
                    "target": term["target"],
                    "target_aliases": term.get("target_aliases", []),
                    "aliases": term.get("aliases", []),
                    "category": term["category"],
                    "locked": term["locked"],
                }
            )

    return matches


def build_context_bundles(
    structured: dict[str, Any],
    term_memory: dict[str, Any],
) -> dict[str, Any]:
    blocks = _all_blocks(structured)
    headings = {section["id"]: section["heading"] for section in structured.get("sections", [])}
    terms = term_memory.get("terms", [])
    bundles: list[dict[str, Any]] = []

    for index, block in enumerate(blocks):
        if block.get("role") not in TRANSLATABLE_ROLES:
            continue
        if not is_translatable_text(block.get("text", ""), block.get("role")):
            continue

        previous_text = blocks[index - 1]["text"] if index > 0 else ""
        next_text = blocks[index + 1]["text"] if index + 1 < len(blocks) else ""
        section_heading = headings.get(block.get("section_id"), "")
        section_terms = [
            term
            for term in terms
            if any(
                occurrence.get("section_id") == block.get("section_id")
                for occurrence in term["occurrences"]
            )
        ]

        bundles.append(
            {
                "block_id": block["id"],
                "page": block["page"],
                "role": block["role"],
                "section_id": block.get("section_id"),
                "section_heading": section_heading,
                "text": block["text"],
                "previous_text": previous_text,
                "next_text": next_text,
                "matched_terms": _matching_terms(block["text"], terms),
                "section_terms": [
                    {
                        "source": term["source"],
                        "target": term["target"],
                        "target_aliases": term.get("target_aliases", []),
                        "category": term["category"],
                        "locked": term["locked"],
                    }
                    for term in section_terms[:30]
                ],
            }
        )

    return {
        "source_document": structured.get("source"),
        "bundle_count": len(bundles),
        "bundles": bundles,
    }
