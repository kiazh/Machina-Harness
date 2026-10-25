"""Windows now-playing bridge using the System Media Transport Controls API.

The WinRT projections are imported lazily and only on Windows. The API exposes
the same read/transport surface as the macOS detector; every failure is
reported as an unavailable reading rather than fabricated metadata.
"""

from __future__ import annotations

import asyncio
from typing import Any


def _empty(error: str | None = None) -> dict[str, Any]:
    return {
        "available": error is None,
        "state": None,
        "title": None,
        "artist": None,
        "album": None,
        "source": None,
        "elapsed": None,
        "duration": None,
        "error": error,
    }


def _display_source(app_id: str | None) -> str | None:
    value = (app_id or "").strip()
    if not value:
        return None
    # Packaged app ids are long; the executable-looking tail is the useful bit.
    for separator in ("\\", "/"):
        if separator in value:
            value = value.rsplit(separator, 1)[-1]
    if value.lower().endswith(".exe"):
        value = value[:-4]
    return value or None


def _seconds(value: Any) -> float | None:
    if value is None:
        return None
    try:
        total = value.total_seconds()
        return max(0.0, float(total))
    except (AttributeError, TypeError, ValueError):
        return None


def _read_sync() -> dict[str, Any]:
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        )
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as PlaybackStatus,
        )
    except Exception:
        return _empty(
            "Windows media support is unavailable; reinstall Machina so the "
            "WinRT media packages are installed"
        )

    async def read() -> dict[str, Any]:
        manager = await SessionManager.request_async()
        session = manager.get_current_session()
        if session is None:
            return _empty(None)
        try:
            properties = await session.try_get_media_properties_async()
        except Exception as error:
            return _empty(f"Windows media properties unavailable ({type(error).__name__})")
        if properties is None or not str(getattr(properties, "title", "") or "").strip():
            return _empty(None)
        info = session.get_playback_info()
        status = getattr(info, "playback_status", None)
        if status == PlaybackStatus.PLAYING:
            state = "playing"
        elif status == PlaybackStatus.PAUSED:
            state = "paused"
        else:
            state = "unknown"
        timeline = session.get_timeline_properties()
        position = _seconds(getattr(timeline, "position", None))
        end = _seconds(getattr(timeline, "end_time", None))
        start = _seconds(getattr(timeline, "start_time", None)) or 0.0
        duration = max(0.0, end - start) if end is not None else None
        return {
            "available": True,
            "state": state,
            "title": str(properties.title),
            "artist": str(getattr(properties, "artist", "") or "") or None,
            "album": str(getattr(properties, "album_title", "") or "") or None,
            "source": _display_source(getattr(session, "source_app_user_model_id", None)),
            "elapsed": position,
            "duration": duration,
            "error": None,
        }

    try:
        return asyncio.run(asyncio.wait_for(read(), timeout=3.0))
    except Exception as error:
        return _empty(f"Windows media query failed ({type(error).__name__})")


def detect_now_playing() -> dict[str, Any]:
    return _read_sync()


def send_now_playing_command(action: str) -> dict[str, Any]:
    try:
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as SessionManager,
        )
    except Exception:
        return {"ok": False, "source": None, "error": "Windows media support is unavailable"}

    async def command() -> bool:
        manager = await SessionManager.request_async()
        session = manager.get_current_session()
        if session is None:
            return False
        if action == "toggle":
            return bool(await session.try_toggle_play_pause_async())
        if action == "next":
            return bool(await session.try_skip_next_async())
        if action == "previous":
            return bool(await session.try_skip_previous_async())
        return False

    if action not in {"toggle", "next", "previous"}:
        return {"ok": False, "source": None, "error": f"unknown action {action!r}"}
    try:
        accepted = asyncio.run(asyncio.wait_for(command(), timeout=3.0))
    except Exception as error:
        return {
            "ok": False,
            "source": None,
            "error": f"Windows media command failed ({type(error).__name__})",
        }
    return {
        "ok": accepted,
        "source": "Windows media session" if accepted else None,
        "error": None if accepted else "nothing is playing or the source rejected the command",
    }
