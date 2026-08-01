from rasaops_edge.tracking.geometry import centroid_in_polygon, point_in_polygon

# table-1 from sample zones
TABLE1 = [[40, 200], [180, 200], [180, 360], [40, 360]]


def test_point_inside_table():
    assert point_in_polygon(110, 280, TABLE1) is True


def test_point_outside_table():
    assert point_in_polygon(10, 10, TABLE1) is False


def test_centroid_in_polygon():
    assert centroid_in_polygon((80, 240, 140, 320), TABLE1) is True
    assert centroid_in_polygon((400, 100, 450, 150), TABLE1) is False
