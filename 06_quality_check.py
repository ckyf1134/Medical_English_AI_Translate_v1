"""Run deterministic quality checks on translated blocks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from med_agent.quality_checker import build_quality_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check translated blocks for high-risk errors.")
    parser.add_argument("--translations", default="data/output/translations.json")
    parser.add_argument("--context", default="data/output/translation_context.json")
    parser.add_argument("--output", default="data/output/qa_report.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    translations = json.loads(Path(args.translations).read_text(encoding="utf-8"))
    context = json.loads(Path(args.context).read_text(encoding="utf-8"))
    report = build_quality_report(translations, context)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"质检完成：通过 {report['passed_count']}，失败 {report['failed_count']}")
    for check in report["checks"]:
        if not check["passed"]:
            codes = ", ".join(error["code"] for error in check["errors"])
            print(f"  失败 {check['block_id']}：{codes}")
    print(f"结果：{output_path.resolve()}")


if __name__ == "__main__":
    main()
