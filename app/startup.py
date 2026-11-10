"""Optional: launch Margin to the tray when Windows starts."""
from __future__ import annotations

import os
from pathlib import Path

from . import config


def _script() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/Margin.vbs"


def enabled() -> bool:
    return _script().exists()


def set_enabled(on: bool) -> None:
    script = _script()
    if not on:
        script.unlink(missing_ok=True)
        return
    python = config.ROOT / ".venv/Scripts/pythonw.exe"
    entry = config.ROOT / "desktop.py"
    script.write_text(
        'Set s = CreateObject("WScript.Shell")\n'
        f's.CurrentDirectory = "{config.ROOT}"\n'
        f's.Run """{python}"" ""{entry}"" --tray", 0, False\n',
        "utf-8",
    )
