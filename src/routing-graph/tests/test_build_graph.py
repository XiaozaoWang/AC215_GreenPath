import numpy as np

from build_graph import cells_along, largest_component, metres_per_degree, polyline_length_m, sample_polyline

LAT = 42.35


def test_length_of_one_degree_of_longitude_at_equator():
    assert abs(polyline_length_m([(0, 0), (1, 0)]) - 111320) < 100


def test_samples_are_at_most_step_apart_and_keep_both_ends():
    line = [(-71.10, LAT), (-71.09, LAT)]                       # about 820 m east-west
    points = sample_polyline(line, step_m=40)
    kx, _ = metres_per_degree(LAT)
    gaps = [(b[0] - a[0]) * kx for a, b in zip(points, points[1:])]
    assert points[0] == line[0] and points[-1] == line[1]
    assert max(gaps) <= 40 + 1e-6 and len(points) > 20


def test_cells_are_ordered_unique_and_cover_the_line():
    line = [(-71.10, LAT), (-71.09, LAT)]
    cells = cells_along(line, res=10, step_m=40)
    assert len(cells) == len(set(cells)) and len(cells) >= 5      # 820 m crosses several ~130 m cells
    assert cells_along([(-71.1, LAT), (-71.1, LAT)]) == cells_along([(-71.1, LAT)] * 2)
    assert len(cells_along([(-71.1, LAT), (-71.1, LAT)])) == 1     # zero-length segment -> one cell


def test_largest_component_keeps_the_bigger_island():
    # nodes 0-1-2 are connected, 3-4 are a separate island
    keep = largest_component(np.array([0, 1, 3]), np.array([1, 2, 4]), 5)
    assert keep.tolist() == [True, True, True, False, False]
