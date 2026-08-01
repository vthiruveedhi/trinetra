"""USB / built-in webcam capture for host PC dogfood (before real Pi CSI)."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, Tuple

import numpy as np

from .frame import Frame

log = logging.getLogger(__name__)


@dataclass
class WebcamSource:
    """
    OpenCV ``VideoCapture`` wrapper for index or device path.

    Windows: prefers **DirectShow (CAP_DSHOW)** — MSMF often fails with
    ``can't grab frame. Error: -1072875772`` when the device is busy or flaky.

    Env:
      RASAOPS_CAMERA_INDEX — default device index (0)
      RASAOPS_CAPTURE_FPS — target emit rate (default 2.0)
      RASAOPS_CAMERA_WIDTH / RASAOPS_CAMERA_HEIGHT — optional request size
      RASAOPS_CAMERA_BACKEND — dshow | msmf | any (default dshow on Windows)
    """

    device: int | str = 0
    target_fps: float = 2.0
    width: Optional[int] = None
    height: Optional[int] = None
    backend: Optional[int] = None  # cv2.CAP_* hint
    _cap: object = field(default=None, init=False, repr=False)
    _backend_used: Optional[int] = field(default=None, init=False, repr=False)
    _fail_streak: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        self.target_fps = max(0.1, float(self.target_fps))
        self._cap = None
        self._backend_used = None
        self._fail_streak = 0

    @classmethod
    def from_env(cls) -> "WebcamSource":
        idx_raw = os.environ.get("RASAOPS_CAMERA_INDEX", "0")
        try:
            device: int | str = int(idx_raw)
        except ValueError:
            device = idx_raw
        w = os.environ.get("RASAOPS_CAMERA_WIDTH")
        h = os.environ.get("RASAOPS_CAMERA_HEIGHT")
        fps = float(os.environ.get("RASAOPS_CAPTURE_FPS", "2.0"))
        return cls(
            device=device,
            target_fps=fps,
            width=int(w) if w else None,
            height=int(h) if h else None,
        )

    def _backend_candidates(self) -> List[Optional[int]]:
        import cv2

        pref = (os.environ.get("RASAOPS_CAMERA_BACKEND") or "").lower().strip()
        if self.backend is not None:
            return [self.backend]

        import sys

        if os.name == "nt":
            dshow = getattr(cv2, "CAP_DSHOW", None)
            msmf = getattr(cv2, "CAP_MSMF", None)
            if pref == "msmf":
                return [msmf, dshow, 0]
            if pref == "any":
                return [dshow, msmf, 0]
            # default: DirectShow only first — avoid MSMF grab bugs
            return [dshow, 0, msmf]
        if sys.platform == "darwin":
            # macOS: AVFoundation is the reliable OpenCV backend for webcams
            avf = getattr(cv2, "CAP_AVFOUNDATION", None)
            if pref in ("avfoundation", "avf", ""):
                return [avf, 0] if avf is not None else [0]
            if pref == "any":
                return [avf, 0] if avf is not None else [0]
            return [0]
        # Linux: V4L2 when available
        v4l = getattr(cv2, "CAP_V4L2", None)
        if v4l is not None:
            return [v4l, 0]
        return [0]

    def open(self) -> None:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("opencv-python required for webcam") from exc

        self.close()
        last_err: Optional[Exception] = None
        for be in self._backend_candidates():
            if be is None:
                continue
            try:
                cap = cv2.VideoCapture(self.device, int(be))
                if cap is None or not cap.isOpened():
                    if cap is not None:
                        cap.release()
                    continue
                # Prove we can actually grab (open alone is not enough on Windows)
                ok = False
                for _ in range(8):
                    ret, mat = cap.read()
                    if ret and mat is not None and getattr(mat, "size", 0) > 0:
                        ok = True
                        break
                    time.sleep(0.05)
                if not ok:
                    cap.release()
                    log.warning(
                        "Camera %s backend %s opened but cannot grab frames",
                        self.device,
                        be,
                    )
                    continue
                self._cap = cap
                self._backend_used = int(be)
                break
            except Exception as exc:  # pragma: no cover
                last_err = exc
                continue

        if self._cap is None:
            raise RuntimeError(
                f"Could not open webcam device={self.device!r}. "
                "Close Teams/Zoom/Camera app and stop other RasaOps agents "
                "(only one process can use the camera). "
                "Try: --camera 1   or   $env:RASAOPS_CAMERA_BACKEND='dshow'"
                + (f" Last error: {last_err}" if last_err else "")
            )

        # Mild size request — avoid MJPG on DSHOW if it breaks some drivers
        if self.width:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(self.width))
        if self.height:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(self.height))
        try:
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        self._fail_streak = 0
        log.info("Webcam %s opened backend=%s", self.device, self._backend_used)

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
                    "Webcam grab failed %s times (MSMF/DSHOW busy?) — reopening",
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
            path_hint=f"webcam:{self.device}",
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
                time.sleep(0.1)
                continue
            yield frame
            elapsed = time.perf_counter() - t0
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    def close(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
            self._backend_used = None

    def __enter__(self) -> "WebcamSource":
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def list_camera_indices(max_index: int = 5) -> List[Tuple[int, bool]]:
    """Probe indices 0..max_index-1; return (index, opened+readable)."""
    try:
        import cv2
    except ImportError:  # pragma: no cover
        return []
    found: List[Tuple[int, bool]] = []
    for i in range(max_index):
        cap = None
        try:
            if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
            else:
                cap = cv2.VideoCapture(i)
            ok = bool(cap is not None and cap.isOpened())
            if ok:
                ret, mat = cap.read()
                ok = bool(ret and mat is not None)
            found.append((i, ok))
        except Exception:
            found.append((i, False))
        finally:
            if cap is not None:
                cap.release()
    return found
