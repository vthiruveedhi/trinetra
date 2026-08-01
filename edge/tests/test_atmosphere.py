import numpy as np

from rasaops_edge.metrics.atmosphere import AtmosphereEngine
from rasaops_edge.tracking.byte_track import Track


def test_atmosphere_proxies():
    eng = AtmosphereEngine()
    frame = np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)
    tracks = [
        Track(1, (40, 40, 80, 120), 0.9, "person", 0, 0),
        Track(2, (55, 50, 95, 130), 0.9, "person", 0, 0),  # close = social
    ]
    from rasaops_edge.inference.backend import Detection

    dets = [
        Detection(0, "person", 0.9, (40, 40, 80, 120)),
        Detection(39, "bottle", 0.8, (10, 10, 30, 50)),
        Detection(41, "cup", 0.7, (100, 100, 120, 130)),
    ]
    m1 = eng.update(
        frame_bgr=frame,
        tracks=tracks,
        detections=dets,
        timestamp_ms=1000,
        table_metrics=[{"state": "occupied"}, {"state": "free"}],
    )
    frame2 = frame.copy()
    frame2[20:40, 20:40] = 255
    m2 = eng.update(
        frame_bgr=frame2,
        tracks=tracks,
        detections=dets,
        timestamp_ms=1500,
        table_metrics=[{"state": "occupied"}],
    )
    assert "happiness_index" in m2
    assert 0 <= m2["happiness_index"] <= 100
    assert m2["drinks"]["visible_now"] >= 2
    assert m2["movement"]["frame_motion_energy"] >= 0
    assert "atmosphere" in m2
    assert "disclaimer" in m2
    assert m1["social"]["people_in_groups"] >= 2
