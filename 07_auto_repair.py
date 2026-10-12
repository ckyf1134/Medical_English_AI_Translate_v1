"""Automatically retranslate blocks that fail deterministic quality checks."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from med_agent.ollama_client import OllamaError, chat_json
from med_agent.quality_checker import check_translation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retry failed translations with QA feedback.")
    parser.add_argument("--translations", default="data/output/translations.json")
    parser.add_argument("--context", default="data/output/translation_context.json")
    parser.add_argument("--qa", default="data/output/qa_report.json")
    parser.add_argument("--config", default="configs/models.json")
    parser.add_argument("--prompt", default="prompts/medical_translation_system.txt")
    parser.add_argument("--output", default="data/output/translations.json")
    parser.add_argument("--report", default="data/output/qa_report.json")
    parser.add_argument("--model", default=None)
    parser.add_argument("--fallback-model", default=None, help="Second model used on retry.")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--attempts", type=int, default=2)
    return parser.parse_args()


def _locked_term_hint(bundle: dict[str, Any]) -> str:
    """Describe locked terms, allowing the paper's own abbreviation."""
    parts: list[str] = []
    for term in bundle.get("matched_terms", []):
        if not term.get("locked") or not term.get("target"):
            continue
        alternatives = [term["target"], *term.get("target_aliases", [])]
        # Keeping the source abbreviation is correct medical practice.
        alternatives.extend([term["source"], *term.get("aliases", [])])
        unique = "或".join(dict.fromkeys(alternative for alternative in alternatives if alternative))
        parts.append(unique)
    if not parts:
        return ""
    return f"完整译文中必须出现这些锁定术语（可用括号内的英文简称）：{'、'.join(parts)}。\n"


def _repair_prompt(item: dict[str, Any], bundle: dict[str, Any], check: dict[str, Any]) -> str:
    payload = {
        "page": item.get("page"),
        "section": bundle.get("section_heading", ""),
        "block_role": item.get("role"),
        "previous_block": bundle.get("previous_text", ""),
        "source_block": item.get("source_text", ""),
        "previous_translation": item.get("translation", ""),
        "quality_errors": check.get("errors", []),
        "locked_terms": bundle.get("matched_terms", []),
    }
    requirement_text = _locked_term_hint(bundle)
    return (
        "上一次译文未通过自动质检。请根据质量错误逐项修复并完整重译 source_block。\n"
        f"{requirement_text}"
        "不要只输出修改片段，不要解释。只返回 JSON："
        "{\"translation\":\"完整译文\",\"used_terms\":[],\"uncertainties\":[]}。\n"
        f"输入：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    translations = json.loads(Path(args.translations).read_text(encoding="utf-8"))
    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    qa_report = json.loads(Path(args.qa).read_text(encoding="utf-8"))
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    system_prompt = Path(args.prompt).read_text(encoding="utf-8")

    primary_model = args.model or config["translation"]["model"]
    fallback_model = args.fallback_model or config["review"]["model"]
    repair_models = [primary_model]
    if fallback_model and fallback_model != primary_model:
        repair_models.append(fallback_model)
    bundles = {bundle["block_id"]: bundle for bundle in context.get("bundles", [])}
    items = {item["block_id"]: item for item in translations.get("items", [])}
    checks = {check["block_id"]: check for check in qa_report.get("checks", [])}
    failed_ids = [block_id for block_id, check in checks.items() if not check["passed"]]

    repaired = 0
    for index, block_id in enumerate(failed_ids, start=1):
        item = items[block_id]
        bundle = bundles[block_id]
        history = item.setdefault("retry_history", [])

        for attempt in range(1, args.attempts + 1):
            attempt_model = repair_models[min(attempt - 1, len(repair_models) - 1)]
            print(
                f"[{index}/{len(failed_ids)}] 自动重译 {block_id}，第 {attempt} 次 "
                f"({attempt_model}) ...",
                flush=True,
            )
            started = time.perf_counter()
            try:
                response, metadata = chat_json(
                    model=attempt_model,
                    system_prompt=system_prompt,
                    user_prompt=_repair_prompt(item, bundle, checks[block_id]),
                    base_url=args.base_url or config.get("base_url", "http://127.0.0.1:11434"),
                    num_ctx=int(config["translation"].get("num_ctx", 8192)),
                    temperature=float(config["translation"].get("temperature", 0.0)),
                    think=False,
                )
                translation = response.get("translation")
                if not isinstance(translation, str) or not translation.strip():
                    raise OllamaError("Repair response has no non-empty translation field.")

                item["previous_translation"] = item.get("translation")
                item["translation"] = translation.strip()
                item["used_terms"] = response.get("used_terms", [])
                item["uncertainties"] = response.get("uncertainties", [])
                item["model_metadata"] = metadata
                checks[block_id] = check_translation(item, bundle)
                history.append(
                    {
                        "attempt": attempt,
                        "model": attempt_model,
                        "elapsed_seconds": round(time.perf_counter() - started, 2),
                        "passed": checks[block_id]["passed"],
                        "errors": checks[block_id]["errors"],
                    }
                )
                _write_json(Path(args.output), translations)
                _write_json(
                    Path(args.report),
                    {
                        **qa_report,
                        "passed_count": sum(1 for check in checks.values() if check["passed"]),
                        "failed_count": sum(1 for check in checks.values() if not check["passed"]),
                        "checks": list(checks.values()),
                    },
                )
                if checks[block_id]["passed"]:
                    repaired += 1
                    break
            except Exception as error:
                history.append({"attempt": attempt, "passed": False, "exception": str(error)})
                _write_json(Path(args.output), translations)
                print(f"  重译失败：{error}")

    final_checks = list(checks.values())
    updated_report = {
        **qa_report,
        "passed_count": sum(1 for check in final_checks if check["passed"]),
        "failed_count": sum(1 for check in final_checks if not check["passed"]),
        "checks": final_checks,
    }
    _write_json(Path(args.report), updated_report)
    _write_json(Path(args.output), translations)
    print(f"自动重译完成：修复 {repaired}，仍失败 {updated_report['failed_count']}")


if __name__ == "__main__":
    main()
