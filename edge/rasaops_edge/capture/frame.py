"""Shared capture frame type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Frame:
    """BGR frame with optional pixel buffer."""

    timestamp_ms: int
    width: int
    height: int
    path_hint: str
    bgr: Optional[np.ndarray] = None
