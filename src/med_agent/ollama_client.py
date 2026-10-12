"""Small Ollama HTTP client with JSON-output support."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any


class OllamaError(RuntimeError):
    """Raised when Ollama cannot return a valid response."""


MANAGED_MODELS = {
    "deepseek-r1:1.5b",
    "Qwen3-14B-Q4KM:latest",
    "Gemma-4-E4B:latest",
}
_MODEL_SWITCH_LOCK = threading.Lock()
_ACTIVE_MODEL_KEY: tuple[str, str] | None = None


def _post_json(url: str, payload: dict[str, Any], timeout: int = 15) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def _unload_other_models(base_url: str, target_model: str) -> None:
    """Keep one managed model resident so an 8GB GPU does not run out of memory."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/ps", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return

    for item in data.get("models", []):
        name = item.get("name") or item.get("model")
        if name in MANAGED_MODELS and name != target_model:
            try:
                _post_json(
                    f"{base_url.rstrip('/')}/api/generate",
                    {"model": name, "keep_alive": 0},
                )
            except (OSError, urllib.error.URLError):
                continue


def _ensure_active_model(base_url: str, target_model: str) -> None:
    global _ACTIVE_MODEL_KEY
    desired = (base_url.rstrip("/"), target_model)
    if _ACTIVE_MODEL_KEY == desired:
        return
    with _MODEL_SWITCH_LOCK:
        if _ACTIVE_MODEL_KEY == desired:
            return
        _unload_other_models(base_url, target_model)
        _ACTIVE_MODEL_KEY = desired


def _extract_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass

    raise OllamaError(f"Model did not return a valid JSON object: {text[:500]}")


def chat_json(
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    base_url: str = "http://127.0.0.1:11434",
    timeout: int = 600,
    num_ctx: int = 8192,
    num_predict: int = 2048,
    temperature: float = 0.0,
    think: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _ensure_active_model(base_url, model)

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    if think is not None:
        payload["think"] = think

    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise OllamaError(f"Ollama HTTP {error.code}: {error_body}") from error
    except urllib.error.URLError as error:
        raise OllamaError(f"Cannot connect to Ollama at {base_url}: {error}") from error

    try:
        response_data = json.loads(response_body)
    except json.JSONDecodeError as error:
        raise OllamaError(f"Ollama returned invalid response: {response_body[:500]}") from error

    content = response_data.get("message", {}).get("content", "")
    parsed = _extract_json(content)
    metadata = {
        "model": response_data.get("model", model),
        "done": response_data.get("done"),
        "total_duration": response_data.get("total_duration"),
        "load_duration": response_data.get("load_duration"),
        "prompt_eval_count": response_data.get("prompt_eval_count"),
        "eval_count": response_data.get("eval_count"),
    }
    return parsed, metadata
