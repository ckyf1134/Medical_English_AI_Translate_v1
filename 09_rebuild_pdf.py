"""Render approved translations back into PDF layouts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from med_agent.pdf_builder import (
    build_bilingual_pdf,
    build_flowing_bilingual_pdf,
    build_flowing_translated_pdf,
    build_translated_pdf,
    document_uses_ocr,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild Chinese and bilingual PDFs.")
    parser.add_argument("--original", default="data/input/sample.pdf")
    parser.add_argument("--structured", default="data/output/structured.json")
    parser.add_argument("--translations", default="data/output/approved_translations.json")
    parser.add_argument("--output", default="data/output/translated_cn.pdf")
    parser.add_argument("--bilingual-output", default="data/output/bilingual.pdf")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    structured = json.loads(Path(args.structured).read_text(encoding="utf-8"))
    approved = json.loads(Path(args.translations).read_text(encoding="utf-8"))

    if document_uses_ocr(structured):
        # A scan keeps the original English as pixels, so the translation is
        # typeset on fresh pages instead of being overlaid onto the image.
        print("检测到 OCR 文档（扫描版 PDF），译文将按阅读顺序重新排版")
        result = build_flowing_translated_pdf(structured, approved, args.output)
        bilingual = build_flowing_bilingual_pdf(args.original, args.output, args.bilingual_output)
    else:
        result = build_translated_pdf(args.original, structured, approved, args.output)
        bilingual = build_bilingual_pdf(args.original, args.output, args.bilingual_output)

    print(f"中文 PDF 完成，警告 {result['warning_count']} 个")
    print(f"中文版：{result['output_pdf']}")
    print(f"中英对照版：{bilingual}")


if __name__ == "__main__":
    main()
