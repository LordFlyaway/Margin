"""Turn PowerPoint decks into PDFs, so slides open in the reader exactly as they look.

PowerPoint itself does the conversion (through COM), which keeps the layout, pictures
and fonts. Without PowerPoint the deck stays a .pptx and the reader shows its text.
"""
from __future__ import annotations

import threading
from pathlib import Path

SLIDES = {".pptx", ".ppt"}
_PDF_FORMAT = 32  # ppSaveAsPDF
_lock = threading.Lock()  # PowerPoint handles one conversion at a time


class ConvertError(Exception):
    pass


def available() -> bool:
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"PowerPoint.Application"))
        return True
    except (ImportError, OSError):
        return False


def to_pdf(source: Path) -> Path:
    """Save `source` as a PDF next to it and return the PDF's path."""
    if not available():
        raise ConvertError("PowerPoint isn't installed, so slides can't be turned into a PDF.")
    target = source.with_suffix(".pdf")
    with _lock:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        app = pres = None
        try:
            app = win32com.client.DispatchEx("PowerPoint.Application")
            # ReadOnly, Untitled=False, WithWindow=False: nothing appears on screen
            pres = app.Presentations.Open(str(source.resolve()), True, False, False)
            pres.SaveAs(str(target.resolve()), _PDF_FORMAT)
        except Exception as e:
            target.unlink(missing_ok=True)
            raise ConvertError(f"PowerPoint couldn't convert this file: {e}") from e
        finally:
            if pres is not None:
                try:
                    pres.Close()
                except Exception:
                    pass
            # Quit only our own instance: the user may have PowerPoint open with their work
            if app is not None:
                try:
                    if app.Presentations.Count == 0:
                        app.Quit()
                except Exception:
                    pass
            pythoncom.CoUninitialize()
    if not target.exists() or target.stat().st_size == 0:
        raise ConvertError("PowerPoint didn't produce a PDF.")
    return target
