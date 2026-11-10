"""In-process event bus. Worker threads publish; websocket clients and the desktop shell listen."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

_clients: set[asyncio.Queue] = set()
_listeners: list[Callable[[dict], None]] = []
_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()

pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="margin-job")
# Separate lanes, so a big upload can never hold up a question.
ingest_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="margin-ingest")
chat_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="margin-chat")


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def add_client() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue()
    with _lock:
        _clients.add(q)
    return q


def remove_client(q: asyncio.Queue) -> None:
    with _lock:
        _clients.discard(q)


def listen(fn: Callable[[dict], None]) -> None:
    _listeners.append(fn)


def publish(event: dict) -> None:
    for fn in list(_listeners):
        try:
            fn(event)
        except Exception:
            pass
    if _loop is None or _loop.is_closed():
        return
    with _lock:
        clients = list(_clients)
    for q in clients:
        _loop.call_soon_threadsafe(q.put_nowait, event)


def changed(what: str) -> None:
    """Tell open pages that a kind of data changed and should be refetched."""
    publish({"type": "changed", "what": what})


def toast(message: str, tone: str = "info") -> None:
    publish({"type": "toast", "message": message, "tone": tone})


def background(fn: Callable, *args, on_error: Callable[[Exception], None] | None = None) -> None:
    def wrapped():
        try:
            fn(*args)
        except Exception as e:  # surfaced to the user; a job must never die silently
            if on_error:
                on_error(e)
            toast(str(e) or e.__class__.__name__, "error")
    pool.submit(wrapped)
