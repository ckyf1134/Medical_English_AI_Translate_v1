"""Build a semantic document structure from extracted PDF blocks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from med_agent.document_structurer import load_and_structure


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recognize document sections and block roles.")
    parser.add_argument("--input", default="data/output/extracted.json", help="Extracted JSON path.")
    parser.add_argument("--output", default="data/output/structured.json", help="Output JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.is_file():
        raise SystemExit(f"找不到解析结果：{input_path.resolve()}")

    result = load_and_structure(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print(f"结构识别完成：{len(result['sections'])} 个章节")
    for section in result["sections"]:
        print(f"  {section['id']}  第 {section['page_start']} 页  {section['heading']}")
    print(f"结果：{output_path.resolve()}")


if __name__ == "__main__":
    main()
