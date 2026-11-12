"""Preference-aware route search on the road graph.

Steps (see plan_route in routing.py for the orchestration):
  1. prepare(): snap, shortest route, detour ellipse and the H3 cells under it. Needs no preference and no scores,
     so the cells can be sent to the Model Service while Gemini parses the text.
  2. search(): predicted scores + preference -> per-cell mismatch -> per-edge cost -> routes.
     Cost of an edge = length x (1 + s x mismatch). Several sensitivities s give several candidate routes. Three of them
     are offered, spread over the allowed extra length (20 %, 50 % and 100 % of the maximum detour), clearly different
     from each other and from the shortest route (they run along each other for at most MAX_OVERLAP of their length) and in length (at least
     MIN_LENGTH_STEP apart), each longer one fitting the preference better than the shorter ones. When the preference
     does not yield three such routes, the caller widens the maximum detour (LADDER) and, as a last resort, different
     routes are added with a penalty method, so that every request gets three options whenever the road network allows.

The preference weights are split into a direction (what to look for, scaled so the largest weight is 1) and an
intensity (the largest absolute weight). Mismatch uses the direction only; the intensity only chooses which of the
offered routes is recommended.
"""
from dataclasses import dataclass, field

import h3
import numpy as np
from scipy.spatial import cKDTree

from api import road_graph, score_math
from api.road_graph import Graph, NoRouteError

# Large values matter for sparse preferences (water, farmland): few places fit, so a detour only pays off at a high s.
# Steps of sqrt(2): a route is the cheapest one only inside a narrow range of s, and a coarser grid steps over it.
SENSITIVITIES = tuple(0.25 * 2 ** (k / 2) for k in range(25))      # 0.25 ... 1024
OPTION_NAMES = ("efficient", "balanced", "preferred")
WANTED_OPTIONS = len(OPTION_NAMES)      # every request should get this many options besides the shortest route
LADDER = (0.5, 0.75, 1.0)               # maximum detour tried in turn until WANTED_OPTIONS different routes exist
MAX_DETOUR = LADDER[0]
TIER_FRACTIONS = (0.2, 0.5, 1.0)        # the three options are the best routes within these fractions of the maximum detour
MIN_GAIN = 0.005                        # a route improves on the shortest one only if its match is higher by this much
ALTERNATIVE_SENSITIVITY = 8             # the s used to look for different routes when the preference gives too few
MAX_OVERLAP = 0.7                       # two offered routes (or an option and the shortest route) may run along each other for at most this part of the shorter one
RELAXED_OVERLAP = 0.85                  # last resort, when the road network holds no three routes that differ by MAX_OVERLAP
NEAR_M = 60.0                           # streets closer than this count as the same street: parallel streets one block apart look alike on the map
MIN_LENGTH_STEP = 0.03                  # two options should differ in length by at least this fraction of the shortest route
PENALTIES = (1.1, 1.2, 1.35, 1.5, 2.0, 3.0, 5.0, 10.0)   # cost factor per route already using a road, tried from gentle to strong
MISSING_MISMATCH = 0.5      # cells without a prediction count as neither good nor bad
MAX_SNAP_M = 500.0          # a point farther than this from the road graph is outside the supported area


@dataclass
class Prepared:
    s: int
    t: int
    snap_m: tuple[float, float]
    base_nodes: list[int]
    base_edges: list[int]
    base_length: float
    allowed: np.ndarray      # edge ids inside the detour ellipse
    cells: np.ndarray        # indexes into graph.cell_list of the cells under those edges, nearest to the base route first
    max_detour: float = MAX_DETOUR   # the extra length the ellipse allows, as a fraction of the shortest route
    prepare_ms: float = 0.0  # time spent loading the graph and preparing, filled in by the caller for diagnostics

    def cell_ids(self, graph: Graph) -> list[str]:
        return [graph.cell_list[i] for i in self.cells]


@dataclass
class Candidate:
    nodes: list[int]
    edges: list[int]
    length_m: float
    match: float             # 1 - length-weighted mean mismatch (1 = every metre fits the preference)
    sensitivity: float = 0.0  # the s that produced this route (0 for the shortest route)
    points: np.ndarray | None = field(default=None, repr=False, compare=False)   # the route as points 25 m apart, in metres (cached)


@dataclass
class SearchResult:
    shortest: Candidate
    options: dict[str, Candidate]     # option name -> route; three distinct routes when the graph allows it
    recommended: str                  # "shortest" or an option name
    intensity: float
    mismatch: np.ndarray = field(default_factory=lambda: np.empty(0))   # per cell of Prepared.cells
    candidates: list[Candidate] = field(default_factory=list)           # every distinct route found, shortest first
    improved: bool = False            # at least one option fits the preference better than the shortest route


def prepare(graph: Graph, start: tuple[float, float], end: tuple[float, float], max_detour: float = MAX_DETOUR) -> Prepared:
    """start, end: (lon, lat)."""
    s, s_m = graph.snap(*start)
    t, t_m = graph.snap(*end)
    if max(s_m, t_m) > MAX_SNAP_M:
        raise NoRouteError("Start or end is outside the area covered by the road graph")
    base = road_graph.shortest_path(graph, graph.length, s, t)
    if base is None:
        raise NoRouteError("Start and end are not connected on the road graph")
    base_length = float(graph.length[base[1]].sum())
    ellipse = road_graph.ellipse_mask(graph, road_graph.distances(graph, s), road_graph.distances(graph, t), base_length, max_detour)
    allowed = np.flatnonzero(ellipse)
    cells = _nearest_first(graph, graph.edge_cells(allowed), graph.route_coords(*base))
    return Prepared(s, t, (s_m, t_m), base[0], base[1], base_length, allowed, cells, max_detour)


def _nearest_first(graph: Graph, cells: np.ndarray, route: list[list[float]]) -> np.ndarray:
    """Order cells by the distance from their centre to the shortest route, so predictions spread outwards from it."""
    if not len(cells):
        return cells
    centres = np.array([h3.cell_to_latlng(graph.cell_list[i]) for i in cells])          # (lat, lon)
    line = np.array(route) * [graph.kx, graph.ky]                                        # (lon, lat) -> metres
    # Points every 20 m along the route, so a long straight segment is measured along its length, not only at its ends.
    steps = [np.linspace(a, b, max(2, int(np.hypot(*(b - a)) // 20) + 1)) for a, b in zip(line[:-1], line[1:])]
    tree = cKDTree(np.vstack(steps) if steps else line)
    dist, _ = tree.query(np.c_[centres[:, 1] * graph.kx, centres[:, 0] * graph.ky])
    return cells[np.argsort(dist, kind="stable")]


# ---------- Preference -> mismatch ----------

def split_preference(weights: np.ndarray) -> tuple[np.ndarray, float]:
    """(direction, intensity): direction = weights / max|weights|, intensity = max|weights| (0 when there is no preference)."""
    intensity = float(np.max(np.abs(weights))) if len(weights) else 0.0
    return (weights / intensity if intensity > 0 else weights), intensity


def cell_mismatch(X: np.ndarray, direction: np.ndarray, must_have: list[int], reference: np.ndarray | None = None) -> np.ndarray:
    """Raw scores (cells, dims) -> mismatch in [0, 1] per cell (0 = best for this preference); NaN rows stay NaN.

    Cells are standardized against the reference scores (the area baseline), or against the scored cells of X when there
    is none. The weighted sum is then stretched so the 10th percentile of the reference is mismatch 1 and the 90th is 0,
    which keeps the cost scale of s the same from request to request. With a reference, a cell's mismatch does not depend
    on the other cells in X, so it can be computed batch by batch."""
    base = X if reference is None else reference
    base = base[~np.isnan(base).any(axis=1)]
    scored = ~np.isnan(X).any(axis=1)
    out = np.full(len(X), np.nan)
    if len(base) < 2 or not scored.any():
        return out
    _, mu, sigma = score_math.standardize(base, np.full(X.shape[1], score_math.PLACEHOLDER_RMSE))

    def weighted(M):
        return np.clip((M - mu) / sigma, -score_math.Z_CLIP, score_math.Z_CLIP) @ direction

    lo, hi = np.percentile(weighted(base), [10, 90])
    t = weighted(X[scored])
    out[scored] = 0.0 if hi - lo < 1e-9 else 1 - np.clip((t - lo) / (hi - lo), 0, 1)
    for d in must_have:
        out[scored & (X[:, d] < score_math.MUST_HAVE_MIN)] = 1.0
    return out


def edge_mismatch(graph: Graph, cell_mis: np.ndarray) -> np.ndarray:
    """Mean mismatch of the cells under each edge. cell_mis is indexed like graph.cell_list (NaN = unscored)."""
    values = np.where(np.isnan(cell_mis), MISSING_MISMATCH, cell_mis)[graph.flat]
    return np.add.reduceat(values, graph.ptr[:-1]) / np.diff(graph.ptr)


# ---------- Routes ----------

def _candidate(graph: Graph, path, edge_mis: np.ndarray, sensitivity: float = 0.0) -> Candidate:
    nodes, edges = path
    length = float(graph.length[edges].sum())
    return Candidate(nodes, edges, length, float(1 - (graph.length[edges] * edge_mis[edges]).sum() / length), sensitivity)


def recommend(intensity: float, available: list[str]) -> str:
    """Strong wishes recommend the preference-first route, mild ones the efficient route; the closest one offered wins."""
    wanted = 0 if intensity <= 0.4 else 1 if intensity <= 0.8 else 2
    if not available:
        return "shortest"
    return min(available, key=lambda name: (abs(OPTION_NAMES.index(name) - wanted), OPTION_NAMES.index(name)))


def _points(graph: Graph, route: Candidate) -> np.ndarray:
    """The route as points at most 25 m apart, in metres (computed once per route)."""
    if route.points is None:
        parts = []
        for k in route.edges:
            xy = np.array(graph.coords[k], dtype=float).reshape(-1, 2) * [graph.kx, graph.ky]
            parts += [np.linspace(a, b, max(2, int(np.hypot(*(b - a)) // 25) + 1)) for a, b in zip(xy[:-1], xy[1:])]
        route.points = np.vstack(parts)
    return route.points


def _overlap(graph: Graph, a: Candidate, b: Candidate) -> float:
    """Share of the shorter route that runs within NEAR_M of the other one (1 = it lies along the other route)."""
    short, long_ = (a, b) if a.length_m <= b.length_m else (b, a)
    distance, _ = cKDTree(_points(graph, long_)).query(_points(graph, short))
    return float((distance <= NEAR_M).mean())


def _distinct(graph: Graph, candidate: Candidate, others: list[Candidate], max_overlap: float = MAX_OVERLAP) -> bool:
    return all(_overlap(graph, candidate, o) < max_overlap for o in others)


def _select(graph: Graph, shortest: Candidate, improving: list[Candidate], base_length: float, max_detour: float) -> list[Candidate]:
    """Up to WANTED_OPTIONS routes that fit the preference better than the shortest one, spread over the allowed extra length.

    Every option is clearly different from the shortest route and from the others (overlap below MAX_OVERLAP), and no option
    is beaten by another on both length and match: sorted by length the options also rise in match, so each step up in
    length buys a better match."""
    chosen: list[Candidate] = []

    def fits(c):
        return (_distinct(graph, c, [shortest, *chosen])
                and all((c.length_m - o.length_m) * (c.match - o.match) > 0 and abs(c.length_m - o.length_m) >= MIN_LENGTH_STEP * base_length
                        for o in chosen))

    for fraction in TIER_FRACTIONS:             # the best route within each budget
        limit = (1 + fraction * max_detour) * base_length + 1e-6
        pool = [c for c in improving if c.length_m <= limit and fits(c)]
        if pool:
            chosen.append(max(pool, key=lambda c: (round(c.match, 6), -c.length_m)))
    while len(chosen) < WANTED_OPTIONS:         # still short: add the route whose length differs most from the chosen ones
        pool = [c for c in improving if fits(c)]
        if not pool:
            break
        chosen.append(max(pool, key=lambda c: min((abs(c.length_m - o.length_m) for o in chosen), default=c.length_m)))
    return chosen


def _alternatives(graph: Graph, prep: Prepared, edge_mis: np.ndarray, taken: list[Candidate], need: int, min_step: float = 0.0,
                  max_overlap: float = MAX_OVERLAP) -> list[Candidate]:
    """Different routes inside the detour limit (penalty method): the roads of the routes taken so far get more expensive,
    so the next shortest path goes elsewhere. They still lean towards the preference through the edge costs.

    For each route the penalty grows until the path is different enough (MAX_OVERLAP); the gentlest penalty that works is
    kept, so the route stays as short and as close to the preference as it can. A route that is too long is dropped.
    min_step: the new route must differ in length from every route taken by this fraction of the shortest route.
    max_overlap: how far the new route may run along any route taken."""
    limit = (1 + prep.max_detour) * prep.base_length + 1e-6
    base_cost = graph.length * (1 + ALTERNATIVE_SENSITIVITY * edge_mis)
    used = np.zeros(graph.m)                                     # how many routes taken so far use each edge
    for c in taken:
        used[c.edges] += 1
    found: list[Candidate] = []
    while len(found) < need:
        for factor in PENALTIES:
            path = road_graph.shortest_path(graph, base_cost * factor ** used, prep.s, prep.t, prep.allowed)
            if path is None:
                return found
            candidate = _candidate(graph, path, edge_mis, ALTERNATIVE_SENSITIVITY)
            if candidate.length_m <= limit and all(_overlap(graph, candidate, c) < max_overlap
                                                   and abs(candidate.length_m - c.length_m) >= min_step * prep.base_length for c in taken + found):
                found.append(candidate)
                used[candidate.edges] += 1
                break
        else:
            return found                                          # no penalty gives another route inside the limit
    return found


def _name(options: list[Candidate]) -> dict[str, Candidate]:
    """Name the options: the best match is 'preferred', the one adding the least length 'efficient', the other 'balanced'."""
    if len(options) == 1:
        return {"balanced": options[0]}
    if not options:
        return {}
    best = max(options, key=lambda c: (round(c.match, 6), -c.length_m))
    rest = sorted((c for c in options if c is not best), key=lambda c: c.length_m)
    named = {"preferred": best, "efficient": rest[0]}
    if len(rest) > 1:
        named["balanced"] = rest[1]
    return {name: named[name] for name in OPTION_NAMES if name in named}


def search(graph: Graph, prep: Prepared, X: np.ndarray, weights: np.ndarray, must_have: list[int] = (),
           reference: np.ndarray | None = None, add_alternatives: bool = False) -> SearchResult:
    """X: raw predicted scores for prep.cells (NaN rows = no prediction); weights: one per dimension, in -1..1.

    reference: raw scores of the area baseline used to standardize cells (default: the scored cells of X).
    add_alternatives: when fewer than WANTED_OPTIONS different routes fit the preference, add routes that merely differ
    (see _alternatives); the caller sets it once the maximum detour cannot be widened any more."""
    direction, intensity = split_preference(weights)
    cell_mis = np.full(len(graph.cell_list), np.nan)
    mismatch = cell_mismatch(X, direction, list(must_have), reference) if intensity > 0 else np.full(len(X), 0.0)
    cell_mis[prep.cells] = mismatch
    edge_mis = edge_mismatch(graph, cell_mis)
    shortest = _candidate(graph, (prep.base_nodes, prep.base_edges), edge_mis)
    if intensity == 0:
        return SearchResult(shortest, {}, "shortest", 0.0, mismatch, [shortest])

    # One search per sensitivity; identical routes are kept once.
    found = {tuple(shortest.edges): shortest}
    for s in SENSITIVITIES:
        cost = graph.length * (1 + s * edge_mis)
        path = road_graph.shortest_path(graph, cost, prep.s, prep.t, prep.allowed)
        if path is not None and tuple(path[1]) not in found:
            found[tuple(path[1])] = _candidate(graph, path, edge_mis, s)

    improving = [c for c in found.values() if c.match > shortest.match + MIN_GAIN]
    chosen = _select(graph, shortest, improving, prep.base_length, prep.max_detour)
    improved = bool(chosen)
    candidates = list(found.values())
    if add_alternatives:
        # Fill up to three. Routes whose lengths are spread out come first: other routes that fit better than the shortest one
        # but are beaten by a chosen option, then routes that merely differ (penalty method); then the same without the spacing.
        for step in (MIN_LENGTH_STEP, 0.0):
            while len(chosen) < WANTED_OPTIONS:
                pool = [c for c in improving if all(c is not o for o in chosen) and _distinct(graph, c, [shortest, *chosen])
                        and all(abs(c.length_m - o.length_m) >= step * prep.base_length for o in chosen)]
                if not pool:
                    break
                chosen.append(max(pool, key=lambda c: (round(c.match, 6), -c.length_m)))
            if len(chosen) < WANTED_OPTIONS:
                extra = _alternatives(graph, prep, edge_mis, [shortest] + chosen, WANTED_OPTIONS - len(chosen), step)
                chosen += extra
                candidates += extra
        if len(chosen) < WANTED_OPTIONS:             # still short (a short trip in a tight street grid): accept routes that look more alike
            extra = _alternatives(graph, prep, edge_mis, [shortest] + chosen, WANTED_OPTIONS - len(chosen), 0.0, RELAXED_OVERLAP)
            chosen += extra
            candidates += extra
    options = _name(chosen)
    return SearchResult(shortest, options, recommend(intensity, list(options)), intensity, mismatch, candidates, improved)


def profile(graph: Graph, cells: np.ndarray, X: np.ndarray, edges: list[int], dims: list[int]) -> dict[int, float]:
    """Length-weighted mean predicted score along a route for the given dimensions (dimension index -> value).

    cells/X are Prepared.cells and the scores; unscored cells are ignored; a dimension with no score is left out."""
    full = np.full((len(graph.cell_list), X.shape[1]), np.nan)
    full[cells] = X
    total, weight = np.zeros(len(dims)), np.zeros(len(dims))
    for k in edges:
        rows = full[graph.flat[graph.ptr[k]:graph.ptr[k + 1]]][:, dims]
        ok = ~np.isnan(rows).all(axis=0)
        total[ok] += graph.length[k] * np.nanmean(rows[:, ok], axis=0)
        weight[ok] += graph.length[k]
    return {d: float(total[i] / weight[i]) for i, d in enumerate(dims) if weight[i] > 0}
