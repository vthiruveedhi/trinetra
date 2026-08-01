from pathlib import Path

from rasaops_edge.events.occupancy import OccupancyConfig, OccupancyEngine, TableOccupancy
from rasaops_edge.inference.mock import person
from rasaops_edge.pipeline import EdgePipeline
from rasaops_shared.events import EventType
from rasaops_shared.zones import load_zones

ROOT = Path(__file__).resolve().parents[2]
ZONES = ROOT / "shared" / "schemas" / "examples" / "zones.v1.sample.json"


def test_table_occupied_after_enter_dwell():
    pipe = EdgePipeline.from_paths(ZONES)
    pipe.config.t_enter_sec = 1.0
    pipe.config.t_leave_sec = 2.0
    pipe.config.sample_fps = 2.0

    t0 = 10_000
    events_all = []
    # 3 frames * 0.5s = 1.5s > 1.0s enter
    for i in range(4):
        r = pipe.process_synthetic(
            [person((80, 240, 140, 320))],
            timestamp_ms=t0 + i * 500,
        )
        events_all.extend(r.events)

    types = [e.event_type for e in events_all]
    assert EventType.TABLE_OCCUPIED in types
    assert pipe.occupancy.tables["table-1"].occupancy == TableOccupancy.OCCUPIED
    # Cloud payload must not include track_id
    for e in events_all:
        assert e.track_id is None


def test_table_freed_after_leave_dwell():
    pipe = EdgePipeline.from_paths(ZONES)
    pipe.config.t_enter_sec = 0.5
    pipe.config.t_leave_sec = 1.0
    pipe.config.sample_fps = 2.0
    t0 = 20_000

    for i in range(3):
        pipe.process_synthetic([person((80, 240, 140, 320))], timestamp_ms=t0 + i * 500)
    assert pipe.occupancy.tables["table-1"].occupancy == TableOccupancy.OCCUPIED

    free_events = []
    for i in range(4):
        r = pipe.process_synthetic([], timestamp_ms=t0 + 2000 + i * 500)
        free_events.extend(r.events)

    assert any(e.event_type == EventType.TABLE_FREED for e in free_events)
    assert pipe.occupancy.tables["table-1"].occupancy == TableOccupancy.FREE


def test_queue_buildup():
    zones = load_zones(ZONES)
    cfg = OccupancyConfig(
        queue_min_count=2,
        queue_min_duration_sec=1.0,
        sample_fps=2.0,
        site_id=zones.site_id,
    )
    eng = OccupancyEngine(zones=zones, config=cfg)
    from rasaops_edge.tracking.byte_track import Track

    # queue-host polygon [[400,140]...[600,280]] center ~500,210
    def mk(i: int) -> Track:
        return Track(
            track_id=i,
            xyxy=(480 + i * 5, 180, 520 + i * 5, 240),
            conf=0.9,
            class_name="person",
            created_ts_ms=0,
            last_ts_ms=0,
        )

    t0 = 30_000
    events = []
    for i in range(4):
        tracks = [mk(1), mk(2)]
        events.extend(eng.update(tracks, timestamp_ms=t0 + i * 500))

    assert any(e.event_type == EventType.QUEUE_BUILDUP for e in events)


def test_customer_entry_rising_edge():
    pipe = EdgePipeline.from_paths(ZONES)
    t0 = 40_000
    # entrance-main [[200,20]...[440,120]] center ~320,70
    r0 = pipe.process_synthetic([], timestamp_ms=t0)
    assert r0.events == []
    r1 = pipe.process_synthetic(
        [person((300, 40, 360, 100))],
        timestamp_ms=t0 + 500,
    )
    assert any(e.event_type == EventType.CUSTOMER_ENTRY for e in r1.events)
