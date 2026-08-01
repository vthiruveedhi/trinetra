"""Ultralytics YOLO backend for host-lab higher recall (imgsz 640+).

Pi product path stays ONNX INT8 320. This backend is for Windows/macOS dogfood
where missing half the room at 320px is unacceptable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from .backend import Detection, InferenceBackend
from .onnx_yolo import ATMOSPHERE_CLASS_ALLOW, DEFAULT_CLASS_NAMES, MVP_CLASS_ALLOW

log = logging.getLogger(__name__)

# COCO person class id
_PERSON = 0


class UltralyticsYoloBackend(InferenceBackend):
    """
    Host-lab detector using Ultralytics + yolo11n.pt at imgsz>=640.

    Much higher person recall on wide pub CCTV than fixed ONNX 320.
    """

    def __init__(
        self,
        model_path: str | Path,
        imgsz: int = 640,
        conf_thres: float = 0.15,
        iou_thres: float = 0.45,
        keep_classes: Optional[frozenset[str]] = MVP_CLASS_ALLOW,
        class_names: Optional[Sequence[str]] = None,
        device: str = "cpu",
    ) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as e:
            raise ImportError(
                "ultralytics required for UltralyticsYoloBackend: pip install ultralytics"
            ) from e

        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(f"Weights not found: {self.model_path}")

        self.imgsz = int(imgsz)
        self.conf_thres = float(conf_thres)
        self.iou_thres = float(iou_thres)
        self.keep_classes = keep_classes
        self.class_names = list(class_names or DEFAULT_CLASS_NAMES)
        self.device = device
        self.model_version = f"{self.model_path.name}@imgsz{self.imgsz}"
        self._model = YOLO(str(self.model_path))
        log.info("Ultralytics YOLO: %s imgsz=%s conf=%s", self.model_version, imgsz, conf_thres)

    def predict(self, frame_bgr: np.ndarray) -> List[Detection]:
        if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
            return []
        h0, w0 = frame_bgr.shape[:2]
        # Ultralytics accepts BGR numpy
        results = self._model.predict(
            source=frame_bgr,
            imgsz=self.imgsz,
            conf=self.conf_thres,
            iou=self.iou_thres,
            verbose=False,
            device=self.device,
        )
        dets: List[Detection] = []
        if not results:
            return dets
        r0 = results[0]
        boxes = getattr(r0, "boxes", None)
        if boxes is None or len(boxes) == 0:
            return dets

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        frame_area = float(max(1, h0 * w0))

        for i in range(len(xyxy)):
            cid = int(clss[i])
            name = (
                self.class_names[cid]
                if 0 <= cid < len(self.class_names)
                else f"class_{cid}"
            )
            if self.keep_classes is not None and name not in self.keep_classes:
                if name != "person" and cid != _PERSON:
                    continue
            x1, y1, x2, y2 = (float(v) for v in xyxy[i])
            x1 = max(0.0, min(float(w0), x1))
            x2 = max(0.0, min(float(w0), x2))
            y1 = max(0.0, min(float(h0), y1))
            y2 = max(0.0, min(float(h0), y2))
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            is_person = cid == _PERSON or name == "person"
            if is_person:
                if area < 120.0:
                    continue
                if area > 0.70 * frame_area:
                    continue
            dets.append(
                Detection(
                    class_id=cid,
                    class_name="person" if is_person else name,
                    conf=float(confs[i]),
                    xyxy=(x1, y1, x2, y2),
                )
            )
        return dets
