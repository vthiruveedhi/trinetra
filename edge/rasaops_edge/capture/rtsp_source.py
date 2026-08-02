"""RTSP camera capture for IP cameras (e.g., garage camera via RTSP URL)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Iterator, Optional

import numpy as np

from .frame import Frame

log = logging.getLogger(__name__)


@dataclass
class RTSPSource:
    """
    OpenCV ``VideoCapture`` wrapper for RTSP URLs.

    Example:
        rtsp://user:pass@192.168.68.55:554/live/ch0

    Env:
      RASAOPS_RTSP_URL — RTSP stream URL
      RASAOPS_CAPTURE_FPS — target emit rate (default 2.0)
      RASAOPS_RTSP_TRANSPORT — tcp | udp (default tcp, more reliable over WiFi)
    """

    url: str
    target_fps: float = 2.0
    transport: str = "tcp"  # tcp or udp
    _cap: object = field(default=None, init=False, repr=False)
    _fail_streak: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.target_fps = max(0.1, float(self.target_fps))
        self._cap = None
        self._fail_streak = 0

    @classmethod
    def from_env(cls) -> "RTSPSource":
        """Create from environment variables."""
        url = os.environ.get("RASAOPS_RTSP_URL")
        if not url:
            raise ValueError(
                "RASAOPS_RTSP_URL not set. "
                "E.g.: rtsp://user:pass@192.168.68.55:554/live/ch0"
            )
        fps = float(os.environ.get("RASAOPS_CAPTURE_FPS", "2.0"))
        transport = os.environ.get("RASAOPS_RTSP_TRANSPORT", "tcp").lower()
        return cls(url=url, target_fps=fps, transport=transport)

    def open(self) -> None:
        """Open the RTSP stream."""
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("opencv-python required for RTSP") from exc

        self.close()
        try:
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if cap is None or not cap.isOpened():
                raise RuntimeError(f"VideoCapture.isOpened() returned False for {self.url}")

            # Set RTSP transport preference
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                f"rtsp_transport;{self.transport}"
            )

            # Test that we can actually grab a frame
            ok = False
            for attempt in range(5):
                ret, mat = cap.read()
                if ret and mat is not None and getattr(mat, "size", 0) > 0:
                    ok = True
                    log.info(
                        "RTSP stream opened: %dx%d @ attempt %d",
                        mat.shape[1],
                        mat.shape[0],
                        attempt,
                    )
                    break
                time.sleep(0.2)

            if not ok:
                cap.release()
                raise RuntimeError(
                    f"Could not grab frames from {self.url}. "
                    "Check: URL format, credentials, camera is reachable, "
                    "RTSP port (554) is open, transport (tcp/udp)."
                )

            self._cap = cap
            self._fail_streak = 0
        except Exception as e:
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            raise RuntimeError(f"Failed to open RTSP stream {self.url}: {e}") from e

    def read(self) -> Optional[Frame]:
        """Grab one frame or None if grab fails (will auto-reopen after streaks)."""
        if self._cap is None:
            self.open()
        assert self._cap is not None

        ok, mat = self._cap.read()
        if not ok or mat is None or getattr(mat, "size", 0) == 0:
            self._fail_streak += 1
            if self._fail_streak >= 5:
                log.warning(
                    "RTSP grab failed %d times — reconnecting",
                    self._fail_streak,
                )
                try:
                    self.open()
                except RuntimeError:
                    return None
            return None

        self._fail_streak = 0
        h, w = mat.shape[:2]
        return Frame(
            timestamp_ms=int(time.time() * 1000),
            width=int(w),
            height=int(h),
            path_hint=f"rtsp:{self.url.split('@')[-1]}",  # mask credentials
            bgr=mat,
        )

    def frames(self) -> Iterator[Frame]:
        """Yield frames at target FPS."""
        if self._cap is None:
            self.open()
        interval = 1.0 / self.target_fps
        while True:
            t0 = time.perf_counter()
            frame = self.read()
            if frame is None:
                time.sleep(0.1)
                continue
            yield frame
            elapsed = time.perf_counter() - t0
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    def close(self) -> None:
        """Close the stream."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def __enter__(self) -> "RTSPSource":
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
