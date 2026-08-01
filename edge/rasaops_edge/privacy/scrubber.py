"""
Fail-closed privacy scrubber — architecture decision table (PR-06).

Mandatory before any frame bytes enter the upload queue.
Face detector is redaction-only; never facial recognition or embeddings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

from rasaops_shared.events import RedactionStatus

from .blur import blur_regions, head_roi_from_person
from .face_detector import FaceBox, FaceDetector, FaceDetectorError, MockFaceDetector

Box = Tuple[float, float, float, float]


@dataclass
class PrivacyConfig:
    """Tunable privacy thresholds (architecture provisional defaults)."""

    fail_closed_upload: bool = True
    face_conf_min: float = 0.50
    head_roi_frac: float = 0.30
    min_person_side_px: float = 8.0
    blur_ksize: int = 31
    frames_upload_entitled: bool = True
    # Lab-only: when False, low-confidence faces still get blurred if possible
    # (never FR; still never ships identity embeddings).


@dataclass
class ScrubResult:
    """Outcome of scrubbing one frame for optional cloud upload."""

    redaction_status: RedactionStatus
    scrubbed: bool
    frame_upload_allowed: bool
    frame_bgr: Optional[np.ndarray]
    method: str
    face_boxes_redacted: int = 0
    person_boxes_heuristic: int = 0
    fail_closed: bool = True
    frames_upload_entitled: bool = True
    error: Optional[str] = None

    def privacy_dict(self) -> dict:
        """Architecture event-package privacy block."""
        return {
            "scrubbed": self.scrubbed,
            "method": self.method,
            "redaction_status": self.redaction_status.value,
            "face_boxes_redacted": self.face_boxes_redacted,
            "person_boxes_heuristic": self.person_boxes_heuristic,
            "fail_closed": self.fail_closed,
            "frames_upload_entitled": self.frames_upload_entitled,
        }


@dataclass
class PrivacyScrubber:
    """
    Apply fail-closed decision table to a BGR frame + person boxes.

    Person boxes come from the occupancy detector (class person); face boxes
    come only from ``face_detector`` for redaction.
    """

    config: PrivacyConfig = field(default_factory=PrivacyConfig)
    face_detector: FaceDetector = field(default_factory=MockFaceDetector)

    def scrub(
        self,
        frame_bgr: Optional[np.ndarray],
        person_boxes: Optional[Sequence[Box]] = None,
        *,
        face_boxes: Optional[Sequence[FaceBox]] = None,
    ) -> ScrubResult:
        """
        Scrub frame for cloud attachment.

        ``face_boxes`` if provided skips the detector (test injection).
        """
        cfg = self.config
        entitled = cfg.frames_upload_entitled
        fail_closed = cfg.fail_closed_upload

        def _meta_only(
            status: RedactionStatus,
            method: str,
            *,
            error: Optional[str] = None,
            faces: int = 0,
            heuristic: int = 0,
        ) -> ScrubResult:
            return ScrubResult(
                redaction_status=status,
                scrubbed=False,
                frame_upload_allowed=False,
                frame_bgr=None,
                method=method,
                face_boxes_redacted=faces,
                person_boxes_heuristic=heuristic,
                fail_closed=fail_closed,
                frames_upload_entitled=entitled,
                error=error,
            )

        # 1) Entitlement gate
        if not entitled:
            return _meta_only(RedactionStatus.SKIPPED_META_ONLY, "meta_only_entitlement")

        # 2) Missing / invalid frame
        if frame_bgr is None or not isinstance(frame_bgr, np.ndarray) or frame_bgr.size == 0:
            return _meta_only(
                RedactionStatus.FAILED_NO_FRAME,
                "no_frame",
                error="missing_or_empty_frame",
            )
        if frame_bgr.ndim != 3 or frame_bgr.shape[2] != 3:
            return _meta_only(
                RedactionStatus.FAILED_NO_FRAME,
                "no_frame",
                error="invalid_frame_shape",
            )

        persons = list(person_boxes or [])

        # 3) Face detector (unless injected)
        try:
            if face_boxes is not None:
                faces = list(face_boxes)
            else:
                faces = list(self.face_detector.detect_faces(frame_bgr))
        except FaceDetectorError as exc:
            return _meta_only(
                RedactionStatus.FAILED_NO_FRAME,
                "detector_error",
                error=str(exc),
            )
        except Exception as exc:  # pragma: no cover — belt & suspenders
            return _meta_only(
                RedactionStatus.FAILED_NO_FRAME,
                "detector_error",
                error=f"unexpected:{type(exc).__name__}:{exc}",
            )

        # Clamp faces to frame
        h, w = frame_bgr.shape[:2]
        faces = [f.clamp(w, h) for f in faces]

        # 4) Empty scene (no person pixels / person-class dets)
        if not persons:
            return ScrubResult(
                redaction_status=RedactionStatus.OK,
                scrubbed=True,
                frame_upload_allowed=True,
                frame_bgr=np.array(frame_bgr, copy=True),
                method="crop_only",
                face_boxes_redacted=0,
                person_boxes_heuristic=0,
                fail_closed=fail_closed,
                frames_upload_entitled=entitled,
            )

        # 5) Person present + face boxes with any conf < T → fail-closed
        if faces:
            low = [f for f in faces if f.conf < cfg.face_conf_min]
            if low:
                if fail_closed:
                    return _meta_only(
                        RedactionStatus.FAILED_NO_FRAME,
                        "face_conf_below_threshold",
                        error=f"faces_below_t={len(low)}",
                        faces=0,
                    )
                # Lab override: blur only faces meeting threshold; still redact
                faces = [f for f in faces if f.conf >= cfg.face_conf_min]
                if not faces:
                    return _meta_only(
                        RedactionStatus.FAILED_NO_FRAME,
                        "face_conf_below_threshold_lab",
                        error="no_faces_above_t_lab",
                    )

            # All remaining faces meet conf ≥ T → blur every face box
            out = blur_regions(
                frame_bgr,
                [f.xyxy for f in faces],
                ksize=cfg.blur_ksize,
            )
            return ScrubResult(
                redaction_status=RedactionStatus.OK,
                scrubbed=True,
                frame_upload_allowed=True,
                frame_bgr=out,
                method="crop+face_blur",
                face_boxes_redacted=len(faces),
                person_boxes_heuristic=0,
                fail_closed=fail_closed,
                frames_upload_entitled=entitled,
            )

        # 6) Person present + zero face dets → head-ROI heuristic
        head_rois: List[Box] = []
        for pb in persons:
            roi = head_roi_from_person(
                pb,
                head_roi_frac=cfg.head_roi_frac,
                min_side_px=cfg.min_person_side_px,
            )
            if roi is None:
                return _meta_only(
                    RedactionStatus.FAILED_NO_FRAME,
                    "head_roi_invalid",
                    error="person_box_too_small_or_invalid",
                )
            head_rois.append(roi)

        out = blur_regions(frame_bgr, head_rois, ksize=cfg.blur_ksize)
        return ScrubResult(
            redaction_status=RedactionStatus.PERSON_NO_FACE_HEURISTIC,
            scrubbed=True,
            frame_upload_allowed=True,
            frame_bgr=out,
            method="crop+head_roi_blur",
            face_boxes_redacted=0,
            person_boxes_heuristic=len(head_rois),
            fail_closed=fail_closed,
            frames_upload_entitled=entitled,
        )


def person_boxes_from_detections(
    detections: Iterable,
    *,
    class_name: str = "person",
) -> List[Box]:
    """Extract person xyxy boxes from inference Detection-like objects."""
    out: List[Box] = []
    for d in detections:
        name = getattr(d, "class_name", None)
        if name is not None and name != class_name:
            continue
        xyxy = getattr(d, "xyxy", None)
        if xyxy is None:
            continue
        out.append((float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3])))
    return out
