# Build a bike-routing graph for one area from an OpenStreetMap extract, and record which H3 cells every
# road segment passes through (so a segment's score can later be read from the cells under it).
# Run offline; the API service only loads the output files (src/api-service/api/road_graph.py).
#
#   python build_graph.py --city city_ma_boston_big --pbf massachusetts-240101.osm.pbf
#
# Output folder (default ../api-service/data/graphs/<city>/):
#   nodes.parquet   node (0..N-1), lon, lat
#   edges.parquet   u, v, length_m, highway, cells (list of H3 ids, in travel order), coords (flat lon,lat,lon,lat,...)
#
# Simplifications: the graph is undirected (one-way streets and bike-specific rules are ignored), and
# only the largest connected component is kept.
import argparse
import math
from pathlib import Path

import h3
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

CONFIG = Path(__file__).resolve().parents[1] / "data-collector" / "configs" / "1026_Full_data.yaml"


# ---------- Pure helpers (unit-tested) ----------

# Metres per degree of longitude / latitude at this latitude (flat-earth approximation, fine for a city)
def metres_per_degree(lat):
    return 111320.0 * math.cos(math.radians(lat)), 110540.0


# Length in metres of a polyline given as [(lon, lat), ...]
def polyline_length_m(coords):
    total = 0.0
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        kx, ky = metres_per_degree((y0 + y1) / 2)
        total += math.hypot((x1 - x0) * kx, (y1 - y0) * ky)
    return total


# Points along the polyline at most step_m apart, always including the first and last vertex
def sample_polyline(coords, step_m=40.0):
    points = [coords[0]]
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        kx, ky = metres_per_degree((y0 + y1) / 2)
        n = max(1, math.ceil(math.hypot((x1 - x0) * kx, (y1 - y0) * ky) / step_m))
        points += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(1, n + 1)]
    return points


# H3 cells the polyline passes through, in the order they are first visited, without repeats
def cells_along(coords, res=10, step_m=40.0):
    seen = {}
    for lon, lat in sample_polyline(coords, step_m):
        seen.setdefault(h3.latlng_to_cell(lat, lon, res), None)
    return list(seen)


# Boolean mask over nodes 0..n-1 marking the largest connected component
def largest_component(u, v, n):
    graph = coo_matrix((np.ones(len(u)), (u, v)), shape=(n, n))
    _, labels = connected_components(graph, directed=False)
    return labels == np.bincount(labels).argmax()


# ---------- Reading OSM ----------

def bbox_of(city, config=CONFIG):
    import yaml
    for area in yaml.safe_load(Path(config).read_text(encoding="utf-8"))["areas"]:
        if area["area_id"] == city:
            return area["bbox"]                      # [west, south, east, north]
    raise KeyError(f"{city} is not in {config}")


# pyrosm reads the whole state file and keeps what falls inside the bounding box (slow, a few minutes)
def read_network(pbf, bbox, network_type="cycling"):
    import pyrosm
    osm = pyrosm.OSM(str(pbf), bounding_box=list(bbox))
    nodes, edges = osm.get_network(network_type=network_type, nodes=True)
    return nodes, edges


# ---------- Building ----------

def build_graph(nodes, edges, h3_res=10, step_m=40.0):
    edges = edges[edges.geometry.notna() & edges.u.isin(nodes.id) & edges.v.isin(nodes.id) & (edges.u != edges.v)].reset_index(drop=True)
    index = {osm_id: i for i, osm_id in enumerate(pd.unique(np.concatenate([edges.u.values, edges.v.values])))}
    u = edges.u.map(index).to_numpy()
    v = edges.v.map(index).to_numpy()
    keep_node = largest_component(u, v, len(index))
    keep_edge = keep_node[u] & keep_node[v]
    # renumber the kept nodes 0..N-1
    new_id = np.cumsum(keep_node) - 1
    node_xy = nodes.drop_duplicates("id").set_index("id").loc[list(index)][["lon", "lat"]].to_numpy()
    out_nodes = pd.DataFrame({"node": np.arange(keep_node.sum()), "lon": node_xy[keep_node, 0], "lat": node_xy[keep_node, 1]})

    rows = []
    for ui, vi, highway, geom in zip(u[keep_edge], v[keep_edge], edges.highway[keep_edge], edges.geometry[keep_edge]):
        coords = [(x, y) for x, y, *_ in geom.coords]
        rows.append(dict(u=int(new_id[ui]), v=int(new_id[vi]), length_m=polyline_length_m(coords), highway=highway,
                         cells=cells_along(coords, h3_res, step_m), coords=[c for p in coords for c in p]))
    return out_nodes, pd.DataFrame(rows), dict(osm_edges=len(edges), dropped_by_component=int((~keep_edge).sum()))


# ---------- Command line ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="city_ma_boston_big", help="area name from the data-collector config (gives the bounding box)")
    ap.add_argument("--pbf", required=True, help="OpenStreetMap extract (.osm.pbf) covering the area")
    ap.add_argument("--margin", type=float, default=0.004, help="degrees added around the bounding box (same as data-collector)")
    ap.add_argument("--network-type", default="cycling")
    ap.add_argument("--h3-res", type=int, default=10)
    ap.add_argument("--step-m", type=float, default=40.0)
    ap.add_argument("--out", help="output folder; default ../api-service/data/graphs/<city>")
    a = ap.parse_args()

    w, s, e, n = bbox_of(a.city)
    bbox = [w - a.margin, s - a.margin, e + a.margin, n + a.margin]
    print(f"{a.city}: bbox {bbox}, reading {a.pbf} (this takes a few minutes)", flush=True)
    nodes, edges = read_network(a.pbf, bbox, a.network_type)
    print(f"OSM: {len(nodes):,} nodes, {len(edges):,} segments; highway types: {edges.highway.value_counts().to_dict()}", flush=True)

    out_nodes, out_edges, info = build_graph(nodes, edges, a.h3_res, a.step_m)
    out = Path(a.out or Path(__file__).resolve().parents[1] / "api-service" / "data" / "graphs" / a.city)
    out.mkdir(parents=True, exist_ok=True)
    out_nodes.to_parquet(out / "nodes.parquet", index=False)
    out_edges.to_parquet(out / "edges.parquet", index=False)
    print(f"graph: {len(out_nodes):,} nodes, {len(out_edges):,} edges, {out_edges.length_m.sum() / 1000:.1f} km; {info}")
    print(f"wrote {out}/")


if __name__ == "__main__":
    main()
