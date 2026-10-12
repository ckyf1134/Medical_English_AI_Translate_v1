"""Read a PDF and save basic text/layout information."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from med_agent.pdf_extractor import NoTextLayerError, extract_pdf


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract PDF text and layout information.")
    parser.add_argument("--input", default="data/input/sample.pdf", help="Input PDF path.")
    parser.add_argument("--output", default="data/output/extracted.json", help="Output JSON path.")
    ocr_group = parser.add_mutually_exclusive_group()
    ocr_group.add_argument(
        "--ocr",
        dest="ocr",
        action="store_true",
        default=None,
        help="Force OCR even when the PDF already has a text layer.",
    )
    ocr_group.add_argument(
        "--no-ocr",
        dest="ocr",
        action="store_false",
        help="Never run OCR; fail with a clear message when a PDF has no text layer.",
    )
    parser.add_argument("--ocr-dpi", type=int, default=None, help="Render resolution used for OCR.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.is_file():
        raise SystemExit(f"找不到 PDF：{input_path.resolve()}")

    def report(message: str) -> None:
        print(message, flush=True)

    try:
        result = extract_pdf(
            input_path,
            ocr=args.ocr,
            ocr_dpi=args.ocr_dpi,
            progress=report,
        )
    except NoTextLayerError as error:
        raise SystemExit(f"无法解析 PDF：{error}") from error

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    block_count = sum(len(page["blocks"]) for page in result["pages"])
    extraction = result.get("extraction", {})
    print(f"提取完成：{len(result['pages'])} 页，{block_count} 个内容块")
    if extraction.get("ocr", {}).get("applied"):
        ocr = extraction["ocr"]
        print(f"已使用 OCR：{ocr.get('engine')}（{ocr.get('language')}），生成 {ocr.get('blocks')} 个文字行")
    print(f"结果：{output_path.resolve()}")


if __name__ == "__main__":
    main()
