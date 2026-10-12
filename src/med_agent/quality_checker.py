"""Rule-based quality checks for medical document translation."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?![A-Za-z])")
CITATION_PATTERN = re.compile(r"\[(?:\d+[\s,;–-]*)*\d+\]")
UNIT_GROUPS = {
    "mg": r"(?<![A-Za-z])mg(?![A-Za-z])|毫克",
    "g": r"(?<![A-Za-z])g(?![A-Za-z])|克",
    "kg": r"(?<![A-Za-z])kg(?![A-Za-z])|千克|公斤",
    "ug": r"(?<![A-Za-z])(?:µg|ug)(?![A-Za-z])|微克",
    "ng": r"(?<![A-Za-z])ng(?![A-Za-z])|纳克",
    "ml": r"(?<![A-Za-z])mL(?![A-Za-z])|毫升",
    "l": r"(?<![A-Za-z])L(?![A-Za-z])|升",
    "dl": r"(?<![A-Za-z])dL(?![A-Za-z])|分升",
    "mmol_l": r"(?<![A-Za-z])mmol/L(?![A-Za-z])|毫摩尔/升",
    "mol_l": r"(?<![A-Za-z])mol/L(?![A-Za-z])|摩尔/升",
    "mmhg": r"(?<![A-Za-z])(?:mm Hg|mmHg)(?![A-Za-z])|毫米汞柱",
    "bpm": r"(?<![A-Za-z])bpm(?![A-Za-z])|次/分",
    "year": r"(?<![A-Za-z])years?(?![A-Za-z])|年",
    "day": r"(?<![A-Za-z])days?(?![A-Za-z])|天|日",
    "hour": r"(?<![A-Za-z])hours?(?![A-Za-z])|小时",
    "week": r"(?<![A-Za-z])weeks?(?![A-Za-z])|周",
    "month": r"(?<![A-Za-z])months?(?![A-Za-z])|月",
    "percent": r"%(?![A-Za-z])|百分之",
}

# "lack" and the "un-"/"in-" prefixed forms are negations as well. Without them a
# correct translation such as "lack of targeting" -> "缺乏靶向性" or
# "remained unaffected" -> "未受影响" is reported as a mismatch, retranslated in
# vain, and then blocks the whole document at the approval gate.
EN_NEGATIONS = re.compile(
    r"\b(?:no|not|never|none|neither|nor|without|failed to|did not|does not|"
    r"do not|was not|were not|cannot|can't|isn't|aren't|excluded?|exclusion|omitted|absent|absence|"
    r"lack(?:s|ed|ing)?|"
    r"un(?:affected|changed|clear|known|treated|able|likely|successful|common|necessary)|"
    r"in(?:adequate|sufficient|complete|significant|effective|conclusive))\b",
    re.IGNORECASE,
)
ZH_NEGATIONS = re.compile(r"(?:不(?!良|同|仅|但|论)|无|未|非|没有|不能|不可|禁止|缺乏|缺少|排除|否认|阴性)")


def _number_counter(text: str) -> Counter[str]:
    values: list[str] = []
    for match in NUMBER_PATTERN.finditer(text):
        value = match.group(0).replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        values.append(value)
    return Counter(values)


def _unit_counter(text: str) -> Counter[str]:
    counts: Counter[str] = Counter()
    for canonical, pattern in UNIT_GROUPS.items():
        counts[canonical] = len(re.findall(pattern, text, flags=re.IGNORECASE))
    return counts


def _citation_counter(text: str) -> Counter[str]:
    return Counter(re.sub(r"\s+", "", match.group(0)) for match in CITATION_PATTERN.finditer(text))


def _english_ratio(text: str) -> float:
    letters = len(re.findall(r"[A-Za-z]", text))
    chinese = len(re.findall(r"[\u4e00-\u9fff]", text))
    visible = letters + chinese
    if visible == 0:
        return 0.0
    return letters / visible


def check_translation(
    item: dict[str, Any],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    source = item.get("source_text", "")
    target = item.get("translation", "")
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    source_numbers = _number_counter(source)
    target_numbers = _number_counter(target)
    missing_numbers = source_numbers - target_numbers
    if missing_numbers:
        errors.append(
            {
                "code": "missing_numbers",
                "message": "译文缺少或改变了原文数字",
                "values": sorted(missing_numbers.elements()),
            }
        )

    source_citations = _citation_counter(source)
    target_citations = _citation_counter(target)
    missing_citations = source_citations - target_citations
    if missing_citations:
        errors.append(
            {
                "code": "missing_citations",
                "message": "译文缺少引用编号",
                "values": sorted(missing_citations.elements()),
            }
        )

    source_units = _unit_counter(source)
    target_units = _unit_counter(target)
    missing_units = source_units - target_units
    if missing_units:
        errors.append(
            {
                "code": "missing_units",
                "message": "译文缺少原文单位",
                "values": sorted(missing_units.elements()),
            }
        )

    for term in bundle.get("matched_terms", []):
        targets = [term["target"], *term.get("target_aliases", [])]
        if not term.get("locked"):
            continue
        if any(candidate in target for candidate in targets):
            continue
        # Medical papers have a standard abbreviation for many terms and it is
        # correct practice to introduce the Chinese name once and then keep the
        # abbreviation ("中性粒细胞胞外诱捕网（NETs）" ... "NETs"). Keeping the
        # source term or one of its aliases is therefore acceptable, so
        # "NETs" satisfies the locked "neutrophil extracellular traps".
        sources = [term["source"], *term.get("aliases", [])]
        if any(candidate in target for candidate in sources):
            continue
        errors.append(
            {
                "code": "missing_locked_term",
                "message": "未使用文章级锁定术语",
                "source": term["source"],
                "target": term["target"],
            }
        )

    source_negative = bool(EN_NEGATIONS.search(source))
    target_negative = bool(ZH_NEGATIONS.search(target))
    if source_negative != target_negative:
        errors.append(
            {
                "code": "negation_mismatch",
                "message": "原文与译文的否定关系不一致",
                "source_negative": source_negative,
                "target_negative": target_negative,
            }
        )

    role = item.get("role")
    source_words = len(re.findall(r"[A-Za-z0-9]+", source))
    target_chinese = len(re.findall(r"[\u4e00-\u9fff]", target))
    if role in {"paragraph", "table"} and source_words >= 12:
        if target_chinese < source_words * 0.35:
            errors.append(
                {
                    "code": "suspiciously_short",
                    "message": "译文可能明显漏译",
                    "source_words": source_words,
                    "target_chinese_characters": target_chinese,
                }
            )
        elif target_chinese > source_words * 3.5:
            warnings.append(
                {
                    "code": "suspiciously_long",
                    "message": "译文明显长于原文，可能增译",
                    "source_words": source_words,
                    "target_chinese_characters": target_chinese,
                }
            )

    if role in {"paragraph", "table"} and _english_ratio(target) > 0.55:
        errors.append(
            {
                "code": "untranslated_english",
                "message": "译文中英文占比过高，可能未完成翻译",
                "english_ratio": round(_english_ratio(target), 3),
            }
        )

    return {
        "block_id": item["block_id"],
        "page": item.get("page"),
        "role": role,
        "passed": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def build_quality_report(
    translations: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    bundles = {bundle["block_id"]: bundle for bundle in context.get("bundles", [])}
    # Only check blocks the context asked for. A block that was filtered out (for
    # example a DOI line) can still sit in an older translations.json; checking it
    # would fail the report and block a document that has nothing wrong with it.
    items = [
        item
        for item in translations.get("items", [])
        if item.get("block_id") in bundles
    ]
    checks = [check_translation(item, bundles[item["block_id"]]) for item in items]
    return {
        "source_document": context.get("source_document"),
        "translated_count": len(checks),
        "passed_count": sum(1 for check in checks if check["passed"]),
        "failed_count": sum(1 for check in checks if not check["passed"]),
        "checks": checks,
    }
