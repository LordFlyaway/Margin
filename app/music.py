"""Spotify: a saved list of links, plus now-playing and controls for the Spotify desktop app.

Needs no Spotify login or developer key. Links are looked up through Spotify's public oEmbed page,
and the desktop app is controlled through the same Windows media controls as the keyboard's media keys.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from datetime import datetime, timezone

import httpx

from concurrent.futures import ThreadPoolExecutor


def _init_media_thread() -> None:
    # Windows media and mixer calls share one thread, set up for multithreaded COM.
    # Mixing apartment types on server threads made cover-art reads hang.
    import sys
    sys.coinit_flags = 0  # COINIT_MULTITHREADED; comtypes reads this when first imported
    import comtypes
    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass  # this thread is already set up


_media = ThreadPoolExecutor(max_workers=1, thread_name_prefix="margin-media", initializer=_init_media_thread)


def _on_media_thread(fn):
    return _media.submit(fn).result(timeout=10)

_LINK = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]+/)?|spotify:)(track|album|playlist|artist|episode|show)[/:]([A-Za-z0-9]{10,40})")
_THUMBNAIL = re.compile(r"^https://[a-z0-9.-]*(?:scdn\.co|spotifycdn\.com)/")


def parse_link(text: str) -> tuple[str, str] | None:
    m = _LINK.search(text or "")
    return (m.group(1), m.group(2)) if m else None


def lookup(kind: str, spotify_id: str) -> dict:
    """Title and cover art. A failed lookup still saves the link."""
    fallback = {"title": f"Spotify {kind}", "thumbnail": None}
    try:
        r = httpx.get("https://open.spotify.com/oembed", params={"url": f"https://open.spotify.com/{kind}/{spotify_id}"},
                      timeout=8, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return fallback
    thumb = data.get("thumbnail_url") or ""
    return {"title": data.get("title") or fallback["title"], "thumbnail": thumb if _THUMBNAIL.match(thumb) else None}


async def _session():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    manager = await Manager.request_async()
    for s in manager.get_sessions():
        if "spotify" in (s.source_app_user_model_id or "").lower():
            return s
    return None


def position_now(position: float, duration: float, updated: datetime, playing: bool, now: datetime) -> float:
    """Spotify only reports its position on play, pause and seek, so count forward from then."""
    if playing:
        position += max(0.0, (now - updated).total_seconds())
    return min(max(0.0, position), duration) if duration else max(0.0, position)


async def _now() -> dict:
    s = await _session()
    if s is None:
        return {"available": False}
    props = await s.try_get_media_properties_async()
    # 4 is PLAYING in GlobalSystemMediaTransportControlsSessionPlaybackStatus
    playing = int(s.get_playback_info().playback_status) == 4
    tl = s.get_timeline_properties()
    duration = tl.end_time.total_seconds()
    position = position_now(tl.position.total_seconds(), duration, tl.last_updated_time, playing,
                            datetime.now(timezone.utc))
    title, artist = props.title or "", props.artist or ""
    return {"available": True, "playing": playing, "title": title, "artist": artist,
            "album": props.album_title or "", "position": position, "duration": duration,
            "art": hashlib.sha1(f"{title}\n{artist}\n{props.album_title}".encode()).hexdigest()[:12]
            if props.thumbnail else None}


async def _art() -> tuple[bytes, str] | None:
    from winrt.windows.storage.streams import DataReader
    s = await _session()
    if s is None:
        return None
    props = await s.try_get_media_properties_async()
    if not props.thumbnail:
        return None
    stream = await props.thumbnail.open_read_async()
    reader = DataReader(stream.get_input_stream_at(0))
    await reader.load_async(stream.size)
    return bytes(reader.read_buffer(stream.size)), stream.content_type or "image/png"


_art_cache: dict = {}


def album_art(key: str | None = None) -> tuple[bytes, str] | None:
    """Cover of the current song. Read once per song, then served from memory."""
    if key and key in _art_cache:
        return _art_cache[key]
    try:
        art = _on_media_thread(lambda: asyncio.run(asyncio.wait_for(_art(), 5)))
    except Exception:
        return None
    if art and key:
        _art_cache.clear()  # only the current song's cover is worth keeping
        _art_cache[key] = art
    return art


async def _control(action: str) -> bool:
    s = await _session()
    if s is None:
        return False
    call = {"toggle": s.try_toggle_play_pause_async, "play": s.try_play_async, "pause": s.try_pause_async,
            "next": s.try_skip_next_async, "previous": s.try_skip_previous_async}[action]
    return bool(await call())


def _spotify_volumes() -> list:
    """Spotify's channels in the Windows volume mixer (it can have several). Call on the media thread."""
    from pycaw.pycaw import AudioUtilities
    found = []
    for s in AudioUtilities.GetAllSessions():
        try:  # sessions from closed apps linger and raise when asked their name
            if s.Process and s.Process.name().lower() == "spotify.exe":
                found.append(s.SimpleAudioVolume)
        except Exception:
            continue
    return found


def _get_volume() -> int | None:
    volumes = _spotify_volumes()
    return round(max(v.GetMasterVolume() for v in volumes) * 100) if volumes else None


def get_volume() -> int | None:
    try:
        return _on_media_thread(_get_volume)
    except Exception:
        return None


def set_volume(percent: int) -> int | None:
    """Set Spotify's level in the Windows mixer. Returns the new level, or None if Spotify has no audio channel."""
    level = min(100, max(0, int(percent)))

    def apply():
        volumes = _spotify_volumes()
        for v in volumes:
            v.SetMasterVolume(level / 100, None)
            if level and v.GetMute():
                v.SetMute(0, None)
        return level if volumes else None

    return _on_media_thread(apply)


def now_playing() -> dict:
    def read():
        now = asyncio.run(asyncio.wait_for(_now(), 5))
        if now["available"]:
            now["volume"] = _get_volume()
        return now

    try:
        return _on_media_thread(read)
    except Exception:
        return {"available": False}


def control(action: str) -> bool:
    return _on_media_thread(lambda: asyncio.run(asyncio.wait_for(_control(action), 5)))
