from rasaops_edge.inference.mock import person
from rasaops_edge.tracking.byte_track import ByteTrackLite


def test_track_birth_and_match():
    trk = ByteTrackLite(max_track_sec=120.0)
    t0 = 1_000_000
    tracks = trk.update([person((100, 100, 140, 200))], timestamp_ms=t0)
    assert len(tracks) == 1
    tid = tracks[0].track_id

    tracks2 = trk.update([person((102, 102, 142, 202))], timestamp_ms=t0 + 500)
    assert len(tracks2) == 1
    assert tracks2[0].track_id == tid
    assert tracks2[0].hits >= 2


def test_track_ttl_expires():
    trk = ByteTrackLite(max_track_sec=1.0, max_age_frames=100)
    t0 = 1_000_000
    trk.update([person((100, 100, 140, 200))], timestamp_ms=t0)
    # Far in the future without updates beyond TTL on next update after miss
    trk.update([], timestamp_ms=t0 + 500)
    tracks = trk.update([], timestamp_ms=t0 + 2500)
    assert tracks == []
    assert trk.active_tracks() == [] or all(
        (t0 + 2500 - t.created_ts_ms) / 1000.0 <= 1.0 + 1e-6
        for t in trk.active_tracks()
    )
