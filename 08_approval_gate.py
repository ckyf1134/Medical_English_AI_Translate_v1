"""Gate final output on successful automatic quality checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Allow output only when every QA check passes.")
    parser.add_argument("--translations", default="data/output/translations.json")
    parser.add_argument("--qa", default="data/output/qa_report.json")
    parser.add_argument("--output", default="data/output/approved_translations.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    translations = json.loads(Path(args.translations).read_text(encoding="utf-8"))
    report = json.loads(Path(args.qa).read_text(encoding="utf-8"))
    passed_ids = {
        check["block_id"]
        for check in report.get("checks", [])
        if check.get("passed")
    }
    blocked_ids = {
        check["block_id"]
        for check in report.get("checks", [])
        if not check.get("passed")
    }
    approved_items = [
        item
        for item in translations.get("items", [])
        if item["block_id"] in passed_ids
    ]

    result = {
        "approved": report.get("failed_count", 1) == 0,
        "translated_count": len(translations.get("items", [])),
        "approved_count": len(approved_items),
        "blocked_block_ids": sorted(blocked_ids),
        "items": approved_items,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not result["approved"]:
        raise SystemExit(
            f"自动质检未全部通过，已拦截最终输出。失败内容块：{', '.join(result['blocked_block_ids'])}"
        )

    print(f"自动质检门禁通过：{result['approved_count']} 个内容块")
    print(f"结果：{output_path.resolve()}")


if __name__ == "__main__":
    main()
