# Route planning

Given a start, an end and a free-text description of the ride, the API returns the shortest route plus **three options**
that differ clearly from each other and from the shortest route. The allowed extra length is not fixed: it widens
(+50 %, +75 %, +100 %) until three such routes exist.

| Endpoint | Use |
|---|---|
| `POST /route/plan` | Instant: the shortest route and the H3 cells that will be predicted (nearest to the route first). Needs no text |
| `POST /route/stream` | Plan with a text and report progress as newline-delimited JSON events (what the web app uses) |
| `POST /route` | The same work as one JSON response |

## Request and response

```json
{"origin": {"lat": 42.3736, "lon": -71.1190}, "destination": {"lat": 42.3550, "lon": -71.0655}, "text": "a shady ride away from traffic"}
```

`/route/plan` takes the same body without `text` and returns `{region_id, shortest, cells}`.

| `/route` response field | Meaning |
|---|---|
| `routes` | The shortest route first, then the options `efficient`, `balanced`, `preferred`. Each has `kind`, `geometry` (GeoJSON LineString, `[lon, lat]`), `distance_m`, `detour` (fraction over the shortest), `match` (0 to 1), `alternative` (true when another route is at least as short and fits at least as well: it only offers other streets) and `profile` (mean predicted score along the route for each dimension the user cares about) |
| `recommended` | Which `kind` to show first. A mild wish recommends `efficient`, a strong one `preferred` |
| `geometry`, `distance_m` | The recommended route, for clients that show a single route |
| `weights`, `must_have`, `intensity` | What was understood from the text (`intensity` is the largest absolute weight) |
| `candidates` | Every distinct route found (`detour`, `match`, and `kind` when it was offered): the length / match trade-off |
| `max_detour`, `improved` | The extra length the search ended up allowing; whether any option fits the preference better than the shortest route |
| `area_mean` | Area-wide mean score of each dimension the user cares about |
| `cells_scored`, `cells_without_score`, `timings_ms` | Diagnostics |

Without `text`, only the shortest route is returned and neither Gemini nor the Model Service is called.
Errors: 404 unknown region, 422 start/end outside the road graph or not connected, 502/504 Model Service failure.

### Stream events (`/route/stream`)

One JSON object per line, `Content-Type: application/x-ndjson`. Input errors (404, 422) are plain HTTP errors sent before
the stream starts; later failures arrive as an `error` event because the HTTP status is already sent.

| Event | Fields | When |
|---|---|---|
| `start` | `cells_total` | Immediately |
| `preference` | `weights`, `must_have`, `intensity`, `used_fallback` | When Gemini has answered |
| `cells` | `values`: `[[h3_index, match], ...]`, match 0 = poor, 1 = good, `null` = no score | For each batch the Model Service finishes (nearest cells first); colours need the preference, so a batch waits for it |
| `expand` | `max_detour`, `cells_added` | When the search widens the allowed extra length to find more options; only the new cells are predicted afterwards |
| `result` | the `/route` response | At the end |
| `error` | `status`, `detail` | Instead of `result` on failure |

## How it works

1. **Prepare** (`route_search.prepare`, about 70 ms): snap both points to the road graph, find the shortest route, and mark
   every road that can lie on a route at most 50 % longer (the detour ellipse). This needs no preference and no scores.
2. **In parallel**: Gemini parses the text into 15 weights in [-1, 1]; the Model Service predicts the 15 scores of the
   H3 cells under the ellipse (the cells depend only on the geometry, so this starts at once).
3. **Search** (`route_search.search`, about 100 ms per round):
   - The weights are split into a direction (largest weight scaled to 1) and an intensity (the largest absolute weight).
   - Each cell gets a mismatch in [0, 1] from the direction only: standardize against the area baseline, take the weighted
     sum, stretch so the 10th percentile is 1 and the 90th is 0. Cells that fail a must-have are 1.
   - Edge cost = `length x (1 + s x mismatch)`. Dijkstra runs for 25 sensitivities `s` (0.25 to 1024, steps of sqrt 2); identical
     routes are merged.
   - **Three options** are chosen from the routes that fit better than the shortest one: the best within 20 %, 50 % and 100 % of the
     maximum detour. They run along each other (streets within 60 m count as the same street) for at most 70 % of their length, also relative to the shortest route, and differ in length by
     at least 3 % of the shortest route, and each longer one fits better than the shorter ones. Named by role: the best match is
     `preferred`, the one adding the least length `efficient`, the other `balanced`.
   - **Dynamic maximum detour**: if fewer than three such routes exist, the allowed extra length widens (`route_search.LADDER`:
     +50 %, +75 %, +100 %) and only the new cells are predicted. At the last step the options are filled up, first with other
     routes that fit better but are beaten by a chosen option, then with routes that merely differ (penalty method: roads
     already used cost more, gentlest penalty first). Such routes have `alternative: true`; when no route fits better at all
     (`improved: false`), all three options are alternatives. The graph may still hold fewer than three different routes
     (very short trips), then fewer are returned.
   - The intensity only picks which option is recommended; it does not change the routes.

Cells without a prediction (no imagery) count as neutral (mismatch 0.5) in the search and are `null` in the stream.

## Area baseline

`data/baselines/<region_id>.parquet`: `h3_index` plus the 15 predicted scores of the cells of the region (Boston: 2,009
cells, from the final model's saved predictions). Cells are standardized against it, so a cell's match value depends only
on its own scores and the preference, not on which other cells were predicted: colours do not shift while batches arrive.
Without a baseline file the stream sends all colours once at the end, relative to the cells of the request.
Set `ROUTE_BASELINE_DIR` to use another folder.

## Road graph

`data/graphs/<region_id>/{nodes,edges}.parquet`, built offline by `src/routing-graph/build_graph.py`, loaded lazily on the
first request (Boston: 3.6 MB of Parquet, about 2 s to load). Set `ROUTE_GRAPH_DIR` to use another folder.

## Known limits

- The graph is undirected: one-way streets and bike-specific rules are ignored.
- Only the area that has satellite images downloaded can be scored (Boston: about 5.8 km x 5.6 km); the Model Service does
  not fetch imagery itself.
- All predictions come from the Model Service, so a long route (about 6 km, +50 % ellipse) needs about 1,800 cells; a
  short one a few hundred. Latency is dominated by the Model Service (cold start about 40 s), not by the search.
- Per-dimension model RMSE is not available yet; the standardization uses a fixed floor of 0.05
  (`score_math.PLACEHOLDER_RMSE`).
