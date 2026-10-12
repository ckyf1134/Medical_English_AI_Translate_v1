"""Build context bundles used by the translation model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from med_agent.context_retriever import build_context_bundles


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retrieve context and locked terms for each block.")
    parser.add_argument("--document", default="data/output/structured.json")
    parser.add_argument("--terms", default="data/output/term_memory.json")
    parser.add_argument("--output", default="data/output/translation_context.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with Path(args.document).open("r", encoding="utf-8") as file:
        structured = json.load(file)
    with Path(args.terms).open("r", encoding="utf-8") as file:
        term_memory = json.load(file)

    result = build_context_bundles(structured, term_memory)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print(f"上下文检索完成：{result['bundle_count']} 个待翻译内容块")
    print(f"结果：{output_path.resolve()}")


if __name__ == "__main__":
    main()
