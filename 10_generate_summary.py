"""Generate a structured Chinese summary through the review model."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from med_agent.ollama_client import chat_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a structured medical literature summary.")
    parser.add_argument("--structured", default="data/output/structured.json")
    parser.add_argument("--translations", default="data/output/approved_translations.json")
    parser.add_argument("--config", default="configs/models.json")
    parser.add_argument("--prompt", default="prompts/summary_system.txt")
    parser.add_argument("--output", default="data/output/summary.json")
    parser.add_argument("--markdown", default="data/output/summary.md")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    return parser.parse_args()


def _document_text(structured: dict[str, Any], approved: dict[str, Any]) -> str:
    translations = {item["block_id"]: item for item in approved.get("items", [])}
    chunks: list[str] = []
    section_heading = ""
    for page in structured.get("pages", []):
        for block in page.get("blocks", []):
            role = block.get("role")
            if role == "heading":
                section_heading = block.get("text", "")
                continue
            if role not in {"title", "paragraph", "table", "table_caption", "figure_caption"}:
                continue
            item = translations.get(block.get("id"))
            text = item.get("translation") if item else block.get("text")
            if not text:
                continue
            prefix = f"[{section_heading}] " if section_heading else ""
            chunks.append(prefix + text.strip())
    return "\n\n".join(chunks)


def _to_markdown(summary: dict[str, Any]) -> str:
    lines = [f"# {summary.get('title', '文献总结')}", ""]
    if summary.get("overview"):
        lines.extend(["## 总览", str(summary["overview"]), ""])
    if summary.get("research_question"):
        lines.extend(["## 研究问题", str(summary["research_question"]), ""])
    for key, label in [
        ("methods", "方法"),
        ("key_findings", "主要发现"),
        ("conclusions", "结论"),
        ("limitations", "局限性"),
        ("clinical_implications", "临床意义"),
        ("keywords", "关键词"),
    ]:
        values = summary.get(key) or []
        lines.append(f"## {label}")
        if isinstance(values, list):
            lines.extend([f"- {value}" for value in values] or ["- 未明确说明"])
        else:
            lines.append(str(values))
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    args = parse_args()
    structured = json.loads(Path(args.structured).read_text(encoding="utf-8"))
    approved = json.loads(Path(args.translations).read_text(encoding="utf-8"))
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    system_prompt = Path(args.prompt).read_text(encoding="utf-8")
    model = args.model or config["review"]["model"]
    document_text = _document_text(structured, approved)

    prompt = (
        "请总结下面的医学文献文本。只依据文本，返回指定 JSON。\n\n"
        f"文献内容：\n{document_text[:16000]}"
    )
    started = time.perf_counter()
    summary, metadata = chat_json(
        model=model,
        system_prompt=system_prompt,
        user_prompt=prompt,
        base_url=args.base_url or config.get("base_url", "http://127.0.0.1:11434"),
        num_ctx=int(config["review"].get("num_ctx", 8192)),
        temperature=float(config["review"].get("temperature", 0.0)),
        think=False,
    )
    summary["_metadata"] = {
        **metadata,
        "elapsed_seconds": round(time.perf_counter() - started, 2),
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path = Path(args.markdown)
    markdown_path.write_text(_to_markdown(summary), encoding="utf-8")

    print(f"总结完成：{model}")
    print(f"JSON：{output_path.resolve()}")
    print(f"Markdown：{markdown_path.resolve()}")


if __name__ == "__main__":
    main()
