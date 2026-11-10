"""One interface over Claude and local Ollama. Everything else calls these functions."""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from typing import Iterator

import httpx

from . import config

OLLAMA = "http://127.0.0.1:11434"
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Roughly how much source text fits in one request (characters).
_CLAUDE_SECTION = 600_000
_OLLAMA_SECTION = 12_000
_OCR_MAX_TOKENS = 1500  # vision models can loop forever on a busy slide


class LLMError(Exception):
    """Failure with a message written for the person using the app."""


def provider() -> str:
    return config.load_settings()["provider"]


def section_limit() -> int:
    return _CLAUDE_SECTION if provider() == "claude" else _OLLAMA_SECTION


def _user_message(prompt: str, images: list[str] | None) -> dict:
    return {"role": "user", "content": prompt, "images": images or []}


_LIGHT_GPU_LAYERS = 18  # of about 29 in a 7B model


def placement(role: str) -> dict:
    """Ollama options saying how much of a job runs on the GPU.

    Background jobs ("reader", "search") go to the CPU so the GPU is only busy while thinking.
    """
    mode = config.load_settings()["gpu_mode"]
    if mode == "full":
        return {}
    # Roughly the physical cores, minus some so the laptop stays usable.
    options: dict = {"num_thread": max(2, (os.cpu_count() or 4) // 2 - 1)}
    if role in ("reader", "search"):
        options["num_gpu"] = 0
    elif mode == "light":
        options["num_gpu"] = _LIGHT_GPU_LAYERS
    return options


_cv = threading.Condition()
_questions = 0


@contextmanager
def question():
    """Mark a student's question in progress; document reading pauses until it's answered."""
    global _questions
    with _cv:
        _questions += 1
    try:
        yield
    finally:
        with _cv:
            _questions -= 1
            _cv.notify_all()


def yield_to_questions() -> None:
    with _cv:
        _cv.wait_for(lambda: _questions == 0, timeout=300)


def stream(messages: list[dict], system: str, effort: str = "medium", schema: dict | None = None,
           read_timeout: float = 600) -> Iterator[str]:
    """Yield text as it is generated. messages: [{role, content, images?}]"""
    if provider() == "claude":
        yield from _claude_stream(messages, system, effort, schema)
    else:
        yield from _ollama_stream(messages, system, schema, read_timeout=read_timeout)


def read_image(prompt: str, system: str, image: str) -> str:
    """The document model: read a scanned page. Separate from the model that thinks."""
    s = config.load_settings()
    if s["ocr_provider"] == "claude":
        if not config.claude_configured():
            raise LLMError("Add your Claude API key in Settings, or read scanned pages locally.")
        message = _user_message(prompt, [image])
        return "".join(_claude_stream([message], system, "low", None, model=s["claude_ocr_model"])).strip()
    installed = ollama_status()["models"]
    model = s["ollama_ocr_model"]
    if model not in installed:
        model = s["ollama_model"]  # dedicated reader not downloaded; use the thinking model
    if "glm-ocr" in model:  # this model takes a fixed instruction and no system prompt
        prompt, system = "Text Recognition:", None
    message = _user_message(prompt, [image])
    return "".join(_ollama_stream([message], system, None, model=model, keep_alive="5m",
                                  read_timeout=300, max_tokens=_OCR_MAX_TOKENS, role="reader")).strip()


def complete(prompt: str, system: str, effort: str = "high", images: list[str] | None = None) -> str:
    return "".join(stream([_user_message(prompt, images)], system, effort)).strip()


def complete_json(prompt: str, system: str, schema: dict, effort: str = "high") -> dict:
    text = "".join(stream([_user_message(prompt, None)], system, effort, schema))
    try:
        return json.loads(text)
    except ValueError as e:
        raise LLMError("The AI returned an unreadable answer. Try again.") from e


# ---- Claude -------------------------------------------------------------

def _claude_client():
    import anthropic
    key = config.get_claude_key()
    try:
        return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    except Exception as e:
        raise LLMError("Add your Claude API key in Settings, or switch to Local.") from e


def _claude_content(m: dict):
    if not m.get("images"):
        return m["content"]
    blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}}
              for b in m["images"]]
    return blocks + [{"type": "text", "text": m["content"]}]


def _claude_stream(messages, system, effort, schema, model: str | None = None) -> Iterator[str]:
    import anthropic
    model = model or config.load_settings()["claude_model"]
    output_config: dict = {"effort": effort}
    if schema:
        output_config["format"] = {"type": "json_schema", "schema": schema}
    kwargs: dict = {}
    if model in _FALLBACK_MODELS:
        kwargs = {"betas": [_FALLBACK_BETA], "fallbacks": "default"}
    try:
        with _claude_client().beta.messages.stream(
            model=model,
            max_tokens=64000,
            system=system,
            messages=[{"role": m["role"], "content": _claude_content(m)} for m in messages],
            output_config=output_config,
            **kwargs,
        ) as s:
            yield from s.text_stream
            final = s.get_final_message()
    except anthropic.AuthenticationError as e:
        raise LLMError("Claude rejected the API key. Check it in Settings.") from e
    except anthropic.PermissionDeniedError as e:
        raise LLMError("This Claude API key can't use the selected model.") from e
    except anthropic.NotFoundError as e:
        raise LLMError(f"Claude doesn't recognise the model {model}. Pick another in Settings.") from e
    except anthropic.RateLimitError as e:
        raise LLMError("Claude's rate limit was reached. Wait a minute and try again.") from e
    except anthropic.BadRequestError as e:
        if "credit" in str(e).lower():
            raise LLMError("Your Claude API account is out of credit. Top up, or switch to Local in Settings.") from e
        raise LLMError(f"Claude couldn't process this request: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError("Can't reach Claude. Check your internet, or switch to Local in Settings.") from e
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude had a server problem ({e.status_code}). Try again shortly.") from e
    if final.stop_reason == "refusal":
        raise LLMError("Claude declined to answer this request.")
    if final.stop_reason == "max_tokens":
        raise LLMError("The answer was too long and got cut off. Try a smaller document.")


# ---- Ollama -------------------------------------------------------------

def _ollama_stream(messages, system, schema, model: str | None = None, keep_alive: str = "30m",
                   read_timeout: float = 600, max_tokens: int | None = None,
                   role: str = "thinking") -> Iterator[str]:
    model = model or config.load_settings()["ollama_model"]
    wire = [{"role": "system", "content": system}] if system else []
    for m in messages:
        item = {"role": m["role"], "content": m["content"]}
        if m.get("images"):
            item["images"] = m["images"]
        wire.append(item)
    body: dict = {"model": model, "messages": wire, "stream": True, "keep_alive": keep_alive,
                  "options": {"num_ctx": 8192, "temperature": 0.3, **placement(role)}}
    if max_tokens:
        body["options"]["num_predict"] = max_tokens
    if schema:
        body["format"] = schema
    try:
        with httpx.stream("POST", f"{OLLAMA}/api/chat", json=body, timeout=httpx.Timeout(read_timeout, connect=5)) as r:
            if r.status_code == 404:
                raise LLMError(f"The local model {model} isn't installed. Pick another in Settings.")
            if r.status_code != 200:
                r.read()
                raise LLMError(f"The local model failed: {r.text[:200]}")
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("error"):
                    raise LLMError(f"The local model failed: {data['error']}")
                piece = data.get("message", {}).get("content")
                if piece:
                    yield piece
    except httpx.ConnectError as e:
        raise LLMError("Ollama isn't running. Open Ollama, or switch to Claude in Settings.") from e
    except httpx.TimeoutException as e:
        raise LLMError("The local model took too long. Try again or use a smaller document.") from e


def ollama_status() -> dict:
    try:
        r = httpx.get(f"{OLLAMA}/api/tags", timeout=2)
        r.raise_for_status()
        return {"running": True, "models": sorted(m["name"] for m in r.json().get("models", []))}
    except (httpx.HTTPError, ValueError):
        return {"running": False, "models": []}


def embed(texts: list[str]) -> list[list[float]] | None:
    """Local embeddings for meaning-based search. None when unavailable: keyword search still works."""
    model = config.load_settings()["ollama_embed_model"]
    try:
        r = httpx.post(f"{OLLAMA}/api/embed", json={"model": model, "input": texts, "keep_alive": "30m",
                                                    "options": placement("search")},
                       timeout=httpx.Timeout(120, connect=3))
        r.raise_for_status()
        return r.json().get("embeddings") or None
    except (httpx.HTTPError, ValueError):
        return None
