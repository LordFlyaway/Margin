"""Paths, user settings and the Claude API key."""
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
    "provider": "ollama",            # "claude" | "ollama"
    "claude_model": "claude-opus-5-5",
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

_lock = threading.Lock()
_KEYRING_SERVICE = "margin-study"
_KEYRING_USER = "anthropic_api_key"


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


def get_claude_key() -> str | None:
    """Saved key from Windows Credential Manager, if there is one."""
    try:
        import keyring
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
    except Exception:
        return None


def set_claude_key(key: str | None) -> None:
    import keyring
    if key:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, key)
    elif get_claude_key():
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USER)


def claude_configured() -> bool:
    return bool(get_claude_key() or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))
