"""Translate structured blocks with locked terminology and local context."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from med_agent.context_retriever import is_translatable_text
from med_agent.ollama_client import OllamaError, chat_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Translate document blocks with Ollama.")
    parser.add_argument("--input", default="data/output/translation_context.json")
    parser.add_argument("--config", default="configs/models.json")
    parser.add_argument("--prompt", default="prompts/medical_translation_system.txt")
    parser.add_argument("--output", default="data/output/translations.json")
    parser.add_argument("--model", default=None, help="Override the translation model.")
    parser.add_argument("--base-url", default=None, help="Override the Ollama base URL.")
    parser.add_argument("--limit", type=int, default=None, help="Translate only the first N blocks.")
    parser.add_argument("--retries", type=int, default=2)
    return parser.parse_args()


def _unique_terms(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    terms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term in [*bundle.get("matched_terms", []), *bundle.get("section_terms", [])]:
        key = term["source"].lower()
        if key not in seen:
            seen.add(key)
            terms.append(term)
    return terms


def _build_user_prompt(bundle: dict[str, Any]) -> str:
    payload = {
        "page": bundle["page"],
        "section": bundle.get("section_heading", ""),
        "block_role": bundle["role"],
        "previous_block": bundle.get("previous_text", ""),
        "current_block": bundle["text"],
        "next_block": bundle.get("next_text", ""),
        "locked_terms": _unique_terms(bundle),
    }
    locked_targets = [
        term["target"]
        for term in _unique_terms(bundle)
        if term.get("locked") and term.get("target")
    ]
    required = "、".join(dict.fromkeys(locked_targets))
    requirement_text = f"译文必须逐字包含这些锁定术语：{required}。\n" if required else ""
    return (
        "请翻译 current_block。previous_block 和 next_block 只用于理解上下文，不得翻译或合并。\n"
        f"{requirement_text}"
        "返回 JSON 格式：{\"translation\":\"...\",\"used_terms\":[],\"uncertainties\":[]}。\n"
        f"输入：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def _load_existing(path: Path, model: str) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if data.get("model") != model:
        return {}
    return {
        item["block_id"]: item
        for item in data.get("items", [])
        if item.get("block_id") and item.get("translation")
    }


def _save(path: Path, model: str, items: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model": model,
        "translated_count": len([item for item in items if item.get("translation")]),
        "items": items,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    args = parse_args()
    context_data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    system_prompt = Path(args.prompt).read_text(encoding="utf-8")
    translation_config = config["translation"]
    model = args.model or translation_config["model"]
    output_path = Path(args.output)
    existing = _load_existing(output_path, model)

    bundles = context_data.get("bundles", [])
    if args.limit is not None:
        bundles = bundles[: args.limit]

    items_by_id: dict[str, dict[str, Any]] = dict(existing)
    for index, bundle in enumerate(bundles, start=1):
        block_id = bundle["block_id"]
        if block_id in items_by_id:
            print(f"[{index}/{len(bundles)}] 跳过已完成 {block_id}")
            continue

        if not is_translatable_text(bundle.get("text", "")):
            items_by_id[block_id] = {
                "block_id": block_id,
                "page": bundle["page"],
                "role": bundle["role"],
                "source_text": bundle["text"],
                "translation": bundle["text"],
                "used_terms": [],
                "uncertainties": [],
                "status": "skipped_non_translatable",
                "model_metadata": {},
                "elapsed_seconds": 0.0,
            }
            _save(output_path, model, list(items_by_id.values()))
            print(f"[{index}/{len(bundles)}] 跳过非文字块 {block_id}")
            continue

        print(f"[{index}/{len(bundles)}] 翻译 {block_id} ({bundle['role']}) ...", flush=True)
        prompt = _build_user_prompt(bundle)
        last_error: Exception | None = None

        for attempt in range(1, args.retries + 2):
            started = time.perf_counter()
            try:
                response, metadata = chat_json(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    base_url=args.base_url or config.get("base_url", "http://127.0.0.1:11434"),
                    num_ctx=int(translation_config.get("num_ctx", 8192)),
                    temperature=float(translation_config.get("temperature", 0.0)),
                    think=False,
                )
                translation = (
                    response.get("translation")
                    or response.get("translated_text")
                    or response.get("output")
                    or response.get("text")
                )
                if not isinstance(translation, str) or not translation.strip():
                    raise OllamaError(
                        "Translation response has no non-empty translation field. "
                        f"Keys={sorted(response.keys())}"
                    )

                items_by_id[block_id] = {
                    "block_id": block_id,
                    "page": bundle["page"],
                    "role": bundle["role"],
                    "source_text": bundle["text"],
                    "translation": translation.strip(),
                    "used_terms": response.get("used_terms", []),
                    "uncertainties": response.get("uncertainties", []),
                    "model_metadata": metadata,
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                }
                _save(output_path, model, list(items_by_id.values()))
                break
            except Exception as error:
                last_error = error
                print(f"  第 {attempt} 次失败：{error}")
                if attempt <= args.retries:
                    time.sleep(1.5)
        else:
            raise SystemExit(f"翻译失败：{block_id}；最后错误：{last_error}")

    _save(output_path, model, list(items_by_id.values()))
    print(f"翻译完成：{len(items_by_id)} 个内容块")
    print(f"结果：{output_path.resolve()}")


if __name__ == "__main__":
    main()
