from rasaops_edge.capture.file_source import FileVideoSource, Frame


def test_frame_dataclass():
    f = Frame(timestamp_ms=1, width=640, height=480, path_hint="x")
    assert f.width == 640


def test_source_construct():
    s = FileVideoSource("missing.mp4", target_fps=2.0)
    assert s.target_fps == 2.0
