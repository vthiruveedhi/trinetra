"""
Offline-first SQLite event queue.

States: pending → inflight → acked | deadletter
Frame blobs on disk with 72h TTL then meta-only keep.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional
from uuid import uuid4

from rasaops_shared.events import EventMeta, EventPackage, RedactionStatus


class QueueStatus(str, Enum):
    PENDING = "pending"
    INFLIGHT = "inflight"
    ACKED = "acked"
    DEADLETTER = "deadletter"


@dataclass
class QueueItem:
    id: str
    status: QueueStatus
    meta_json: str
    frame_path: Optional[str]
    attempts: int
    next_attempt_ms: int
    created_ms: int
    updated_ms: int
    last_error: Optional[str] = None

    def event_meta(self) -> EventMeta:
        return EventMeta.model_validate_json(self.meta_json)

    def to_package(self) -> EventPackage:
        meta = self.event_meta()
        frame = None
        if self.frame_path and Path(self.frame_path).exists():
            from rasaops_shared.events import EventFrameRef

            frame = EventFrameRef(
                width=0,
                height=0,
                redaction_status=meta.redaction_status,
                object_key=self.frame_path,
            )
        return EventPackage(meta=meta, frame=frame)


class EventQueue:
    """
    Durable store-and-forward queue.

    - enqueue(meta, frame_bytes optional)
    - claim_batch for sync agent
    - ack / nack with exponential backoff
    - purge_expired frames after frame_ttl_sec (default 72h)
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        frames_dir: Optional[str | Path] = None,
        max_attempts: int = 12,
        base_backoff_sec: float = 2.0,
        frame_ttl_sec: float = 72 * 3600,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.frames_dir = Path(frames_dir) if frames_dir else self.db_path.parent / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max_attempts
        self.base_backoff_sec = base_backoff_sec
        self.frame_ttl_sec = frame_ttl_sec
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS event_queue (
              id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              meta_json TEXT NOT NULL,
              frame_path TEXT,
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_ms INTEGER NOT NULL,
              created_ms INTEGER NOT NULL,
              updated_ms INTEGER NOT NULL,
              last_error TEXT
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_eq_status_next ON event_queue(status, next_attempt_ms)"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def enqueue(
        self,
        meta: EventMeta,
        frame_jpeg: Optional[bytes] = None,
        *,
        scrub_allows_frame: bool = False,
    ) -> str:
        """
        Store event meta always. Frame bytes only when scrub allows + entitled.
        """
        now = int(time.time() * 1000)
        qid = meta.event_id or str(uuid4())
        frame_path: Optional[str] = None
        may_frame = (
            scrub_allows_frame
            and meta.frames_upload_entitled
            and meta.redaction_status
            in (RedactionStatus.OK, RedactionStatus.PERSON_NO_FACE_HEURISTIC)
            and frame_jpeg
        )
        if may_frame and frame_jpeg:
            fp = self.frames_dir / f"{qid}.jpg"
            fp.write_bytes(frame_jpeg)
            frame_path = str(fp)

        self._conn.execute(
            """
            INSERT INTO event_queue
              (id, status, meta_json, frame_path, attempts, next_attempt_ms, created_ms, updated_ms)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                qid,
                QueueStatus.PENDING.value,
                meta.model_dump_json(),
                frame_path,
                now,
                now,
                now,
            ),
        )
        self._conn.commit()
        return qid

    def depth(self, status: Optional[QueueStatus] = None) -> int:
        if status is None:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM event_queue WHERE status IN ('pending','inflight')"
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS c FROM event_queue WHERE status=?",
                (status.value,),
            ).fetchone()
        return int(row["c"])

    def claim_batch(self, limit: int = 10) -> List[QueueItem]:
        """Move due pending items to inflight and return them."""
        now = int(time.time() * 1000)
        rows = self._conn.execute(
            """
            SELECT * FROM event_queue
            WHERE status='pending' AND next_attempt_ms <= ?
            ORDER BY created_ms ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
        items: List[QueueItem] = []
        for row in rows:
            self._conn.execute(
                "UPDATE event_queue SET status=?, updated_ms=? WHERE id=?",
                (QueueStatus.INFLIGHT.value, now, row["id"]),
            )
            items.append(self._row_to_item(row, status=QueueStatus.INFLIGHT))
        self._conn.commit()
        return items

    def ack(self, item_id: str) -> None:
        now = int(time.time() * 1000)
        row = self._conn.execute(
            "SELECT frame_path FROM event_queue WHERE id=?", (item_id,)
        ).fetchone()
        if row and row["frame_path"]:
            try:
                Path(row["frame_path"]).unlink(missing_ok=True)
            except OSError:
                pass
        self._conn.execute(
            """
            UPDATE event_queue
            SET status=?, updated_ms=?, frame_path=NULL, last_error=NULL
            WHERE id=?
            """,
            (QueueStatus.ACKED.value, now, item_id),
        )
        self._conn.commit()

    def nack(self, item_id: str, error: str) -> None:
        now = int(time.time() * 1000)
        row = self._conn.execute(
            "SELECT attempts FROM event_queue WHERE id=?", (item_id,)
        ).fetchone()
        if row is None:
            return
        attempts = int(row["attempts"]) + 1
        if attempts >= self.max_attempts:
            self._conn.execute(
                """
                UPDATE event_queue
                SET status=?, attempts=?, updated_ms=?, last_error=?
                WHERE id=?
                """,
                (QueueStatus.DEADLETTER.value, attempts, now, error[:500], item_id),
            )
        else:
            backoff_ms = int(self.base_backoff_sec * (2 ** min(attempts, 8)) * 1000)
            self._conn.execute(
                """
                UPDATE event_queue
                SET status=?, attempts=?, next_attempt_ms=?, updated_ms=?, last_error=?
                WHERE id=?
                """,
                (
                    QueueStatus.PENDING.value,
                    attempts,
                    now + backoff_ms,
                    now,
                    error[:500],
                    item_id,
                ),
            )
        self._conn.commit()

    def purge_expired_frames(self) -> int:
        """Drop frame files older than TTL; keep meta rows."""
        cutoff = int((time.time() - self.frame_ttl_sec) * 1000)
        rows = self._conn.execute(
            """
            SELECT id, frame_path FROM event_queue
            WHERE frame_path IS NOT NULL AND created_ms < ?
            """,
            (cutoff,),
        ).fetchall()
        n = 0
        for row in rows:
            if row["frame_path"]:
                try:
                    Path(row["frame_path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            self._conn.execute(
                "UPDATE event_queue SET frame_path=NULL, updated_ms=? WHERE id=?",
                (int(time.time() * 1000), row["id"]),
            )
            n += 1
        self._conn.commit()
        return n

    def recent_events(self, limit: int = 50) -> List[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT id, status, meta_json, attempts, created_ms, last_error
            FROM event_queue
            ORDER BY created_ms DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        out: List[dict[str, Any]] = []
        for row in rows:
            meta = json.loads(row["meta_json"])
            out.append(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "event_type": meta.get("event_type"),
                    "zone": meta.get("zone"),
                    "timestamp": meta.get("timestamp"),
                    "redaction_status": meta.get("redaction_status"),
                    "attempts": row["attempts"],
                    "last_error": row["last_error"],
                }
            )
        return out

    @staticmethod
    def _row_to_item(row: sqlite3.Row, status: Optional[QueueStatus] = None) -> QueueItem:
        return QueueItem(
            id=row["id"],
            status=status or QueueStatus(row["status"]),
            meta_json=row["meta_json"],
            frame_path=row["frame_path"],
            attempts=int(row["attempts"]),
            next_attempt_ms=int(row["next_attempt_ms"]),
            created_ms=int(row["created_ms"]),
            updated_ms=int(row["updated_ms"]),
            last_error=row["last_error"],
        )
