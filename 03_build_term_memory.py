"""Build a document-level terminology memory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from med_agent.term_memory import build_term_memory, load_seed_glossary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract and lock document terminology.")
    parser.add_argument("--input", default="data/output/structured.json")
    parser.add_argument("--glossary", default="configs/medical_glossary_seed.json")
    parser.add_argument("--output", default="data/output/term_memory.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with Path(args.input).open("r", encoding="utf-8") as file:
        structured = json.load(file)

    glossary = load_seed_glossary(args.glossary)
    result = build_term_memory(structured, glossary)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print(f"术语记忆完成：锁定 {result['term_count']} 个术语")
    for term in result["terms"][:12]:
        print(f"  {term['source']} -> {term['target']}  出现 {len(term['occurrences'])} 次")
    print(f"结果：{output_path.resolve()}")


if __name__ == "__main__":
    main()
