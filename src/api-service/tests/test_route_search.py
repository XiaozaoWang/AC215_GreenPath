"""Route search on a toy graph: paths, detour ellipse, mismatch, route options and the intensity rule."""
import h3
import numpy as np
import pytest

from api import road_graph, route_search
from api.dimensions import DIMENSION_NAMES
from api.road_graph import Graph, NoRouteError

# Toy graph: S=0 A=1 B=2 C=3 D=4. Shortest S->D is S-B-A-D (8).
# Detours: S-B-C-D is 10 (+25 %), S-A-D is 11 (+37.5 %).
#   edge: 0 S-A 6, 1 S-B 2, 2 B-A 1, 3 A-D 5, 4 B-C 5, 5 C-D 3
NODES = {0: (-71.10, 42.350), 1: (-71.09, 42.350), 2: (-71.10, 42.360), 3: (-71.08, 42.360), 4: (-71.08, 42.350)}
EDGES = [(0, 1, 6), (0, 2, 2), (2, 1, 1), (1, 4, 5), (2, 3, 5), (3, 4, 3)]
CANOPY = DIMENSION_NAMES.index("tree_canopy")


# One real H3 cell (resolution 10) under the middle of each edge
CELLS = [h3.latlng_to_cell((NODES[a][1] + NODES[b][1]) / 2, (NODES[a][0] + NODES[b][0]) / 2, 10) for a, b, _ in EDGES]


def toy_graph(lengths=None) -> Graph:
    """lengths: optional edge lengths replacing EDGES' (same order)."""
    u, v, length = (np.array([e[i] for e in EDGES]) for i in range(3))
    if lengths is not None:
        length = np.array(lengths)
    lon = np.array([NODES[i][0] for i in range(5)])
    lat = np.array([NODES[i][1] for i in range(5)])
    coords = [[*NODES[a], *NODES[b]] for a, b, _ in EDGES]
    return Graph(lon, lat, u, v, length.astype(float), [[cell] for cell in CELLS], coords)


# The scenic detour S-B-C-D is 13 long (+62.5 %): too far for the first ellipse (+50 %), reachable after widening to +75 %
FAR_LENGTHS = [6, 2, 1, 5, 7, 4]


def toy_scores(graph, prep) -> np.ndarray:
    """Raw scores for prep.cells: only the two edges of the S-B-C-D detour have tree canopy."""
    canopy = {CELLS[4]: 1.0, CELLS[5]: 1.0}
    X = np.full((len(prep.cells), len(DIMENSION_NAMES)), 0.5)
    for row, i in enumerate(prep.cells):
        X[row, CANOPY] = canopy.get(graph.cell_list[i], 0.0)
    return X


def weights(canopy: float) -> np.ndarray:
    w = np.zeros(len(DIMENSION_NAMES))
    w[CANOPY] = canopy
    return w


@pytest.fixture
def setup():
    graph = toy_graph()
    prep = route_search.prepare(graph, NODES[0], NODES[4])
    return graph, prep, toy_scores(graph, prep)


def test_shortest_path_and_unreachable():
    g = toy_graph()
    assert road_graph.shortest_path(g, g.length, 0, 4) == ([0, 2, 1, 4], [1, 2, 3])
    assert road_graph.shortest_path(g, g.length, 0, 4, edge_ids=[0]) is None


def test_parallel_edges_keep_the_cheapest():
    g = Graph(np.array([0.0, 1.0]), np.array([0.0, 0.0]), np.array([0, 0]), np.array([1, 1]), np.array([5.0, 3.0]),
              [["a"], ["b"]], [[0, 0, 1, 0], [0, 0, 1, 0]])
    assert road_graph.shortest_path(g, g.length, 0, 1) == ([0, 1], [1])


def test_ellipse_follows_the_detour_limit():
    g = toy_graph()
    ds, dt = road_graph.distances(g, 0), road_graph.distances(g, 4)
    assert road_graph.ellipse_mask(g, ds, dt, 8.0, 0.1).tolist() == [False, True, True, True, False, False]   # limit 8.8
    assert road_graph.ellipse_mask(g, ds, dt, 8.0, 0.25).tolist() == [False, True, True, True, True, True]    # limit 10
    assert road_graph.ellipse_mask(g, ds, dt, 8.0, 0.5).all()                                                 # limit 12


def test_prepare_returns_base_route_and_cells_under_the_ellipse(setup):
    graph, prep, _ = setup
    assert prep.base_nodes == [0, 2, 1, 4] and prep.base_length == 8.0
    assert sorted(prep.cell_ids(graph)) == sorted(CELLS)


def test_prepare_rejects_points_outside_the_graph():
    with pytest.raises(NoRouteError):
        route_search.prepare(toy_graph(), (-70.0, 42.35), NODES[4])


def test_split_preference_separates_direction_from_intensity():
    direction, intensity = route_search.split_preference(np.array([0.3, -0.6, 0.0]))
    assert intensity == pytest.approx(0.6) and direction.tolist() == pytest.approx([0.5, -1.0, 0.0])
    assert route_search.split_preference(np.zeros(3))[1] == 0


def test_cell_mismatch_is_zero_for_the_best_cells_and_one_for_the_worst(setup):
    _, _, X = setup
    mismatch = route_search.cell_mismatch(X, weights(1.0), [])
    assert mismatch == pytest.approx(1 - X[:, CANOPY])                  # canopy 0 -> worst, canopy 1 -> best


def test_cell_mismatch_ignores_unscored_cells_and_applies_must_have(setup):
    _, _, X = setup
    X[0] = np.nan
    X[3, CANOPY] = 0.05                     # below the must-have threshold
    mismatch = route_search.cell_mismatch(X, weights(-1.0), [CANOPY])
    assert np.isnan(mismatch[0]) and mismatch[3] == 1.0


def test_edge_mismatch_treats_unscored_cells_as_neutral(setup):
    graph, prep, _ = setup
    cell_mis = np.full(len(graph.cell_list), np.nan)
    cell_mis[graph.cell_list.index(CELLS[0])], cell_mis[graph.cell_list.index(CELLS[1])] = 0.0, 1.0
    assert route_search.edge_mismatch(graph, cell_mis).tolist() == [0.0, 1.0, 0.5, 0.5, 0.5, 0.5]


def test_offers_the_scenic_detour_inside_its_budget(setup):
    graph, prep, X = setup
    result = route_search.search(graph, prep, X, weights(1.0))
    assert result.shortest.nodes == [0, 2, 1, 4]
    assert list(result.options) == ["balanced"]                        # +25 % is the first budget that fits it
    detour = result.options["balanced"]
    assert detour.nodes == [0, 2, 3, 4] and detour.length_m == 10.0
    assert detour.match > result.shortest.match
    assert result.recommended == "balanced"                            # the only option offered


def test_without_alternatives_only_routes_that_fit_better_are_offered(setup):
    graph, prep, X = setup
    result = route_search.search(graph, prep, X, weights(1.0))
    assert result.improved and all(c.match > result.shortest.match for c in result.options.values())
    assert all(c.length_m <= (1 + prep.max_detour) * prep.base_length + 1e-6 for c in result.options.values())


def test_intensity_does_not_change_the_routes_only_the_recommendation(setup):
    graph, prep, X = setup
    mild, strong = (route_search.search(graph, prep, X, weights(w)) for w in (0.3, 1.0))
    assert {k: c.edges for k, c in mild.options.items()} == {k: c.edges for k, c in strong.options.items()}
    assert np.allclose(mild.mismatch, strong.mismatch)
    assert (mild.intensity, strong.intensity) == (0.3, 1.0)


def test_recommendation_follows_intensity_and_falls_back_to_the_closest_option():
    all_three = ["efficient", "balanced", "preferred"]
    assert [route_search.recommend(i, all_three) for i in (0.2, 0.6, 1.0)] == ["efficient", "balanced", "preferred"]
    assert route_search.recommend(1.0, ["efficient", "balanced"]) == "balanced"
    assert route_search.recommend(0.2, ["preferred"]) == "preferred"
    assert route_search.recommend(0.5, []) == "shortest"


def test_no_preference_returns_only_the_shortest_route(setup):
    graph, prep, X = setup
    result = route_search.search(graph, prep, X, weights(0.0))
    assert result.options == {} and result.recommended == "shortest" and result.intensity == 0


def test_unscored_detour_is_not_preferred_over_a_scored_route(setup):
    graph, prep, X = setup
    X[prep.cells.tolist().index(graph.cell_list.index(CELLS[4]))] = np.nan
    X[prep.cells.tolist().index(graph.cell_list.index(CELLS[5]))] = np.nan
    assert route_search.search(graph, prep, X, weights(1.0)).options == {}


def test_profile_is_the_length_weighted_mean_of_predicted_scores(setup):
    graph, prep, X = setup
    result = route_search.search(graph, prep, X, weights(1.0))
    detour, base = result.options["balanced"], result.shortest
    assert route_search.profile(graph, prep.cells, X, detour.edges, [CANOPY])[CANOPY] == pytest.approx(8 / 10)   # 8 of 10 m have canopy
    assert route_search.profile(graph, prep.cells, X, base.edges, [CANOPY])[CANOPY] == 0.0


def test_route_coordinates_are_continuous_and_end_at_the_snapped_nodes(setup):
    graph, prep, X = setup
    detour = route_search.search(graph, prep, X, weights(1.0)).options["balanced"]
    assert graph.route_coords(detour.nodes, detour.edges) == [list(NODES[i]) for i in (0, 2, 3, 4)]


def test_cells_are_ordered_nearest_to_the_shortest_route_first(setup):
    graph, prep, _ = setup
    on_route = {CELLS[k] for k in (1, 2, 3)}                            # the shortest route S-B-A-D
    assert {graph.cell_list[i] for i in prep.cells[:3]} == on_route      # cells under the route come first
    assert {graph.cell_list[i] for i in prep.cells[3:]} == {CELLS[0], CELLS[4], CELLS[5]}


def test_reference_makes_a_cells_mismatch_independent_of_the_other_cells(setup):
    _, _, X = setup
    reference = X.copy()
    whole = route_search.cell_mismatch(X, weights(1.0), [], reference)
    canopy = np.flatnonzero(X[:, CANOPY] == 1.0)                                 # the two cells with tree canopy
    part = route_search.cell_mismatch(X[canopy], weights(1.0), [], reference)
    assert len(canopy) == 2 and part == pytest.approx(whole[canopy]) and part == pytest.approx([0, 0])
    assert np.isnan(route_search.cell_mismatch(X[:1], weights(1.0), [])).all()     # one cell alone has nothing to compare with


def test_reference_is_used_for_the_search_too(setup):
    graph, prep, X = setup
    result = route_search.search(graph, prep, X, weights(1.0), reference=X)
    assert list(result.options) == ["balanced"] and result.options["balanced"].nodes == [0, 2, 3, 4]


def test_candidates_list_every_distinct_route_with_its_sensitivity(setup):
    graph, prep, X = setup
    result = route_search.search(graph, prep, X, weights(1.0))
    assert [c.nodes for c in result.candidates] == [[0, 2, 1, 4], [0, 2, 3, 4]]
    assert result.candidates[0].sensitivity == 0 and 0.25 < result.candidates[1].sensitivity <= 0.5      # the first s that makes the detour win


# ---------- A fan of five separate routes between S and D, 8, 10, 12, 14 and 16 long (+0 %, +25 % ... +100 %) ----------
# Route k goes S - M_k - D. Tree canopy grows with k, with diminishing returns, so every longer route fits "shade" better.
FAN_LENGTHS = [8, 10, 12, 14, 16]
FAN_CANOPY = [0.0, 0.6, 0.8, 0.9, 1.0]
FAN_S, FAN_D = (-71.10, 42.35), (-71.08, 42.35)
FAN_MID = [(-71.09, 42.35 + 0.004 * k) for k in range(5)]
FAN_CELLS = [h3.latlng_to_cell((a[1] + b[1]) / 2, (a[0] + b[0]) / 2, 10)           # edge 2k = S-M_k, edge 2k+1 = M_k-D
             for k in range(5) for a, b in ((FAN_S, FAN_MID[k]), (FAN_MID[k], FAN_D))]


def fan_graph(canopy_cells=None) -> Graph:
    lon = np.array([FAN_S[0], FAN_D[0]] + [m[0] for m in FAN_MID])
    lat = np.array([FAN_S[1], FAN_D[1]] + [m[1] for m in FAN_MID])
    u = np.array([x for k in range(5) for x in (0, 2 + k)])
    v = np.array([x for k in range(5) for x in (2 + k, 1)])
    length = np.array([FAN_LENGTHS[k] / 2 for k in range(5) for _ in range(2)], dtype=float)
    coords = [[lon[a], lat[a], lon[b], lat[b]] for a, b in zip(u, v)]
    return Graph(lon, lat, u, v, length, [[c] for c in FAN_CELLS], coords)


def fan_canopy(cell) -> float:
    return FAN_CANOPY[FAN_CELLS.index(cell) // 2]


def fan_scores(graph, prep) -> np.ndarray:
    X = np.full((len(prep.cells), len(DIMENSION_NAMES)), 0.5)
    for row, i in enumerate(prep.cells):
        X[row, CANOPY] = fan_canopy(graph.cell_list[i])
    return X


def fan(max_detour):
    graph = fan_graph()
    prep = route_search.prepare(graph, FAN_S, FAN_D, max_detour)
    return graph, prep, fan_scores(graph, prep)


def pairwise_overlaps(graph, result):
    routes = [result.shortest, *result.options.values()]
    return [route_search._overlap(graph, a, b) for i, a in enumerate(routes) for b in routes[i + 1:]]


def test_three_options_are_clearly_different_routes_spread_over_the_allowed_detour():
    graph, prep, X = fan(1.0)
    result = route_search.search(graph, prep, X, weights(1.0))
    assert sorted(result.options) == ["balanced", "efficient", "preferred"] and result.improved
    lengths = [result.options[k].length_m for k in ("efficient", "balanced", "preferred")]
    assert lengths == sorted(lengths) and len(set(lengths)) == 3
    assert min(b - a for a, b in zip(lengths, lengths[1:])) >= route_search.MIN_LENGTH_STEP * prep.base_length   # lengths clearly apart
    assert result.options["preferred"].match == max(c.match for c in result.options.values())        # the best match is 'preferred'
    assert all(o < route_search.MAX_OVERLAP for o in pairwise_overlaps(graph, result))              # no two routes are alike
    assert all(c.length_m <= 2 * prep.base_length + 1e-6 for c in result.options.values())


def test_a_narrow_detour_limit_offers_only_the_routes_that_exist_inside_it():
    graph, prep, X = fan(0.5)                                  # only the 10 and 12 long routes fit besides the shortest
    result = route_search.search(graph, prep, X, weights(1.0), add_alternatives=True)
    assert sorted(result.options) == ["efficient", "preferred"]
    assert [c.length_m for c in result.options.values()] == [10.0, 12.0]


def test_alternatives_are_offered_even_when_nothing_fits_the_preference_better():
    graph, prep, X = fan(1.0)
    X[:, CANOPY] = 0.5                                         # no place is better than another
    plain = route_search.search(graph, prep, X, weights(1.0))
    assert plain.options == {} and not plain.improved
    result = route_search.search(graph, prep, X, weights(1.0), add_alternatives=True)
    assert len(result.options) == 3 and not result.improved
    assert all(o < route_search.MAX_OVERLAP for o in pairwise_overlaps(graph, result))


def fan_route(*paths, match):
    """A candidate that uses whole fan routes (edges 2k and 2k+1 of every path k)."""
    graph = fan_graph()
    edges = [e for k in paths for e in (2 * k, 2 * k + 1)]
    return route_search.Candidate([], edges, float(graph.length[edges].sum()), match)


def test_options_form_a_staircase_each_longer_route_fits_better_and_none_is_a_copy():
    g = fan_graph()
    shortest = fan_route(0, match=.1)
    copy_of_shortest = fan_route(0, 1, match=.9)          # contains the whole shortest route
    p1, p2, p3, p4 = fan_route(1, match=.5), fan_route(2, match=.6), fan_route(3, match=.55), fan_route(4, match=.9)
    chosen = route_search._select(g, shortest, [copy_of_shortest, p1, p2, p3, p4], 8.0, 1.0)
    # p3 is longer than p2 but fits worse: beaten on both counts, so it is never offered
    assert sorted(c.length_m for c in chosen) == [10.0, 12.0, 16.0]
    ordered = sorted(chosen, key=lambda c: c.length_m)
    assert [c.match for c in ordered] == sorted(c.match for c in ordered)
    assert all(route_search._overlap(g, c, shortest) < route_search.MAX_OVERLAP for c in chosen)


def test_select_gives_fewer_options_when_there_are_not_enough_different_better_routes():
    g = fan_graph()
    shortest = fan_route(0, match=.1)
    only_one = route_search._select(g, shortest, [fan_route(1, match=.5), fan_route(1, 2, match=.6)], 8.0, 0.5)
    assert [c.length_m for c in only_one] == [10.0]       # the second route contains the first one
    assert route_search._select(g, shortest, [], 8.0, 0.5) == []


def test_overlap_measures_how_far_the_shorter_route_runs_along_the_other():
    g = fan_graph()
    p0, p4 = fan_route(0, match=0), fan_route(4, match=0)
    assert route_search._overlap(g, p0, p0) == 1.0
    assert route_search._overlap(g, fan_route(0, 1, match=0), p0) == 1.0                  # one route contains the other
    assert route_search._overlap(g, p0, p4) < 0.3                                         # separate routes meet only at S and D
    assert route_search._overlap(g, p0, p4) == route_search._overlap(g, p4, p0)


def test_streets_one_block_apart_count_as_the_same_route():
    # Path 1 is redrawn to run 40 m from path 0: no shared road, yet on the map it is nearly the same route
    g = fan_graph()
    near = fan_graph()
    mid = [FAN_MID[0][0], FAN_MID[0][1] + 0.0004]
    near.coords[2] = [FAN_S[0], FAN_S[1], *mid]
    near.coords[3] = [*mid, FAN_D[0], FAN_D[1]]
    a, b = fan_route(0, match=0), fan_route(1, match=0)
    assert route_search._overlap(near, a, b) > 0.8
    a, b = fan_route(0, match=0), fan_route(1, match=0)                                    # fresh routes: the points are cached per route
    assert route_search._overlap(g, a, b) < route_search.MAX_OVERLAP                       # the real fan: clearly apart
