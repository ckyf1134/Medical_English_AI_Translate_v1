"""Local offline WebUI for the medical document translation agent."""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import json
import mimetypes
import re
import runpy
import shutil
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "webui" / "static"
JOBS_DIR = ROOT / "data" / "jobs"
CACHE_DIR = ROOT / "data" / "cache"
OUTPUT_ROOT = ROOT / "data" / "output"
CONFIG_PATH = ROOT / "configs" / "models.json"
MAX_UPLOAD_BYTES = 300 * 1024 * 1024

OUTPUT_FILES = [
    ("translated_cn.pdf", "中文 PDF"),
    ("bilingual.pdf", "中英对照 PDF"),
    ("summary.md", "文献总结"),
    ("glossary.csv", "术语表"),
    ("qa_report.json", "质检报告"),
    ("approved_translations.json", "最终译文"),
]
PUBLISH_FILES = [
    "input.pdf", "extracted.json", "structured.json", "term_memory.json",
    "glossary.csv", "translation_context.json", "translations.json",
    "qa_report.json", "approved_translations.json", "translated_cn.pdf",
    "bilingual.pdf", "summary.json", "summary.md",
]

def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

def _safe_folder_name(filename: str, digest: str) -> str:
    stem = Path(filename).stem or "document"
    stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", stem).strip("._-")
    stem = stem[:60] or "document"
    return f"{stem}__{digest[:12]}"

class JobManager:
    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        self.execution_lock = threading.Lock()
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        self._load_existing_jobs()

    def _state_path(self, job_id: str) -> Path:
        return JOBS_DIR / job_id / "job.json"

    def _persist(self, job: dict[str, Any]) -> None:
        path = self._state_path(job["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    def _collect_files(self, output_dir: Path) -> list[dict[str, str]]:
        files: list[dict[str, str]] = []
        for filename, label in OUTPUT_FILES:
            path = output_dir / filename
            if path.is_file():
                url = f"/api/outputs/{urllib.parse.quote(output_dir.name)}/files/{urllib.parse.quote(filename)}"
                files.append({"name": filename, "label": label, "size": str(path.stat().st_size), "url": url})
        return files

    def _legacy_output_dir(self, job: dict[str, Any]) -> None:
        output_dir = Path(job.get("output_dir", "")).resolve()
        if output_dir.is_relative_to(OUTPUT_ROOT.resolve()):
            job["output_folder"] = output_dir.name
            return
        input_pdf = output_dir / "input.pdf"
        if not input_pdf.is_file() or job.get("status") != "completed":
            return
        digest = hashlib.sha256(input_pdf.read_bytes()).hexdigest()
        target = OUTPUT_ROOT / _safe_folder_name(job.get("filename", input_pdf.name), digest)
        target.mkdir(parents=True, exist_ok=True)
        for filename in PUBLISH_FILES:
            source = output_dir / filename
            if source.is_file():
                shutil.copy2(source, target / filename)
        job["output_dir"] = str(target.resolve())
        job["output_folder"] = target.name
        job["files"] = self._collect_files(target.resolve())
        self._persist(job)

    def _load_existing_jobs(self) -> None:
        for state_path in JOBS_DIR.glob("*/job.json"):
            try:
                job = json.loads(state_path.read_text(encoding="utf-8"))
                if job.get("status") in {"queued", "running"}:
                    job["status"] = "interrupted"
                    job["message"] = "程序重启，任务已中断"
                self._legacy_output_dir(job)
                self.jobs[job["id"]] = job
            except (OSError, json.JSONDecodeError, KeyError):
                continue

    def create(self, filename: str, content: bytes) -> dict[str, Any]:
        job_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        work_dir = JOBS_DIR / job_id
        work_dir.mkdir(parents=True, exist_ok=False)
        (work_dir / "input.pdf").write_bytes(content)

        digest = _sha256_bytes(content)
        output_dir = OUTPUT_ROOT / _safe_folder_name(filename, digest)
        output_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "id": job_id,
            "filename": filename,
            "input_sha256": digest,
            "output_folder": output_dir.name,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "message": "任务已创建",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "logs": [],
            "files": [],
            "work_dir": str(work_dir.resolve()),
            "output_dir": str(output_dir.resolve()),
        }
        with self.lock:
            self.jobs[job_id] = job
            self._persist(job)
        threading.Thread(target=self._run, args=(job_id,), daemon=True).start()
        return self.snapshot(job_id)

    def snapshot(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.jobs[job_id], ensure_ascii=False))

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            jobs = list(self.jobs.values())
        return sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True)

    def _update(self, job_id: str, **values: Any) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.update(values)
            self._persist(job)

    def _append_log(self, job_id: str, text: str) -> None:
        if not text:
            return
        with self.lock:
            job = self.jobs[job_id]
            job.setdefault("logs", []).append(text)
            job["logs"] = job["logs"][-250:]
            self._persist(job)

    def _run_script(self, job_id: str, script_name: str, args: list[str]) -> None:
        class LiveLog:
            def __init__(self) -> None:
                self.pending = ""

            def write(self, value: str) -> int:
                self.pending += value
                while "\n" in self.pending:
                    line, self.pending = self.pending.split("\n", 1)
                    MANAGER._append_log(job_id, line + "\n")
                    match = re.match(r"\[(\d+)/(\d+)\]", line)
                    if match:
                        current, total = int(match.group(1)), max(1, int(match.group(2)))
                        if script_name == "05_translate_document.py":
                            progress = 30 + int(30 * current / total)
                            MANAGER._update(job_id, progress=progress, message=f"正在翻译 {current}/{total}")
                        elif script_name == "07_auto_repair.py":
                            progress = 70 + int(8 * current / total)
                            MANAGER._update(job_id, progress=progress, message=f"正在自动重译 {current}/{total}")
                return len(value)

            def flush(self) -> None:
                if self.pending:
                    MANAGER._append_log(job_id, self.pending)
                    self.pending = ""

        script_path = ROOT / script_name
        old_argv = sys.argv
        live_log = LiveLog()
        sys.argv = [str(script_path), *args]
        exit_message: str | None = None
        try:
            with contextlib.redirect_stdout(live_log), contextlib.redirect_stderr(live_log):
                runpy.run_path(str(script_path), run_name="__main__")
        except SystemExit as error:
            code = error.code
            if code in (None, 0):
                pass
            elif isinstance(code, str):
                # Scripts call SystemExit("原因") for user-facing problems such as
                # a scanned PDF without OCR; surface that text, not the exit code.
                exit_message = code
            else:
                exit_message = f"{script_name} 退出，代码：{code}"
        finally:
            live_log.flush()
            sys.argv = old_argv
        if exit_message:
            raise RuntimeError(exit_message)

    def _write_glossary(self, term_memory_path: Path, output_path: Path) -> None:
        data = json.loads(term_memory_path.read_text(encoding="utf-8"))
        with output_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["英文术语", "中文译名", "类别", "出现次数", "别名", "是否锁定"])
            for term in data.get("terms", []):
                writer.writerow([
                    term.get("source", ""),
                    term.get("target", ""),
                    term.get("category", ""),
                    len(term.get("occurrences", [])),
                    "; ".join(term.get("aliases", [])),
                    "是" if term.get("locked") else "否",
                ])

    def _publish_outputs(self, job_id: str, work_dir: Path, output_dir: Path) -> list[dict[str, str]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        for filename in PUBLISH_FILES:
            source = work_dir / filename
            if source.is_file():
                shutil.copy2(source, output_dir / filename)
        job = self.snapshot(job_id)
        files = self._collect_files(output_dir)
        manifest = {
            "job_id": job_id,
            "filename": job["filename"],
            "input_sha256": job.get("input_sha256"),
            "output_folder": output_dir.name,
            "completed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": files,
        }
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        history_path = output_dir / "run_history.json"
        try:
            history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.is_file() else []
        except json.JSONDecodeError:
            history = []
        history.append({
            "job_id": job_id,
            "completed_at": manifest["completed_at"],
            "input_sha256": manifest["input_sha256"],
        })
        history_path.write_text(json.dumps(history[-20:], ensure_ascii=False, indent=2), encoding="utf-8")
        return files

    def cache_stats(self) -> dict[str, int]:
        total = 0
        count = 0
        for root in (JOBS_DIR, CACHE_DIR):
            if not root.exists():
                continue
            for path in root.rglob("*"):
                if path.is_file():
                    total += path.stat().st_size
                    count += 1
        with self.lock:
            jobs = len(self.jobs)
        return {"size_bytes": total, "file_count": count, "job_count": jobs}

    def clear_cache(self) -> dict[str, int]:
        with self.lock:
            if any(job.get("status") in {"queued", "running"} for job in self.jobs.values()):
                raise RuntimeError("当前仍有任务在运行，不能清理缓存")
        stats = self.cache_stats()

        def remove_tree(path: Path, allowed_root: Path) -> None:
            resolved = path.resolve()
            if not resolved.is_relative_to(allowed_root.resolve()):
                raise RuntimeError(f"拒绝删除缓存目录之外的路径：{resolved}")
            if resolved.is_dir():
                shutil.rmtree(resolved)
            elif resolved.exists():
                resolved.unlink()

        for child in JOBS_DIR.iterdir():
            remove_tree(child, JOBS_DIR)
        for child in CACHE_DIR.iterdir():
            remove_tree(child, CACHE_DIR)
        with self.lock:
            self.jobs.clear()
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return stats

    def _run(self, job_id: str) -> None:
        with self.execution_lock:
            job = self.snapshot(job_id)
            work_dir = Path(job["work_dir"])
            output_dir = Path(job["output_dir"])
            paths = {
                "input": work_dir / "input.pdf",
                "extracted": work_dir / "extracted.json",
                "structured": work_dir / "structured.json",
                "terms": work_dir / "term_memory.json",
                "glossary": work_dir / "glossary.csv",
                "context": work_dir / "translation_context.json",
                "translations": work_dir / "translations.json",
                "qa": work_dir / "qa_report.json",
                "approved": work_dir / "approved_translations.json",
                "translated_pdf": work_dir / "translated_cn.pdf",
                "bilingual_pdf": work_dir / "bilingual.pdf",
                "summary_json": work_dir / "summary.json",
                "summary_md": work_dir / "summary.md",
            }
            self._update(job_id, status="running", started_at=time.strftime("%Y-%m-%d %H:%M:%S"), message="开始处理")
            steps = [
                ("extracting", 5, "解析 PDF 文本和版面", "01_extract_pdf.py", ["--input", str(paths["input"]), "--output", str(paths["extracted"])]),
                ("structuring", 12, "识别章节和内容块", "02_structure_document.py", ["--input", str(paths["extracted"]), "--output", str(paths["structured"])]),
                ("terminology", 18, "构建文章术语记忆", "03_build_term_memory.py", ["--input", str(paths["structured"]), "--output", str(paths["terms"])]),
                ("context", 26, "检索翻译上下文", "04_build_translation_context.py", ["--document", str(paths["structured"]), "--terms", str(paths["terms"]), "--output", str(paths["context"])]),
                ("translating", 30, "Qwen3-14B-Q4KM 约束翻译", "05_translate_document.py", ["--input", str(paths["context"]), "--output", str(paths["translations"])]),
                ("qa", 63, "规则质检", "06_quality_check.py", ["--translations", str(paths["translations"]), "--context", str(paths["context"]), "--output", str(paths["qa"])]),
                ("repairing", 70, "自动重译失败内容", "07_auto_repair.py", ["--translations", str(paths["translations"]), "--context", str(paths["context"]), "--qa", str(paths["qa"]), "--output", str(paths["translations"]), "--report", str(paths["qa"])]),
                ("gate", 79, "自动质检门禁", "08_approval_gate.py", ["--translations", str(paths["translations"]), "--qa", str(paths["qa"]), "--output", str(paths["approved"])]),
                ("rendering", 86, "重建中文和中英对照 PDF", "09_rebuild_pdf.py", ["--original", str(paths["input"]), "--structured", str(paths["structured"]), "--translations", str(paths["approved"]), "--output", str(paths["translated_pdf"]), "--bilingual-output", str(paths["bilingual_pdf"])]),
                ("summarizing", 94, "Gemma 生成文献总结", "10_generate_summary.py", ["--structured", str(paths["structured"]), "--translations", str(paths["approved"]), "--output", str(paths["summary_json"]), "--markdown", str(paths["summary_md"])]),
            ]
            try:
                for stage, progress, message, script, args in steps:
                    self._update(job_id, stage=stage, progress=progress, message=message)
                    self._append_log(job_id, f"\n[$ {message}]\n")
                    self._run_script(job_id, script, args)
                    if stage == "terminology":
                        self._write_glossary(paths["terms"], paths["glossary"])
                files = self._publish_outputs(job_id, work_dir, output_dir)
                self._update(job_id, status="completed", stage="completed", progress=100, message="处理完成", finished_at=time.strftime("%Y-%m-%d %H:%M:%S"), files=files, output_folder=output_dir.name)
            except Exception as error:
                self._append_log(job_id, traceback.format_exc())
                self._update(job_id, status="failed", stage="failed", message="处理失败", error=str(error), finished_at=time.strftime("%Y-%m-%d %H:%M:%S"))

MANAGER = JobManager()

def ollama_status() -> dict[str, Any]:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        base_url = config.get("base_url", "http://127.0.0.1:11434")
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
        return {"ready": True, "base_url": base_url, "models": [item.get("name") for item in data.get("models", [])]}
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return {"ready": False, "base_url": "http://127.0.0.1:11434", "models": []}

class WebHandler(BaseHTTPRequestHandler):
    server_version = "MedAgentWebUI/0.2"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, download_name: str | None = None) -> None:
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        if download_name:
            quoted = urllib.parse.quote(download_name)
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.end_headers()
        with path.open("rb") as file:
            shutil.copyfileobj(file, self.wfile)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            relative = urllib.parse.unquote(path.removeprefix("/static/"))
            target = (STATIC_DIR / relative).resolve()
            if target.is_relative_to(STATIC_DIR.resolve()) and target.is_file():
                self._send_file(target)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        if path == "/api/health":
            self._send_json({"status": "ok", "ollama": ollama_status()})
            return
        if path == "/api/cache":
            self._send_json(MANAGER.cache_stats())
            return
        if path == "/api/jobs":
            self._send_json({"jobs": MANAGER.list_jobs()})
            return

        parts = [part for part in path.split("/") if part]
        if len(parts) == 5 and parts[:2] == ["api", "jobs"] and parts[3] == "files":
            filename = urllib.parse.unquote(parts[4])
            try:
                job = MANAGER.snapshot(parts[2])
                output_dir = Path(job["output_dir"]).resolve()
                target = (output_dir / filename).resolve()
                if target.is_relative_to(output_dir) and target.is_file():
                    self._send_file(target, filename)
                    return
            except KeyError:
                pass
        if len(parts) == 5 and parts[:2] == ["api", "outputs"] and parts[3] == "files":
            folder = urllib.parse.unquote(parts[2])
            filename = urllib.parse.unquote(parts[4])
            output_root = OUTPUT_ROOT.resolve()
            output_dir = (output_root / folder).resolve()
            target = (output_dir / filename).resolve()
            if output_dir.is_relative_to(output_root) and target.is_relative_to(output_dir) and target.is_file():
                self._send_file(target, filename)
                return
        if len(parts) == 3 and parts[:2] == ["api", "jobs"]:
            try:
                self._send_json(MANAGER.snapshot(parts[2]))
                return
            except KeyError:
                pass
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/cache/clear":
            try:
                removed = MANAGER.clear_cache()
                self._send_json({"removed": removed})
            except RuntimeError as error:
                self._send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        if parsed.path != "/api/jobs":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_UPLOAD_BYTES:
            self._send_json({"error": "PDF 文件为空或超过 300MB 限制"}, HTTPStatus.BAD_REQUEST)
            return
        raw_filename = urllib.parse.unquote(self.headers.get("X-Filename", "document.pdf"))
        safe_name = Path(raw_filename).name
        if not safe_name.lower().endswith(".pdf"):
            self._send_json({"error": "只支持 PDF 文件"}, HTTPStatus.BAD_REQUEST)
            return
        content = self.rfile.read(length)
        if not content.startswith(b"%PDF"):
            self._send_json({"error": "上传内容不是有效 PDF"}, HTTPStatus.BAD_REQUEST)
            return
        job = MANAGER.create(safe_name, content)
        self._send_json({"job": job}, HTTPStatus.CREATED)

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the local medical agent WebUI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()

def main() -> None:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), WebHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Medical Agent WebUI: {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
