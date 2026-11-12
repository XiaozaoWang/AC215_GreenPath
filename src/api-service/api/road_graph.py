"""Road graph for route planning: loading, nearest-node lookup, shortest paths and the detour ellipse.

The graph is built offline by src/routing-graph/build_graph.py and shipped as two Parquet files per region.
It is undirected; every edge knows which H3 cells (resolution 10) it passes through.
"""
import math
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

GRAPH_ROOT = Path(os.getenv("ROUTE_GRAPH_DIR", Path(__file__).resolve().parents[1] / "data" / "graphs"))


class UnknownGraphError(ValueError):
    pass


class NoRouteError(ValueError):
    pass


def metres_per_degree(lat: float) -> tuple[float, float]:
    # Flat-earth approximation, accurate enough inside one city.
    return 111320.0 * math.cos(math.radians(lat)), 110540.0


@dataclass
class Graph:
    lon: np.ndarray        # node coordinates
    lat: np.ndarray
    u: np.ndarray          # edge endpoints (node numbers)
    v: np.ndarray
    length: np.ndarray     # edge length in metres
    cells: list            # per edge: H3 cells it passes through, in travel order
    coords: list           # per edge: flat [lon, lat, lon, lat, ...] from u to v

    def __post_init__(self):
        self.n, self.m = len(self.lon), len(self.u)
        self.cell_list = sorted({c for cs in self.cells for c in cs})
        index = {c: i for i, c in enumerate(self.cell_list)}
        # All (edge, cell) pairs stored edge by edge; ptr[k]:ptr[k+1] is the slice of edge k.
        self.flat = np.array([index[c] for cs in self.cells for c in cs], dtype=np.int64)
        self.ptr = np.concatenate([[0], np.cumsum([len(cs) for cs in self.cells])]).astype(np.int64)
        self.kx, self.ky = metres_per_degree(float(np.mean(self.lat)))
        self.tree = cKDTree(np.c_[self.lon * self.kx, self.lat * self.ky])

    def snap(self, lon: float, lat: float) -> tuple[int, float]:
        """Nearest node to a point, and its distance in metres."""
        dist, node = self.tree.query([lon * self.kx, lat * self.ky])
        return int(node), float(dist)

    def edge_cells(self, edge_ids) -> np.ndarray:
        """Sorted unique indexes (into cell_list) of the cells under the given edges."""
        if not len(edge_ids):
            return np.empty(0, dtype=np.int64)
        return np.unique(np.concatenate([self.flat[self.ptr[k]:self.ptr[k + 1]] for k in edge_ids]))

    def route_coords(self, nodes: list[int], edges: list[int]) -> list[list[float]]:
        """Polyline [[lon, lat], ...] of a route given as nodes and edge numbers."""
        out = []
        for node, k in zip(nodes, edges):
            flat = self.coords[k]
            points = list(zip(flat[0::2], flat[1::2]))
            if self.u[k] != node:
                points.reverse()
            out.extend(points if not out else points[1:])
        return [[float(x), float(y)] for x, y in out]


def _read(path: Path) -> dict:
    return pq.read_table(path).to_pydict()


def from_dir(folder: Path) -> Graph:
    nodes, edges = _read(folder / "nodes.parquet"), _read(folder / "edges.parquet")
    return Graph(np.array(nodes["lon"], float), np.array(nodes["lat"], float),
                 np.array(edges["u"], np.int64), np.array(edges["v"], np.int64), np.array(edges["length_m"], float),
                 [list(c) for c in edges["cells"]], [list(c) for c in edges["coords"]])


_lock = threading.Lock()


@lru_cache(maxsize=4)
def _load(region_id: str) -> Graph:
    return from_dir(GRAPH_ROOT / region_id)


def load(region_id: str) -> Graph:
    folder = GRAPH_ROOT / region_id
    if "/" in region_id or "\\" in region_id or region_id.startswith(".") or not (folder / "edges.parquet").is_file():
        raise UnknownGraphError(f"No road graph for region: {region_id}")
    with _lock:  # concurrent first requests must not build the same graph twice
        return _load(region_id)


# ---------- Shortest paths (scipy) ----------

def _matrix(n, u, v, cost, ids):
    """Undirected sparse matrix keeping only the cheapest of several parallel edges.

    Returns (matrix, a, b, kept) where kept are the ids of the edges that were kept, between nodes a and b."""
    a, b = np.minimum(u, v), np.maximum(u, v)
    key = a.astype(np.int64) * n + b
    order = np.lexsort((cost, key))
    first = np.r_[True, key[order][1:] != key[order][:-1]]
    keep = order[first]
    return csr_matrix((np.maximum(cost[keep], 1e-6), (a[keep], b[keep])), shape=(n, n)), a[keep], b[keep], ids[keep]


def distances(graph: Graph, source: int) -> np.ndarray:
    """Length in metres from source to every node."""
    matrix, *_ = _matrix(graph.n, graph.u, graph.v, graph.length, np.arange(graph.m))
    return dijkstra(matrix, directed=False, indices=source)


def shortest_path(graph: Graph, cost: np.ndarray, s: int, t: int, edge_ids=None):
    """Cheapest route using only edge_ids (default: all). Returns (nodes, edge numbers) or None."""
    ids = np.arange(graph.m) if edge_ids is None else np.asarray(edge_ids)
    matrix, a, b, kept = _matrix(graph.n, graph.u[ids], graph.v[ids], cost[ids], ids)
    dist, pred = dijkstra(matrix, directed=False, indices=s, return_predecessors=True)
    if not np.isfinite(dist[t]):
        return None
    nodes = [t]
    while nodes[-1] != s:
        nodes.append(int(pred[nodes[-1]]))
    nodes.reverse()
    edge_of = {(int(x), int(y)): int(k) for x, y, k in zip(a, b, kept)}
    return nodes, [edge_of[(min(p, q), max(p, q))] for p, q in zip(nodes, nodes[1:])]


def ellipse_mask(graph: Graph, dist_s: np.ndarray, dist_t: np.ndarray, base_length: float, detour: float) -> np.ndarray:
    """Edges that can lie on a route at most (1 + detour) x base_length long. Needs no scores."""
    through = np.minimum(dist_s[graph.u] + graph.length + dist_t[graph.v], dist_s[graph.v] + graph.length + dist_t[graph.u])
    return through <= (1 + detour) * base_length
