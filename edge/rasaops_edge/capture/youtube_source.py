"""YouTube / HLS live stream capture via yt-dlp + OpenCV (lab demo only)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import numpy as np

from .frame import Frame

log = logging.getLogger(__name__)


def resolve_stream_url(page_url: str, *, max_height: int = 720) -> dict[str, Any]:
    """Resolve a YouTube (or yt-dlp) page URL to a direct media URL + metadata."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp required: pip install yt-dlp") from exc

    # Prefer progressive MP4 / HLS that OpenCV+FFmpeg can open; avoid pure audio.
    fmt = (
        f"best[height<={max_height}][ext=mp4][protocol^=http]/"
        f"best[height<={max_height}][ext=mp4]/"
        f"best[height<={max_height}]/"
        f"best[ext=mp4]/best"
    )
    base_opts = {
        "quiet": True,
        "noplaylist": True,
        "no_warnings": True,
        "format": fmt,
    }
    # Bypass common bot checks: try android/ios clients first, then cookies
    cookie_browser = os.environ.get("RASAOPS_YT_COOKIES_FROM_BROWSER", "").strip()
    cookie_file = os.environ.get("RASAOPS_YT_COOKIES_FILE", "").strip()
    attempts: list[dict] = [
        {
            **base_opts,
            "extractor_args": {"youtube": {"player_client": ["android", "ios"]}},
        },
        {
            **base_opts,
            "extractor_args": {"youtube": {"player_client": ["tv_embedded", "mweb"]}},
        },
        base_opts,
    ]
    if cookie_file and os.path.isfile(cookie_file):
        attempts.insert(0, {**base_opts, "cookiefile": cookie_file})
    if cookie_browser and cookie_browser.lower() not in ("0", "none", "false"):
        attempts.append({**base_opts, "cookiesfrombrowser": (cookie_browser,)})

    info = None
    last_err: Exception | None = None
    for opts in attempts:
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(page_url, download=False)
            break
        except Exception as exc:
            last_err = exc
            log.warning("yt-dlp resolve failed (%s); trying next strategy", exc)
    if info is None:
        raise RuntimeError(
            f"Could not resolve stream URL for {page_url}: {last_err}"
        ) from last_err
    url = info.get("url")
    if not url:
        # Prefer formats with both video + a direct URL (skip dash video-only if possible)
        candidates = [
            f
            for f in (info.get("formats") or [])
            if f.get("url") and f.get("vcodec") not in (None, "none")
        ]
        # Prefer mp4 progressive (acodec present) then any with height limit
        def _score(f: dict) -> tuple:
            h = f.get("height") or 0
            prog = 1 if f.get("acodec") not in (None, "none") else 0
            mp4 = 1 if (f.get("ext") == "mp4" or "mp4" in str(f.get("container") or "")) else 0
            under = 1 if h and h <= max_height else 0
            return (prog, mp4, under, -abs((h or max_height) - max_height))

        candidates.sort(key=_score, reverse=True)
        if candidates:
            url = candidates[0]["url"]
    if not url:
        raise RuntimeError(f"Could not resolve stream URL for {page_url}")
    return {
        "url": url,
        "title": info.get("title") or page_url,
        "is_live": bool(info.get("is_live")),
        "id": info.get("id"),
        "description": (info.get("description") or "")[:500],
    }


@dataclass
class YoutubeSource:
    """
    Continuously grab frames from a YouTube live (or VOD) URL.

    Re-resolves the stream URL periodically (HLS tokens expire).
    """

    page_url: str
    target_fps: float = 1.0
    max_height: int = 720
    url_refresh_sec: float = 600.0  # re-resolve every 10 min
    path_hint: str = "youtube"
    # When True, drain OpenCV/FFmpeg buffer so we show the newest frame (less lag)
    low_latency: bool = False
    buffer_drain: int = 4  # extra grabs per tick when low_latency

    _cap: Any = field(default=None, init=False, repr=False)
    _stream_url: str = field(default="", init=False)
    _meta: dict = field(default_factory=dict, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _fail_streak: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.target_fps = max(0.2, float(self.target_fps))

    @property
    def title(self) -> str:
        return str(self._meta.get("title") or self.page_url)

    def open(self) -> None:
        import cv2

        self.close()
        meta = resolve_stream_url(self.page_url, max_height=self.max_height)
        self._meta = meta
        self._stream_url = meta["url"]
        # OpenCV uses FFmpeg backend for HTTP/HLS. Do NOT fall back to default
        # (can pick CAP_IMAGES and choke on long googlevideo URLs).
        cap = cv2.VideoCapture(self._stream_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            # One more try with explicit ffmpeg env hint + default open
            os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
            cap = cv2.VideoCapture(self._stream_url, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            raise RuntimeError(
                f"OpenCV/FFmpeg could not open stream for {self.page_url}. "
                "Ensure opencv-python includes FFmpeg, or try a lower --max-height."
            )
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except Exception:
            pass
        # Warm a few frames
        ok = False
        for _ in range(15):
            ret, mat = cap.read()
            if ret and mat is not None and mat.size > 0:
                ok = True
                break
            time.sleep(0.1)
        if not ok:
            cap.release()
            raise RuntimeError("Stream opened but no frames (may be offline or geo-blocked)")
        self._cap = cap
        self._opened_at = time.time()
        self._fail_streak = 0
        log.info("YouTube source open: %s live=%s", meta.get("title"), meta.get("is_live"))

    def _maybe_refresh(self) -> None:
        if time.time() - self._opened_at > self.url_refresh_sec:
            log.info("Refreshing YouTube stream URL…")
            self.open()

    def read(self) -> Optional[Frame]:
        if self._cap is None:
            self.open()
        assert self._cap is not None
        try:
            self._maybe_refresh()
        except Exception as exc:
            log.warning("URL refresh failed: %s", exc)

        ok, mat = self._cap.read()
        if not ok or mat is None or getattr(mat, "size", 0) == 0:
            self._fail_streak += 1
            if self._fail_streak >= 8:
                try:
                    self.open()
                except Exception as exc:
                    log.warning("Reopen failed: %s", exc)
                    return None
            return None

        # Drop buffered frames so live preview stays near realtime
        if self.low_latency:
            drain = max(0, int(self.buffer_drain))
            for _ in range(drain):
                ok2, mat2 = self._cap.read()
                if not ok2 or mat2 is None or getattr(mat2, "size", 0) == 0:
                    break
                mat = mat2

        self._fail_streak = 0
        h, w = mat.shape[:2]
        return Frame(
            timestamp_ms=int(time.time() * 1000),
            width=int(w),
            height=int(h),
            path_hint=f"youtube:{self._meta.get('id') or self.path_hint}",
            bgr=mat,
        )

    def frames(self) -> Iterator[Frame]:
        if self._cap is None:
            self.open()
        interval = 1.0 / self.target_fps
        while True:
            t0 = time.perf_counter()
            frame = self.read()
            if frame is None:
                time.sleep(0.05 if self.low_latency else 0.2)
                continue
            yield frame
            elapsed = time.perf_counter() - t0
            sleep_for = interval - elapsed
            # Never sleep extra when we're already behind (keeps lag from stacking)
            if sleep_for > 0.002:
                time.sleep(sleep_for)

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
