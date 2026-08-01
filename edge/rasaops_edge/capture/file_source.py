"""File-based video capture for Virtual Pi / host DevEx (no CSI required)."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator, Optional

import time

import numpy as np

from .frame import Frame

__all__ = ["FileVideoSource", "Frame"]


class FileVideoSource:
    """
    CaptureSource implementation for mocks.

    Env:
      RASAOPS_VIDEO_PATH — path to mp4/avi
      RASAOPS_CAPTURE_FPS — target emit rate (default 2.0)
    """

    def __init__(
        self,
        video_path: str | Path,
        target_fps: float = 2.0,
        loop: bool = True,
    ) -> None:
        self.video_path = Path(video_path)
        self.target_fps = max(0.1, target_fps)
        self.loop = loop
        self._cap = None
        self._use_cv2 = False

    def open(self) -> None:
        if not self.video_path.exists():
            raise FileNotFoundError(
                f"Video not found: {self.video_path}. "
                "Run devops/virtual-pi/Generate-TestPattern.ps1 or place sample_dining.mp4."
            )
        try:
            import cv2  # type: ignore

            self._cap = cv2.VideoCapture(str(self.video_path))
            if not self._cap.isOpened():
                raise RuntimeError(f"OpenCV failed to open {self.video_path}")
            self._use_cv2 = True
        except ImportError:
            self._use_cv2 = False

    def frames(self) -> Iterator[Frame]:
        if self._cap is None and self._use_cv2 is False and self.video_path.exists():
            try:
                self.open()
            except Exception:
                pass

        interval = 1.0 / self.target_fps
        while True:
            t0 = time.perf_counter()
            ts = int(time.time() * 1000)
            bgr: Optional[np.ndarray] = None

            if self._use_cv2 and self._cap is not None:
                import cv2  # type: ignore

                ok, mat = self._cap.read()
                if not ok:
                    if self.loop:
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                    break
                bgr = mat
                h, w = mat.shape[:2]
            else:
                w, h = 640, 480
                bgr = np.zeros((h, w, 3), dtype=np.uint8)

            yield Frame(
                timestamp_ms=ts,
                width=w,
                height=h,
                path_hint=str(self.video_path),
                bgr=bgr,
            )

            elapsed = time.perf_counter() - t0
            sleep_for = interval - elapsed
            if sleep_for > 0:
                time.sleep(sleep_for)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
