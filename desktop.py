"""Margin desktop shell: main window and tray icon."""
from __future__ import annotations

import os
import socket
import sys
import threading
import time

import httpx
import pystray
import uvicorn
import webview
from PIL import Image, ImageDraw

from app import config, events
from app.server import app
from app.voice.engine import engine as voice

URL = f"http://{config.HOST}:{config.PORT}"


def port_taken() -> bool:
    with socket.socket() as s:
        return s.connect_ex((config.HOST, config.PORT)) == 0


def start_server() -> None:
    server = uvicorn.Server(uvicorn.Config(app, host=config.HOST, port=config.PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if port_taken():
            return
        time.sleep(0.1)
    raise RuntimeError("The local server didn't start.")


def tray_image() -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 4, 58, 60), 6, fill=(251, 252, 254, 255))
    d.line((18, 4, 18, 60), fill=(216, 67, 59, 255), width=4)
    d.rectangle((26, 22, 52, 34), fill=(255, 233, 74, 255))
    d.line((26, 44, 52, 44), fill=(43, 63, 214, 255), width=4)
    return img


class Shell:
    def __init__(self, start_hidden: bool):
        self.quitting = False
        self.main = webview.create_window("Margin", URL, width=1380, height=880, min_size=(980, 640), hidden=start_hidden)
        self.main.events.closing += self.on_close
        self.tray = pystray.Icon("margin", tray_image(), "Margin", pystray.Menu(
            pystray.MenuItem("Open Margin", self.show_main, default=True),
            pystray.MenuItem("Mute microphone", self.toggle_mute, checked=lambda _i: voice.muted),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Margin", self.quit),
        ))
        events.listen(self.on_event)

    def on_close(self):
        if self.quitting:
            return True
        # Runs on the window's own thread, so hiding has to happen after this returns.
        threading.Thread(target=self.main.hide, daemon=True).start()  # keep listening from the tray
        return False

    def show_main(self, *_):
        self.main.show()
        self.main.restore()

    def toggle_mute(self, *_):
        voice.set_muted(not voice.muted)
        self.tray.update_menu()

    def quit(self, *_):
        self.quitting = True
        voice.stop()
        self.tray.stop()
        self.main.destroy()

    def on_event(self, event: dict):
        # "hey Jarvis" shows its state in the assistant panel and answers aloud: no extra window.
        if event.get("type") == "window.show":
            self.show_main()

    def started(self):
        self.tray.run_detached()
        voice.start()


def main() -> None:
    if port_taken():  # already running, maybe hidden in the tray: bring that window back
        httpx.post(f"{URL}/api/window/show", timeout=5)
        return
    config.ensure_dirs()
    start_server()
    shell = Shell(start_hidden="--tray" in sys.argv)
    webview.start(shell.started, storage_path=str(config.DATA / "webview"), private_mode=False)
    os._exit(0)  # background reading resumes next launch; don't linger without a window


if __name__ == "__main__":
    main()
