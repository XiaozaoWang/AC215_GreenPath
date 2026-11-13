# routing-graph

Offline tool that builds the bike road graph used by `POST /route` in `api-service`.
It is not part of any container: run it once per area and commit the two output files.

```sh
# From a Python environment with pyrosm, pandas, pyarrow, h3, scipy and pyyaml
python build_graph.py --city city_ma_boston_big --pbf massachusetts-240101.osm.pbf
```

- `--city`: an area name from `data-collector/configs/1026_Full_data.yaml` (gives the bounding box). `--pbf`: an OpenStreetMap extract covering it (e.g. the Geofabrik Massachusetts file).
- Output, by default in `src/api-service/data/graphs/<city>/`: `nodes.parquet` (`node`, `lon`, `lat`) and `edges.parquet` (`u`, `v`, `length_m`, `highway`, `cells`, `coords`).
  `cells` are the H3 res-10 cells the segment passes through, sampled every 40 m; `coords` is flat `lon, lat, lon, lat, ...`.
- Boston (`city_ma_boston_big`): 49,102 nodes, 53,347 edges, 936 km, 2,568 H3 cells under roads.
- Simplifications: the graph is undirected (one-way streets and bike rules are ignored) and only the largest connected component is kept.

Tests (pure helpers only): `python -m pytest tests` from this folder, with `h3`, `numpy`, `pandas` and `scipy` installed.
