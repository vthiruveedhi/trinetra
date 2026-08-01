"""Event package models — PR-02 shared schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class RedactionStatus(str, Enum):
    OK = "ok"
    FAILED_NO_FRAME = "failed_no_frame"
    PERSON_NO_FACE_HEURISTIC = "person_no_face_heuristic"
    SKIPPED_META_ONLY = "skipped_meta_only"


class EventType(str, Enum):
    # MVP occupancy (architecture names)
    TABLE_OCCUPIED = "table_occupied"
    TABLE_FREED = "table_freed"
    # Aliases kept for early smoke scripts / compatibility
    OCCUPANCY_ENTER = "occupancy_enter"
    OCCUPANCY_LEAVE = "occupancy_leave"
    QUEUE_BUILDUP = "queue_buildup"
    CUSTOMER_ENTRY = "customer_entry"
    DEVICE_HEALTH = "device_health"
    NOISE_SPIKE = "noise_spike"
    # Post-MVP (not dogfood claims)
    TASK_COMPLETED = "task_completed"


class RetentionClass(str, Enum):
    EVENT_META = "event_meta"
    EVENT_FRAME = "event_frame"
    SECURITY_LOG = "security_log"
    INSIGHT = "insight"


class ZoneRef(BaseModel):
    zone_id: str
    kind: str
    label: Optional[str] = None


class BoundingBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class EventMeta(BaseModel):
    """Always uploaded; never contains raw pixels."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: EventType
    site_id: str
    device_id: str
    camera_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    zone: Optional[ZoneRef] = None
    track_id: Optional[str] = None  # short-lived anonymous only
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    metrics: dict[str, Any] = Field(default_factory=dict)
    model_version: Optional[str] = None
    retention_class: RetentionClass = RetentionClass.EVENT_META
    redaction_status: RedactionStatus = RedactionStatus.SKIPPED_META_ONLY
    frames_upload_entitled: bool = False
    schema_version: str = "event_meta.v1"


class EventFrameRef(BaseModel):
    """Pointer + redaction outcome for optional frame blob (object storage)."""

    content_type: str = "image/jpeg"
    max_bytes: int = 400_000
    width: int
    height: int
    redaction_status: RedactionStatus
    sha256: Optional[str] = None
    object_key: Optional[str] = None  # filled after upload


class EventPackage(BaseModel):
    """
    Edge → cloud package.
    Meta always present. Frame bytes only when entitled + fail-closed scrub passes.
    """

    meta: EventMeta
    frame: Optional[EventFrameRef] = None
    # frame_jpeg: not in JSON schema for wire; sent as multipart part when present
    schema_version: str = "event_package.v1"

    def may_attach_frame_bytes(self) -> bool:
        if not self.meta.frames_upload_entitled:
            return False
        if self.meta.redaction_status not in (
            RedactionStatus.OK,
            RedactionStatus.PERSON_NO_FACE_HEURISTIC,
        ):
            return False
        return self.frame is not None


class LocalMetricsSnapshot(BaseModel):
    """Kiosk /local/metrics payload (L1)."""

    site_id: str
    device_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tables: list[dict[str, Any]] = Field(default_factory=list)
    entrance_queue_count: int = 0
    pending_actions: list[str] = Field(default_factory=list)
    noise_z: Optional[float] = None
    cpu_temp_c: Optional[float] = None
    queue_depth: int = 0
