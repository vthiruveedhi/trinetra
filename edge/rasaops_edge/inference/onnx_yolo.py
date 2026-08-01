"""ONNX Runtime YOLO11n-class detector (KD-3 default pin)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from .backend import Detection, InferenceBackend

log = logging.getLogger(__name__)

# COCO subset used for restaurant occupancy MVP
DEFAULT_CLASS_NAMES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)

# MVP keep-list (occupancy uses person; table class optional)
MVP_CLASS_ALLOW = frozenset({"person", "dining table", "chair"})
# Pub / atmosphere demos — drinks & food for proxy metrics (still no FR)
ATMOSPHERE_CLASS_ALLOW = frozenset(
    {
        "person",
        "dining table",
        "chair",
        "bottle",
        "wine glass",
        "cup",
        "bowl",
        "sandwich",
        "pizza",
        "hot dog",
        "donut",
        "cake",
        "banana",
        "apple",
        "orange",
    }
)


class OnnxYoloBackend(InferenceBackend):
    """
    YOLO11/v8-style ONNX export: input NCHW float32, output [1, 4+nc, n_anchors]
    or [1, n_anchors, 4+nc] (legacy).

    Default artifact name: yolo11n_int8_320.onnx (see edge/models/pin.json).
    """

    def __init__(
        self,
        model_path: str | Path,
        input_size: int = 320,
        conf_thres: float = 0.20,
        iou_thres: float = 0.45,
        class_names: Optional[Sequence[str]] = None,
        keep_classes: Optional[frozenset[str]] = MVP_CLASS_ALLOW,
        providers: Optional[Sequence[str]] = None,
        # Small objects (cups/bottles) need a lower gate than people
        conf_thres_objects: Optional[float] = 0.12,
        min_person_box_area: float = 400.0,
    ) -> None:
        try:
            import onnxruntime as ort
        except ImportError as e:
            raise ImportError(
                "onnxruntime is required for OnnxYoloBackend. "
                "pip install 'rasaops[edge]' or onnxruntime"
            ) from e

        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"ONNX model not found: {self.model_path}. "
                "See edge/models/README.md for export/download."
            )

        self.input_size = int(input_size)
        self.conf_thres = float(conf_thres)
        self.conf_thres_objects = float(
            conf_thres_objects if conf_thres_objects is not None else conf_thres
        )
        self.iou_thres = iou_thres
        self.min_person_box_area = float(min_person_box_area)
        self.class_names = list(class_names or DEFAULT_CLASS_NAMES)
        self.keep_classes = keep_classes
        self.model_version = self.model_path.name
        self._object_classes = frozenset(
            {
                "bottle",
                "wine glass",
                "cup",
                "bowl",
                "sandwich",
                "pizza",
                "hot dog",
                "donut",
                "cake",
                "banana",
                "apple",
                "orange",
                "dining table",
                "chair",
            }
        )

        so = ort.SessionOptions()
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        # Use more CPU for live preview / dogfood (override with RASAOPS_ORT_THREADS)
        import os

        n_cpu = os.cpu_count() or 4
        threads = int(os.environ.get("RASAOPS_ORT_THREADS") or max(4, min(n_cpu - 2, 16)))
        so.intra_op_num_threads = threads
        so.inter_op_num_threads = max(1, min(4, threads // 4))
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        prov = list(providers) if providers else ["CPUExecutionProvider"]
        self._session = ort.InferenceSession(str(self.model_path), so, providers=prov)
        log.info(
            "ONNX Runtime: %s threads=%s providers=%s",
            self.model_path.name,
            threads,
            self._session.get_providers(),
        )
        self._input_name = self._session.get_inputs()[0].name
        in_shape = self._session.get_inputs()[0].shape
        # Prefer model static size if present
        if isinstance(in_shape[2], int) and in_shape[2] > 0:
            self.input_size = int(in_shape[2])

    def predict(self, frame_bgr: np.ndarray) -> List[Detection]:
        if frame_bgr is None or frame_bgr.size == 0:
            return []
        h0, w0 = frame_bgr.shape[:2]
        blob, ratio, pad = self._letterbox(frame_bgr, self.input_size)
        outputs = self._session.run(None, {self._input_name: blob})
        raw = outputs[0]
        return self._postprocess(raw, ratio, pad, w0, h0)

    def _letterbox(
        self, img: np.ndarray, new_size: int
    ) -> tuple[np.ndarray, float, tuple[float, float]]:
        h, w = img.shape[:2]
        r = min(new_size / h, new_size / w)
        nh, nw = int(round(h * r)), int(round(w * r))
        try:
            import cv2

            resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        except ImportError:
            # crude fallback without cv2
            from PIL import Image  # type: ignore

            rgb = img[:, :, ::-1]
            pil = Image.fromarray(rgb).resize((nw, nh))
            resized = np.array(pil)[:, :, ::-1]

        canvas = np.full((new_size, new_size, 3), 114, dtype=np.uint8)
        top = (new_size - nh) // 2
        left = (new_size - nw) // 2
        canvas[top : top + nh, left : left + nw] = resized
        blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        blob = np.expand_dims(blob, 0)
        return blob, r, (left, top)

    def _postprocess(
        self,
        pred: np.ndarray,
        ratio: float,
        pad: tuple[float, float],
        w0: int,
        h0: int,
    ) -> List[Detection]:
        # Normalize to [N, 4+nc]
        p = np.asarray(pred)
        while p.ndim > 2:
            # drop batch dim or merge — YOLOv8/11: [1, 4+nc, anchors]
            if p.shape[0] == 1:
                p = p[0]
            else:
                p = p.reshape(p.shape[0], -1)
        if p.ndim != 2:
            return []

        # YOLOv8/11 ONNX: [4+nc, anchors] → [anchors, 4+nc]
        feature_dims = {4 + len(self.class_names), 84, 85, 5, 6}
        if p.shape[0] in feature_dims:
            p = p.T
        elif p.shape[1] not in feature_dims and p.shape[0] < p.shape[1]:
            # ambiguous small matrix; prefer more columns as features if >4
            if p.shape[1] > 4:
                pass
            else:
                p = p.T

        if p.shape[1] < 5:
            return []

        boxes_xywh = p[:, :4]
        # class scores: either objectness*cls or direct cls
        if p.shape[1] == 5:
            # single-class [x,y,w,h,conf]
            scores = p[:, 4]
            class_ids = np.zeros(len(scores), dtype=int)
        else:
            cls_scores = p[:, 4:]
            class_ids = np.argmax(cls_scores, axis=1)
            scores = cls_scores[np.arange(len(cls_scores)), class_ids]

        # Per-class confidence: objects use a lower gate (small cups/bottles)
        names_for_ids = []
        for cid in class_ids:
            cid_i = int(cid)
            if 0 <= cid_i < len(self.class_names):
                names_for_ids.append(self.class_names[cid_i])
            else:
                names_for_ids.append(f"class_{cid_i}")
        thr = np.array(
            [
                self.conf_thres_objects
                if (n in self._object_classes or (n != "person" and cid != 0))
                else self.conf_thres
                for n, cid in zip(names_for_ids, class_ids)
            ],
            dtype=np.float32,
        )
        mask = scores >= thr
        boxes_xywh = boxes_xywh[mask]
        scores = scores[mask]
        class_ids = class_ids[mask]
        names_for_ids = [n for n, m in zip(names_for_ids, mask) if m]
        if len(scores) == 0:
            return []

        # xywh center format → xyxy in letterbox space
        xyxy = np.zeros_like(boxes_xywh)
        xyxy[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2
        xyxy[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2
        xyxy[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2
        xyxy[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2

        # undo letterbox
        pad_x, pad_y = pad
        xyxy[:, [0, 2]] -= pad_x
        xyxy[:, [1, 3]] -= pad_y
        xyxy /= max(ratio, 1e-6)
        xyxy[:, [0, 2]] = xyxy[:, [0, 2]].clip(0, w0)
        xyxy[:, [1, 3]] = xyxy[:, [1, 3]].clip(0, h0)

        keep = self._nms(xyxy, scores, self.iou_thres)
        dets: List[Detection] = []
        frame_area = float(max(1, w0 * h0))
        for i in keep:
            cid = int(class_ids[i])
            name = (
                self.class_names[cid]
                if 0 <= cid < len(self.class_names)
                else f"class_{cid}"
            )
            if self.keep_classes is not None and name not in self.keep_classes:
                # still allow person class_id 0 even if names truncated
                if name != "person" and cid != 0:
                    continue
            x1, y1, x2, y2 = (float(xyxy[i, j]) for j in range(4))
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            is_person = cid == 0 or name == "person"
            if is_person:
                # drop noise dots and full-frame false positives
                if area < self.min_person_box_area:
                    continue
                if area > 0.65 * frame_area:
                    continue
                # very thin boxes are usually false
                bw, bh = max(1.0, x2 - x1), max(1.0, y2 - y1)
                if bw / bh > 2.8 or bh / bw > 5.5:
                    continue
            dets.append(
                Detection(
                    class_id=cid,
                    class_name="person" if is_person else name,
                    conf=float(scores[i]),
                    xyxy=(x1, y1, x2, y2),
                )
            )
        return dets

    @staticmethod
    def _nms(boxes: np.ndarray, scores: np.ndarray, iou_thres: float) -> List[int]:
        if len(boxes) == 0:
            return []
        x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        areas = (x2 - x1).clip(0) * (y2 - y1).clip(0)
        order = scores.argsort()[::-1]
        keep: List[int] = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            if order.size == 1:
                break
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])
            inter = (xx2 - xx1).clip(0) * (yy2 - yy1).clip(0)
            iou = inter / (areas[i] + areas[order[1:]] - inter + 1e-6)
            inds = np.where(iou <= iou_thres)[0]
            order = order[inds + 1]
        return keep
