"""zones.v1 load + validate — MVP config path (wizard is later UX)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Zone(BaseModel):
    zone_id: str
    label: Optional[str] = None
    kind: Literal["table", "entrance", "queue", "pass", "other"]
    polygon: list[list[float]]
    capacity_hint: Optional[int] = Field(default=None, ge=1)

    @field_validator("polygon")
    @classmethod
    def at_least_triangle(cls, v: list[list[float]]) -> list[list[float]]:
        if len(v) < 3:
            raise ValueError("polygon needs at least 3 points")
        for pt in v:
            if len(pt) != 2:
                raise ValueError("each polygon point must be [x, y]")
        return v


class ZonesDocument(BaseModel):
    version: Literal["zones.v1"]
    site_id: str
    camera_id: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    zones: list[Zone]


def load_zones(path: str | Path) -> ZonesDocument:
    p = Path(path)
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return ZonesDocument.model_validate(data)


def tables(doc: ZonesDocument) -> list[Zone]:
    return [z for z in doc.zones if z.kind == "table"]
