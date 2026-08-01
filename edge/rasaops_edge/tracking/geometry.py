"""Polygon geometry for zones.v1 association."""

from __future__ import annotations

from typing import Sequence, Tuple


Point = Tuple[float, float]
Polygon = Sequence[Sequence[float]]


def point_in_polygon(x: float, y: float, polygon: Polygon) -> bool:
    """Ray casting; polygon is list of [x, y] (at least 3)."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = float(polygon[i][0]), float(polygon[i][1])
        xj, yj = float(polygon[j][0]), float(polygon[j][1])
        intersects = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def centroid_in_polygon(xyxy: Tuple[float, float, float, float], polygon: Polygon) -> bool:
    x1, y1, x2, y2 = xyxy
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    return point_in_polygon(cx, cy, polygon)


def feet_in_polygon(xyxy: Tuple[float, float, float, float], polygon: Polygon) -> bool:
    """
    Bottom-center of the box (feet / seat contact proxy).

    Better for seated guests than centroid: torso often sits above the table
    polygon while feet / chair base fall inside the zone.
    """
    x1, y1, x2, y2 = xyxy
    fx = (x1 + x2) / 2.0
    fy = y1 + 0.88 * (y2 - y1)  # slightly above bottom edge (chair/seat)
    return point_in_polygon(fx, fy, polygon)


def association_point(
    xyxy: Tuple[float, float, float, float], mode: str = "feet"
) -> Tuple[float, float]:
    x1, y1, x2, y2 = xyxy
    if mode == "centroid":
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
    # default feet
    return ((x1 + x2) / 2.0, y1 + 0.88 * (y2 - y1))
