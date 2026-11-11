"""Paths, user settings and the cloud API keys."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MARGIN_DATA", ROOT / "data"))
UPLOADS = DATA / "uploads"
DB_PATH = DATA / "margin.db"
SETTINGS_PATH = DATA / "settings.json"
MODELS = ROOT / "models"
HOST, PORT = "127.0.0.1", int(os.environ.get("MARGIN_PORT", 8765))

DEFAULTS = {
    "provider": "ollama",            # "claude" | "openai" | "ollama"
    "claude_model": "claude-opus-5-5",
    "openai_model": "gpt-5-mini",
    "ollama_model": "qwen2.5:7b",    # thinking: notes, answers, exams. Fits 8 GB VRAM alongside the reader
    "ollama_embed_model": "nomic-embed-text",
    "gpu_mode": "balanced",          # "light" | "balanced" | "full"
    "ocr_provider": "ollama",        # who reads scanned pages: "ollama" | "claude"
    "ollama_ocr_model": "glm-ocr:q8_0",
    "claude_ocr_model": "claude-haiku-5-5",
    "focus_course_id": None,         # course the assistant searches; None = all
    "wake_enabled": True,
    "wake_threshold": 0.5,
    "speak_answers": True,
}

CLAUDE_MODELS = [
    {"id": "claude-opus-5-5", "label": "Opus 5.5 (best, $4 / $20 per million tokens)"},
    {"id": "claude-sonnet-5-5", "label": "Sonnet 5.5 (balanced, $2 / $10)"},
    {"id": "claude-haiku-5-5", "label": "Haiku 5.5 (cheapest, $0.10 / $0.50)"},
]

# Suggestions only: any chat model the key can use works, and Settings lists the account's own models.
OPENAI_MODELS = ["gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4.1", "gpt-4o"]

_lock = threading.Lock()
_KEYRING_SERVICE = "margin-study"
_KEYRING_USERS = {"claude": "anthropic_api_key", "openai": "openai_api_key"}
_ENV = {"claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"), "openai": ("OPENAI_API_KEY",)}


def ensure_dirs() -> None:
    UPLOADS.mkdir(parents=True, exist_ok=True)


_cache: dict | None = None  # the voice loop reads settings on every audio frame


def load_settings() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            saved = {}
            if SETTINGS_PATH.exists():
                try:
                    saved = json.loads(SETTINGS_PATH.read_text("utf-8"))
                except ValueError:
                    saved = {}
            _cache = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
        return dict(_cache)


def save_settings(patch: dict) -> dict:
    global _cache
    merged = {**load_settings(), **{k: v for k, v in patch.items() if k in DEFAULTS}}
    ensure_dirs()
    with _lock:
        SETTINGS_PATH.write_text(json.dumps(merged, indent=2), "utf-8")
        _cache = merged
    return dict(merged)


def get_key(provider: str) -> str | None:
    """Saved key from Windows Credential Manager, if there is one."""
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERS[provider])
    except Exception:
        return None


def set_key(provider: str, key: str | None) -> None:
    import keyring
    if key:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERS[provider], key)
    elif get_key(provider):
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USERS[provider])


def configured(provider: str) -> bool:
    if provider == "ollama":
        return True
    return bool(get_key(provider) or any(os.environ.get(v) for v in _ENV[provider]))


def get_claude_key() -> str | None:
    return get_key("claude")


def set_claude_key(key: str | None) -> None:
    set_key("claude", key)


def claude_configured() -> bool:
    return configured("claude")


def openai_key() -> str | None:
    return get_key("openai") or os.environ.get("OPENAI_API_KEY")
