"""Build and retrieve document-level medical terminology memory."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
ACRONYM_PATTERN = re.compile(
    r"(?P<long>[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z0-9-]+){0,7})\s*\((?P<acronym>[A-Z][A-Z0-9-]{1,9})\)"
)
CANDIDATE_PATTERN = re.compile(
    r"\b(?:[A-Za-z][A-Za-z0-9-]*\s+){0,2}"
    r"(?:disease|syndrome|infection|cancer|carcinoma|therapy|treatment|inhibitor|"
    r"antibody|protein|receptor|gene|mutation|score|index|criteria|markers?|count|"
    r"levels?|ratio|risk|outcomes?|studies|study|trial|analysis)\b",
    re.IGNORECASE,
)


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def load_seed_glossary(path: str | Path) -> dict[str, dict[str, Any]]:
    glossary_path = Path(path)
    with glossary_path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _sentence_for_occurrence(text: str, start: int, end: int) -> str:
    sentence_start = 0
    sentence_end = len(text)
    for match in SENTENCE_SPLIT.finditer(text):
        if match.end() <= start:
            sentence_start = match.end()
        elif match.start() >= end:
            sentence_end = match.start()
            break
    return text[sentence_start:sentence_end].strip()


def _find_occurrences(text: str, term: str) -> list[dict[str, int | str]]:
    return [
        {
            "start": match.start(),
            "end": match.end(),
            "sentence": _sentence_for_occurrence(text, match.start(), match.end()),
        }
        for match in _term_pattern(term).finditer(text)
    ]


def _iter_text_blocks(structured: dict[str, Any]):
    for page in structured.get("pages", []):
        for block in page.get("blocks", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                yield block


def build_term_memory(
    structured: dict[str, Any],
    glossary: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_source: dict[str, dict[str, Any]] = {}
    candidate_counts: dict[str, int] = defaultdict(int)
    candidate_examples: dict[str, str] = {}

    for block in _iter_text_blocks(structured):
        text = re.sub(r"\s+", " ", block["text"]).strip()

        for source, definition in glossary.items():
            occurrences = _find_occurrences(text, source)
            if source not in by_source and occurrences:
                by_source[source] = {
                    "source": source,
                    "target": definition["target"],
                    "category": definition.get("category", "medical"),
                    "aliases": definition.get("aliases", []),
                    "target_aliases": definition.get("target_aliases", []),
                    "locked": True,
                    "source_type": "seed_glossary",
                    "occurrences": [],
                }

            if source in by_source:
                for occurrence in occurrences:
                    by_source[source]["occurrences"].append(
                        {
                            "block_id": block["id"],
                            "page": block["page"],
                            "section_id": block.get("section_id"),
                            "sentence": occurrence["sentence"],
                        }
                    )

        for match in ACRONYM_PATTERN.finditer(text):
            long_form = re.sub(r"\s+", " ", match.group("long")).strip(" ,;:")
            acronym = match.group("acronym")
            if len(long_form.split()) < 2 or len(long_form) > 80:
                continue
            candidate_counts[acronym] += 1
            candidate_examples[acronym] = long_form

        for match in CANDIDATE_PATTERN.finditer(text):
            candidate = re.sub(r"\s+", " ", match.group(0)).strip().lower()
            if candidate in by_source:
                continue
            candidate_counts[candidate] += 1
            candidate_examples[candidate] = match.group(0).strip()

    terms = sorted(
        by_source.values(),
        key=lambda item: (-len(item["occurrences"]), item["source"].lower()),
    )
    for index, term in enumerate(terms, start=1):
        term["id"] = f"term-{index:04d}"

    unresolved = [
        {
            "source": source,
            "suggested_expansion": candidate_examples.get(source),
            "count": count,
            "status": "unresolved",
        }
        for source, count in sorted(candidate_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    return {
        "source_document": structured.get("source"),
        "term_count": len(terms),
        "terms": terms,
        "unresolved_candidates": unresolved,
    }


def load_term_memory(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as file:
        return json.load(file)
