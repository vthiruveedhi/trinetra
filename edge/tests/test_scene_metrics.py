from rasaops_edge.metrics.scene_metrics import SceneConfig, SceneMetricsEngine
from rasaops_edge.tracking.byte_track import Track


def _track(i: int, x: float = 100) -> Track:
    return Track(
        track_id=i,
        xyxy=(x, 100, x + 40, 200),
        conf=0.9,
        class_name="person",
        created_ts_ms=0,
        last_ts_ms=0,
    )


def test_scene_metrics_basic():
    eng = SceneMetricsEngine(config=SceneConfig(improve_interval_sec=99999))
    m = eng.update(
        tracks=[_track(1), _track(2, 400)],
        detections=[],
        frame_shape=(480, 640),
        timestamp_ms=10_000,
        inference_ms=12.0,
        table_metrics=[
            {"zone_id": "t1", "state": "occupied"},
            {"zone_id": "t2", "state": "free"},
        ],
    )
    assert m["person_count"] == 2
    assert m["scene_type"] == "dining_restaurant"
    assert "floor_load" in m
    assert m["tables_occupied"] == 1
    assert m["zone_density"]["left"] + m["zone_density"]["center"] + m["zone_density"]["right"] == 2


def test_improve_retunes_thresholds():
    eng = SceneMetricsEngine(
        config=SceneConfig(
            improve_interval_sec=0,
            busy_person_threshold=99,
            very_busy_person_threshold=100,
        )
    )
    for i in range(40):
        eng.update(
            tracks=[_track(j, 50 + j * 10) for j in range(3)],
            detections=[],
            frame_shape=(480, 640),
            timestamp_ms=1000 * i,
            inference_ms=10,
        )
    eng._last_improve_ts = 0
    m = eng.update(
        tracks=[_track(1)],
        detections=[],
        frame_shape=(480, 640),
        timestamp_ms=99999,
        inference_ms=10,
    )
    assert m["improved_this_tick"] is True
    assert eng.config.version >= 2
    assert eng.config.busy_person_threshold < 99


def test_kitchen_cooking_plating_metrics():
    eng = SceneMetricsEngine(
        config=SceneConfig(
            scene_type="kitchen_line",
            improve_interval_sec=99999,
            busy_person_threshold=2,
            very_busy_person_threshold=4,
        )
    )
    m = eng.update(
        tracks=[_track(1, 200), _track(2, 500)],
        detections=[],
        frame_shape=(480, 640),
        timestamp_ms=5_000,
        inference_ms=15.0,
        table_metrics=[
            {
                "zone_id": "cook-grill",
                "label": "Grill / cook station",
                "state": "occupied",
                "person_count_est": 1,
            },
            {
                "zone_id": "prep-back",
                "label": "Prep",
                "state": "free",
                "person_count_est": 0,
            },
        ],
        pass_stations=[
            {
                "zone_id": "plating-pass",
                "label": "Plating / pass",
                "person_count_est": 1,
            }
        ],
        pass_staff_count=1,
    )
    assert m["scene_type"] == "kitchen_line"
    k = m["kitchen"]
    assert k["cook_staff_est"] >= 1
    assert k["plating_staff_est"] >= 1
    assert k["cooking_load"] in ("idle", "light", "active", "rush")
    assert k["plating_pressure"] in ("clear", "cook_to_pass", "plating", "backed_up")
    assert "line_pace" in k
    assert "line_hint" in k
    assert "line_balance_score" in k
