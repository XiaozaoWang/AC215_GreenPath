# Route planning

Route planning turns a start point, an end point and a free-text description of the ride ("a shady ride away from traffic")
into a set of cycling routes: the shortest route and three options that trade extra length for a better fit with what the
rider asked for. It is part of the API service (`src/api-service`) and is shown by the frontend (`src/frontend`).

**Contents**
[1 Overview](#1-overview) ·
[2 System overview](#2-system-overview) ·
[3 API reference](#3-api-reference) ·
[4 How a route is planned](#4-how-a-route-is-planned) ·
[5 Streaming and progress](#5-streaming-and-progress) ·
[6 Frontend](#6-frontend) ·
[7 Data and configuration](#7-data-and-configuration) ·
[8 Performance and capacity](#8-performance-and-capacity) ·
[9 Testing](#9-testing) ·
[10 Limitations](#10-limitations)

---

## 1. Overview

| | |
|---|---|
| **Input** | Start and end coordinates, and a text describing the ride |
| **Output** | The shortest route, plus three options named `efficient`, `balanced` and `preferred`. Each has its geometry, length, extra length over the shortest route, a *match* score (how well it fits the ride) and the average scenery values along it |
| **Recommendation** | One of the options is recommended: a mild wish recommends `efficient`, a strong wish `preferred` |
| **Supported area** | The area covered by the road graph and by downloaded aerial imagery: Boston, about 5.8 km × 5.6 km (longitude −71.1224 to −71.0522, latitude 42.3309 to 42.3815) |

Properties of the result:

- **The three options are clearly different.** Any two routes (and every option versus the shortest route) run along each
  other for at most 70 % of their length, where streets closer than 60 m count as the same street. Their lengths differ by at
  least 3 % of the shortest route.
- **A longer option fits better.** Sorted by length, the options also rise in match, so no option is both longer and a worse fit
  than another one.
- **The allowed extra length is dynamic.** The search starts with at most +50 % over the shortest route and widens to +75 % and
  +100 % until three options exist.
- **Three options whenever the road network allows.** When no route fits the ride better, routes on different streets are
  offered instead and marked `alternative`. Very short trips in a tight street grid may have fewer than three different routes.
- **A route is a path on real roads.** The search runs on a bicycle road graph built from OpenStreetMap, so every route follows
  existing cycling-accessible roads.

Terms used throughout:

| Term | Meaning |
|---|---|
| **Cell** | An H3 cell at resolution 10 (about 130 m across). The model predicts 15 scenery scores per cell from its aerial image |
| **Scenery dimension** | One of 15 scores in 0–1: `tree_canopy`, `forest_environment`, `grassland`, `open_landscape`, `road_width`, `water_access`, `urban_density`, `street_enclosure`, `farmland_pastoral`, `industrial_area`, `historic_character`, `commercial_district`, `scenic_appeal`, `architectural_character`, `visual_clutter` |
| **Preference weights** | One number per dimension in −1…1 (1 = wants much more, −1 = wants much less, 0 = does not care) |
| **Mismatch** | How badly a cell or road fits the ride, 0 (best) to 1 (worst) |
| **Match** | `1 − mismatch`, averaged along a route weighted by length: 1 = every metre fits, 0 = none does |
| **Detour / extra length** | `route length / shortest length − 1` (0.25 = 25 % longer) |
| **Detour limit** | The largest detour a route may have in the current search round |

---

## 2. System overview

```
Browser ──► Next.js (frontend) ──► API service ──┬──► Model Service (Cloud Run): 15 scores per cell
  tap start/end        proxy adds an ID token    │
  describe the ride                              ├──► Gemini (Vertex AI): text → preference weights
  ◄── progress events, routes ◄──────────────────┘
                                                 └──► road graph + area baseline (files in the image)
```

| Component | Where | Role |
|---|---|---|
| Road graph | `src/api-service/data/graphs/<region>/{nodes,edges}.parquet` | Roads as nodes and edges; each edge knows the H3 cells it passes through |
| Area baseline | `src/api-service/data/baselines/<region>.parquet` | Predicted scores of the cells of the region, used as the yardstick for "good" and "poor" |
| `road_graph.py` | `src/api-service/api/` | Loading the graph, snapping points, shortest paths, the detour ellipse |
| `route_search.py` | `src/api-service/api/` | Preference to mismatch, road costs, candidate routes, choosing and naming the options |
| `baseline.py` | `src/api-service/api/` | Loading the baseline |
| `routing.py` | `src/api-service/api/` | Request and response models, orchestration of Gemini, the Model Service and the search, event stream |
| `routers/route.py` | `src/api-service/api/` | HTTP endpoints |
| `src/routing-graph/` | offline tool | Builds the road graph for an area |
| Frontend | `src/frontend/src/` | Map, route cards, explanation panel, proxy routes `/api/route/plan` and `/api/route/stream` |

The graph and the baseline are plain files inside the API image: nothing is downloaded at run time, and no separate service
is needed.

---

## 3. API reference

### 3.1 Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /route/plan` | Instant answer for a start and an end: the shortest route and the cells that will be scored (nearest to the route first). No text needed |
| `POST /route/stream` | Plans the options for a text and reports progress as newline-delimited JSON events. This is what the app uses |
| `POST /route` | Plans the options and returns everything in one JSON response |

FastAPI also publishes the schemas at `/docs` (OpenAPI).

### 3.2 Request

```json
{
  "origin":      {"lat": 42.3467, "lon": -71.0972},
  "destination": {"lat": 42.3588, "lon": -71.0707},
  "text":        "a shady, quiet ride away from traffic",
  "region_id":   "city_ma_boston_big"
}
```

| Field | Notes |
|---|---|
| `origin`, `destination` | Latitude −90…90, longitude −180…180 |
| `text` | Up to 2,000 characters. Optional for `/route` and `/route/stream`: without text only the shortest route is returned, and neither Gemini nor the Model Service is called. Not used by `/route/plan` |
| `region_id` | Default `city_ma_boston_big`. Selects the road graph and baseline |

### 3.3 Response of `/route` (and the `result` event)

| Field | Meaning |
|---|---|
| `routes` | The shortest route first (`kind: "shortest"`), then the options in the order `efficient`, `balanced`, `preferred` (fewer than three only if the road network has no more different routes) |
| `recommended` | The `kind` to show first: `efficient`, `balanced`, `preferred` or `shortest` (no preference) |
| `geometry`, `distance_m` | The recommended route, for clients that show one route |
| `weights` | What was understood from the text: `[{dimension, reason, weight}]`, only non-zero weights |
| `must_have` | Dimensions the rider insists on (a cell must reach a minimum score in them) |
| `intensity` | The largest absolute weight, 0–1: how strongly the rider wants it |
| `used_fallback` | `true` when Gemini failed three times; the weights are then empty and only the shortest route is returned |
| `candidates` | Every distinct route found during the search: `{detour, match, kind}` (`kind` is set for routes that were offered). Shows the length / match trade-off |
| `max_detour` | The detour limit the search ended with (0.5, 0.75 or 1.0) |
| `improved` | `true` when at least one option fits the ride better than the shortest route |
| `area_mean` | Area-wide mean score of each dimension the rider cares about, for comparison |
| `cells_scored`, `cells_without_score` | Cells examined, and how many of them have no prediction (no usable imagery) |
| `timings_ms` | Time spent in `prepare`, `parse_and_predict`, `search`, `response` |

Each entry of `routes`:

| Field | Meaning |
|---|---|
| `kind` | `shortest`, `efficient`, `balanced` or `preferred` |
| `geometry` | GeoJSON `LineString`; coordinates are `[longitude, latitude]` |
| `distance_m` | Length in metres |
| `detour` | Extra length over the shortest route, as a fraction |
| `match` | 0–1, see [4.5](#45-from-scores-to-mismatch-and-match) |
| `alternative` | `true` when another route (the shortest one included) is at least as short and fits at least as well: the route only offers other streets |
| `profile` | Mean predicted score along the route for each dimension the rider cares about |

`/route/plan` returns `{region_id, shortest, cells}`: `shortest` has the same fields as a route (`profile` is empty), `cells`
lists the H3 cells of the first detour limit, nearest to the shortest route first.

### 3.4 Events of `/route/stream`

`Content-Type: application/x-ndjson`, one JSON object per line.

| Event | Fields | When |
|---|---|---|
| `start` | `cells_total` | Immediately (only when there is a text) |
| `preference` | `weights`, `must_have`, `intensity`, `used_fallback` | When Gemini has answered |
| `cells` | `values`: `[[h3_index, match], ...]` | For every batch of cells the Model Service finishes. `match` is 0 (poor) to 1 (good), `null` when the cell has no score. A batch waits for the preference, because colours depend on it |
| `expand` | `max_detour`, `cells_added` | When the search widens the detour limit; the new cells are scored afterwards |
| `result` | the `/route` response | At the end |
| `error` | `status`, `detail` | Instead of `result` when something fails after the stream has started |

Without an area baseline file, the `cells` event is sent once, at the end, with values relative to the cells of the request.

### 3.5 Errors

Errors caused by the input are plain HTTP errors sent before any event.

| Status | Cause |
|---|---|
| 404 | Unknown region |
| 422 | Invalid body; start or end more than 500 m from the road graph (outside the supported area); start and end not connected |
| 502 / 504 | The Model Service failed / timed out (on `/route/stream` after the start: an `error` event with the same status) |

### 3.6 Examples

```bash
# Shortest route and the cells that will be scored
curl -s -X POST localhost:9000/route/plan -H 'Content-Type: application/json' \
  -d '{"origin":{"lat":42.3467,"lon":-71.0972},"destination":{"lat":42.3588,"lon":-71.0707}}'

# Options for a text, with progress events
curl -s -N -X POST localhost:9000/route/stream -H 'Content-Type: application/json' \
  -d '{"origin":{"lat":42.3467,"lon":-71.0972},"destination":{"lat":42.3588,"lon":-71.0707},"text":"a shady, quiet ride"}'

# The deployed API is private: add an identity token
curl -s -X POST "$API_URL/route/plan" -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  -H 'Content-Type: application/json' \
  -d '{"origin":{"lat":42.3467,"lon":-71.0972},"destination":{"lat":42.3588,"lon":-71.0707}}'
```

Shape of a `/route` response (abridged):

```json
{
  "routes": [
    {"kind": "shortest",  "distance_m": 2949.6, "detour": 0.0,   "match": 0.48, "alternative": false, "profile": {"tree_canopy": 0.52}, "geometry": {"type": "LineString", "coordinates": [[-71.0976, 42.3471], "..."]}},
    {"kind": "efficient", "distance_m": 3222.9, "detour": 0.093, "match": 0.73, "alternative": false, "profile": {"tree_canopy": 0.64}, "geometry": "..."},
    {"kind": "balanced",  "distance_m": 3646.1, "detour": 0.236, "match": 0.77, "alternative": false, "profile": {"tree_canopy": 0.65}, "geometry": "..."},
    {"kind": "preferred", "distance_m": 4089.6, "detour": 0.387, "match": 0.80, "alternative": false, "profile": {"tree_canopy": 0.68}, "geometry": "..."}
  ],
  "recommended": "preferred",
  "weights": [{"dimension": "tree_canopy", "reason": "direct wish for shade", "weight": 1.0}],
  "must_have": [], "intensity": 1.0, "used_fallback": false,
  "max_detour": 0.5, "improved": true, "area_mean": {"tree_canopy": 0.47},
  "cells_scored": 355, "cells_without_score": 0,
  "timings_ms": {"prepare": 71.8, "parse_and_predict": 5.6, "search": 119.8, "response": 15.9}
}
```

---

## 4. How a route is planned

```
 1 snap start and end to the road graph; shortest route
 2 detour ellipse: the roads that can lie on a route up to (1 + limit) × the shortest length
 3 cells under those roads, nearest to the shortest route first ───────────────┐
 4 in parallel:  Gemini: text → weights                                       ├─ both start at once
                 Model Service: 15 scores per cell                            │
 5 per cell: scores + weights → mismatch (0 good … 1 poor) ◄──────────────────┘
 6 per road: cost = length × (1 + s × mismatch);  shortest path for 25 values of s → candidate routes
 7 choose three clearly different options spread over the allowed extra length
 8 fewer than three?  widen the limit (+50 % → +75 % → +100 %), score only the new cells, repeat from 5
 9 still fewer?  fill up with routes on different streets
```

### 4.1 Road graph

- Built offline from the OpenStreetMap **cycling network** (see [7.1](#71-road-graph)). Nodes are junctions, edges are road
  segments with their polyline and length in metres.
- The graph is **undirected** and holds only the largest connected component, so any two nodes are connected.
- For every edge the graph stores the **H3 cells** its polyline passes through (sampled every 40 m, in travel order). This is the
  link between roads and scenery: a road's score is read from the cells under it.
- Boston (`city_ma_boston_big`): 49,102 nodes, 53,347 edges, 936 km of roads, 2,568 cells under roads.
- Start and end are **snapped** to the nearest graph node (flat-earth distance in metres). A point farther than 500 m from the
  graph is outside the supported area (HTTP 422).

### 4.2 Shortest route and detour limit

- The **shortest route** is the shortest path by length (Dijkstra, scipy). It is the baseline every option is compared with.
- The **detour ellipse** for a limit `L` is the set of edges `e = (u, v)` with
  `min(d_s(u) + len(e) + d_t(v), d_s(v) + len(e) + d_t(u)) ≤ (1 + L) × length(shortest)`,
  where `d_s` and `d_t` are shortest distances from the start and to the end. Every route up to `1 + L` times the shortest
  length uses only such edges, and computing the set needs no scores: two Dijkstra runs and a vector comparison.
- All later searches run on the edges of the ellipse only, which keeps both the search and the number of cells to score small.
- The first limit is `L = 0.5`. It can widen to 0.75 and 1.0 ([4.8](#48-widening-the-limit-and-filling-up)).

### 4.3 Which cells are scored

- The cells to score are those under the ellipse edges: for a 3 km trip about 450 cells at +50 % and about 1,000 at +100 %.
- They are **ordered by distance from the cell centre to the shortest route** (nearest first). The Model Service receives them
  in that order, so the first batches describe the surroundings of the route and the map fills outwards from it.
- Cells depend only on geometry, not on the text, so the Model Service is called **at the same time** as Gemini.
- The API client splits the cells into at most 30 contiguous, equally sized batches (each at most 2,000 cells) and sends them
  concurrently (`MODEL_MAX_CONCURRENCY`). Each result is either a set of 15 scores or `null` for a cell without usable imagery
  (`not_downloaded`, `no_naip_coverage`, `mostly_black`). Cells without scores are treated as neutral in the search ([4.5](#45-from-scores-to-mismatch-and-match)).

### 4.4 Understanding the ride

- Gemini (Vertex AI) reads the text and returns **preference weights** over the 15 dimensions, each in −1…1, with a short
  reason, plus a list of `must_have` dimensions. The prompt (`api/prompts.py`) gives it the meaning of each dimension and
  how strongly to weigh wishes: about 1.0 for a clear wish, about 0.6 for a softened one ("a bit", "if possible"), about 0.3
  for a passing mention or an association.
- The answer is validated (names, range, no duplicates). A rejected answer is retried with the error message, up to 3 attempts.
  If all fail, the weights are all zero and `used_fallback` is `true`: only the shortest route is returned.
- The weights are split into two parts:

| Part | Definition | Used for |
|---|---|---|
| **Direction** | `weights / max|weights|` (the largest weight becomes ±1) | Which places fit: it defines the mismatch of every cell |
| **Intensity** | `max|weights|` (0–1) | Only to choose which option is recommended |

  Scaling all weights by the same factor changes only the intensity, never the routes.

### 4.5 From scores to mismatch and match

For every cell with scores `x` (15 values):

1. **Standardize against the area baseline.** For each dimension, `z = clip((x − mean) / σ, −3, 3)`, where `mean` and `σ` come from the
   baseline cells of the region and `σ` has a floor of 0.05 (so that dimensions that barely vary in the area are not blown up).
2. **Weighted sum.** `t = Σ direction_d × z_d`. A higher `t` means a better fit.
3. **Stretch to 0–1.** `lo` and `hi` are the 10th and 90th percentile of `t` over the baseline cells. The mismatch is
   `1 − clip((t − lo) / (hi − lo), 0, 1)`: a cell as poor as the worst 10 % of the area has mismatch 1, one as good as the best
   10 % has mismatch 0. (If `hi − lo` is practically zero, every mismatch is 0.)
4. **Must-have.** If a `must_have` dimension has a raw score below 0.1, the cell's mismatch is 1.

Because the yardstick is the whole area (not just the cells of this request), a cell's value depends only on its own scores and
the preference. It does not change while other cells are still being scored, and it means the same for every trip.

Then:

- **Road mismatch** = mean mismatch of the cells under the road. A cell without a prediction counts as 0.5.
- **Route match** = `1 − Σ(length_e × mismatch_e) / Σ length_e`, over the edges of the route.
- Match is **relative to the area**: 0 means "as poor as the poorest tenth of the area", not an absolute quality. A preference
  that few places satisfy (for example water) gives low match to most routes.

### 4.6 Road costs and candidate routes

- Each road gets the cost **`length × (1 + s × mismatch)`**. With `s = 0` the cost is the length; with a large `s` a road with
  mismatch 1 costs `1 + s` times its length, so the search accepts a long detour to stay on roads that fit.
- The search runs the shortest path on the ellipse for **25 values of `s`**: `0.25 × 2^(k/2)`, k = 0…24, from 0.25 to 1024.
  The steps are small because a route is the cheapest one only inside a narrow range of `s`; large values are needed for
  sparse preferences.
- Identical routes are merged. The result is a set of **candidate routes** with a length and a match each.
- Routes within the limit that are better than the shortest one by more than 0.005 in match are the **improving** candidates.

### 4.7 Choosing the three options

The options are chosen from the improving candidates, in three steps of increasing allowed extra length:

| Option slot | Allowed extra length |
|---|---|
| 1 | up to 20 % of the detour limit (+10 % when the limit is +50 %) |
| 2 | up to 50 % of the detour limit (+25 %) |
| 3 | up to 100 % of the detour limit (+50 %) |

For each slot the candidate with the **best match** within its allowed length is taken, provided it is acceptable next to the
routes already taken (and the shortest route):

| Rule | Definition |
|---|---|
| **Different streets** | Overlap below 70 %. Overlap of two routes = the share of the shorter route (sampled every 25 m) that lies within 60 m of the other route |
| **Different lengths** | At least 3 % of the shortest length apart |
| **Staircase** | The longer of two options must also have the higher match. A route that is longer and no better than another option is never offered |

If the slots give fewer than three routes (several slots picked the same one, or the rules reject the rest), more candidates are added
one by one, each time the acceptable candidate whose length is farthest from the routes already chosen.

### 4.8 Widening the limit and filling up

- If fewer than three options exist and a preference is present, the detour limit widens to the next value of **0.5 → 0.75 → 1.0**.
  The new ellipse contains the old one; only the **new cells** are sent to the Model Service, and the search is repeated.
  An `expand` event reports each step.
- At the **last** limit (+100 %), if there are still fewer than three, routes are added in this order, preferring spread-out
  lengths (3 % apart) and then accepting any length:
  1. Other improving candidates that the rules above rejected only for being beaten by a chosen option.
  2. **Alternatives by the penalty method**: shortest paths with cost `length × (1 + 8 × mismatch)` multiplied by `factor ^ n` on every
     road used by `n` of the routes taken so far. The factor goes through 1.1, 1.2, 1.35, 1.5, 2, 3, 5, 10; the gentlest factor
     that gives a route inside the limit and different enough (overlap below 70 %) is kept, so the alternative stays as short and
     as close to the preference as possible.
  3. As a last resort the overlap limit relaxes to 85 %.
- Routes added this way fit the ride no better than the shortest route or are beaten by another option; the response marks them
  `alternative: true`. When no route fits the ride better at all, `improved` is `false` and all three options are alternatives.

### 4.9 Naming and recommending

- Names describe the role of each option: the one with the **best match** is `preferred`, the one adding the **least length** is
  `efficient`, the remaining one is `balanced`. With two options they are `efficient` and `preferred`; a single option is `balanced`.
- The **recommended** option follows the intensity: up to 0.4 → `efficient`, up to 0.8 → `balanced`, above 0.8 → `preferred`
  (the closest available option when the wanted one does not exist). Without a preference the recommendation is the shortest route.

### 4.10 Data for explaining a route

The response carries what a client needs to show *why* a route was chosen:

- `profile` per route and `area_mean`: the average predicted value of every dimension the rider cares about along the route,
  next to the area average.
- `candidates`: every route that was found, which are the options, and the trade-off between length and match.
- `weights` and `intensity`: what was understood from the text.
- `max_detour` and `improved`: how far the search had to go, and whether anything fitted better than the shortest route.

---

## 5. Streaming and progress

The API runs Gemini and the Model Service concurrently and reports progress while they work.

```
time ─────────────────────────────────────────────────────────────────────────────────►
start        preference         cells  cells  cells ...        expand        cells ...        result
 │               │                │      │      │                │              │               │
 │   Gemini ─────┘                │      │      │                │              │               │
 │   Model Service batches ───────┴──────┴──────┴────────────────┘              │               │
 └─ nearest cells first                                       only new cells ────┴───────────────┘
```

- The first event is sent immediately. A `cells` batch is coloured as soon as the preference is known (usually before the
  first batch is back), using the area baseline.
- Batches arrive in completion order, so the map fills from the shortest route outwards.
- When the detour limit widens, an `expand` event adds the new cells to the progress total, and their batches follow.
- The `result` event closes the stream. A failure after the stream has started is reported as an `error` event.
- Responses are not buffered (`Cache-Control: no-store`, `X-Accel-Buffering: no`), and the Next.js proxy passes the stream through.

---

## 6. Frontend

The app shows an iPhone-sized map with an explanation panel beside it.

| Step | What happens |
|---|---|
| Tap the map | Sets the start, then the end. A third tap starts over. A tap outside the dashed outline (the supported area) is refused with a hint |
| Both points set | `/route/plan` returns the shortest route at once; it is drawn and the map zooms to it. The cells that will be scored are known but not drawn yet |
| Describe the ride | The text field is enabled once the shortest route is shown |
| Submit | The stream starts. The hint reads "Calculating your best route…" with the stage: "Understanding your ride…", "Analyzing places… k/N", "Looking further (up to +75 % longer)…", "Finding your best routes…" |
| Cells arrive | Each batch is drawn and coloured: purple (poor match) to yellow (good match), the viridis scale; grey = no score. Only scored cells are drawn |
| Result | Four cards (Shortest, Efficient, Balanced, Best match) show length, extra length and match; a card marked "other streets" is an alternative. Tapping a card draws that route thick, the others thin |

Map details:

- **Basemaps**: satellite (Esri World Imagery) and street map (Esri World Street Map), switched by the Satellite / Map control.
- **Route colours**: shortest dark (dashed when other routes are shown), efficient amber, balanced orange, best match red, each with a
  white casing so it stands out on any colour. Start is a green dot, end a red dot.
- **Notice when nothing fits better**: "No route within +X % fits your ride better. These are other streets to choose from".

**Explanation panel** (beside the phone):

1. Four steps with live status: shortest route, understanding the ride (the weights as chips), reading the satellite images
   (progress), finding the best trade-off.
2. For the selected route: a sentence on its role, the extra length and the match gain.
3. A chart **length against match**: every candidate route is a dot, the options are coloured, the line is the best match reachable
   within a given extra length.
4. Per dimension the rider cares about: the area average, the shortest route and the selected route on a 0–1 scale, with the
   direction the rider wants.

**Proxy.** The browser only talks to its own origin. Next.js route handlers (`/api/route`, `/api/route/plan`, `/api/route/stream`)
forward to the API with a Google ID token (the deployed API is private). The stream handler passes the response body through
without buffering.

---

## 7. Data and configuration

### 7.1 Road graph

`src/api-service/data/graphs/<region_id>/` holds two Parquet files, built once per area by `src/routing-graph/build_graph.py`:

| File | Columns |
|---|---|
| `nodes.parquet` | `node` (0…N−1), `lon`, `lat` |
| `edges.parquet` | `u`, `v` (node numbers), `length_m`, `highway` (OSM road type), `cells` (H3 ids in travel order), `coords` (flat `lon, lat, lon, lat, …`) |

```bash
cd src/routing-graph
python build_graph.py --city city_ma_boston_big --pbf massachusetts-240101.osm.pbf
```

`--city` is an area name from `src/data-collector/configs/1026_Full_data.yaml` (its bounding box, plus a margin of 0.004°, selects
the network); `--pbf` is an OpenStreetMap extract that covers it. Details: `src/routing-graph/README.md`. The Boston graph is
committed (3.6 MB).

The graph is loaded the first time a region is requested (about 2 s for Boston) and kept in memory (about 220 MB).

### 7.2 Area baseline

`src/api-service/data/baselines/<region_id>.parquet`: one row per cell with `h3_index` and the 15 dimension scores, from the final
model's predictions over the cells of the region (Boston: 2,009 cells). It defines "good" and "poor" for the area ([4.5](#45-from-scores-to-mismatch-and-match)).
Without a baseline file the search still works, with values relative to the cells of each request, and colours are sent once at
the end instead of per batch.

### 7.3 Adding another area

1. The area must have downloaded aerial imagery and be covered by the Model Service (it only scores cells it has imagery for).
2. Build the road graph for it and put the output in `data/graphs/<region_id>/`.
3. Save the predicted scores of the region's cells as `data/baselines/<region_id>.parquet`.
4. Update the supported-area outline in `src/frontend/src/app/page.jsx` (`AREA`) and the default `region_id` where needed.

### 7.4 Environment variables

| Variable | Used by | Meaning |
|---|---|---|
| `MODEL_SERVICE_URL` | API | Base URL of the Model Service (`http://localhost:8080` through `gcloud run services proxy`, or its Cloud Run URL) |
| `MODEL_MAX_CONCURRENCY` | API | Concurrent batch requests to the Model Service, 1–30 (default 3; the deployment tool passes its own value) |
| `GCP_PROJECT`, `GEMINI_MODEL`, `GEMINI_LOCATION` | API | Vertex AI project, model and location for Gemini (`gemini-3.1-flash-lite`, `global`); authentication is your Google login, no API key |
| `LLM_PROVIDER` | API | `gemini` (default) or `mock` (offline keyword substitute for tests and demos) |
| `ROUTE_GRAPH_DIR`, `ROUTE_BASELINE_DIR` | API | Alternative folders for the graph and baseline files |
| `API_SERVICE_URL` | frontend | Where the Next.js proxy forwards to (default `http://localhost:9000`) |

### 7.5 Tunable constants (`api/route_search.py`)

| Constant | Value | Meaning |
|---|---|---|
| `LADDER` | 0.5, 0.75, 1.0 | Detour limits tried in turn |
| `TIER_FRACTIONS` | 0.2, 0.5, 1.0 | Allowed extra length of the three option slots, as a fraction of the limit |
| `SENSITIVITIES` | 0.25 … 1024 (25 values) | The `s` values of the search |
| `MAX_OVERLAP`, `RELAXED_OVERLAP` | 0.7, 0.85 | How far two routes may run along each other (the second is the last resort) |
| `NEAR_M` | 60 m | Streets closer than this count as the same street |
| `MIN_LENGTH_STEP` | 0.03 | Minimum length difference between two options, as a fraction of the shortest length |
| `MIN_GAIN` | 0.005 | Match a route must gain over the shortest to count as improving |
| `PENALTIES`, `ALTERNATIVE_SENSITIVITY` | 1.1 … 10, 8 | Penalty method for alternatives |
| `MISSING_MISMATCH` | 0.5 | Mismatch of a cell without prediction |
| `MAX_SNAP_M` | 500 m | Farthest a point may be from the graph |

Scoring constants are in `api/score_math.py`: standard-deviation floor 0.05, z-score clip ±3, must-have minimum 0.1.

---

## 8. Performance and capacity

Measured on a development laptop with synthetic scores, and on the deployed Model Service.

| Step | Time |
|---|---|
| Load the road graph (first request only) | about 2 s |
| Prepare (snap, shortest route, ellipse) | about 70 ms |
| Search per request (one to three rounds) | 0.1–1.3 s |
| Memory after loading graph and baseline | about 220 MB |
| Model Service, a warm batch of 10–15 cells | 0.4–1.5 s |
| Model Service, cold start (no running instance) | about 40 s for the first batches |

- **The Model Service dominates the latency.** The CPU Model Service handles about 6 cells per second per instance, one request per
  instance, up to 30 instances. A 355-cell trip is 30 batches of about 12 cells. A long trip (about 6 km) has about 1,800 cells at
  +50 %, and a wider limit roughly doubles it.
- Cloud Run does not always start one instance per concurrent request; in measurements 30 simultaneous batches were served by
  10–16 instances, so some batches queue while new instances start.
- Scores are not stored: every request asks the Model Service again for the cells it needs (only the new ones when the limit widens).
- The deployed API runs with 1 CPU, 1 GiB and a concurrency of 5 per instance, with a 900 s request timeout.

---

## 9. Testing

| Tests | Where | What they cover |
|---|---|---|
| `tests/test_route_search.py` | api-service | Shortest paths and parallel edges; the detour ellipse; mismatch (baseline, must-have, unscored cells); edge mismatch; candidate routes; the three options (staircase, different streets, lengths); the overlap measure including parallel streets; alternatives; the recommendation rule |
| `tests/test_route.py` | api-service | `/route`, `/route/plan`, `/route/stream` with the Model Service and Gemini replaced by fakes: three different options, widening and only new cells predicted, event order, colours per batch, errors and status codes, the real Boston graph |
| `tests/test_model_client.py` | api-service | Batching, ordering, the per-batch callback |
| `tests/api-proxy.test.mjs` | frontend | The proxy, including the unbuffered stream and error forwarding |
| `tests/test_build_graph.py` | routing-graph | Graph-building helpers |

```bash
cd src/api-service && uv run pytest        # backend
cd src/frontend && npm test                # proxy and stream reader
```

The tests use two toy graphs: a small one for the basic algorithms and a "fan" of five separate routes between two points
whose scenery grows with the length, which makes the selection, the widening and the alternatives exactly predictable.

---

## 10. Limitations

- **Road network.** The graph is undirected: one-way streets, turn restrictions and bike-specific rules (lanes, surface, traffic
  signals) are ignored, and all cycling-accessible road types are treated alike. Elevation is not considered.
- **Area.** Only the area with downloaded aerial imagery can be scored (Boston, about 5.8 km × 5.6 km). Cells in it without usable
  imagery count as neutral.
- **Scores are predictions.** They come from one model with an error of about 0.07 on unseen places, and the labels it learned
  from are Gemini scores. A low match is relative to the area ([4.5](#45-from-scores-to-mismatch-and-match)), not an absolute rating.
- **Sparse preferences.** When few places satisfy a wish (water, farmland), most routes get a low match and no route may fit
  better than the shortest one. The options are then alternatives on other streets, marked as such.
- **The search is not exhaustive.** The cost-weighted search finds routes that are cheapest for some `s`; the penalty method
  complements it. The three options are good, clearly different routes, not provably the best three.
- **Fewer than three options** are possible for very short trips in a tight street grid.
- **Gemini.** If it fails, only the shortest route is returned (`used_fallback`). The wording of the text decides the intensity: a
  direct wish gets weight about 1, so the recommendation is `preferred` for most direct wishes.
- **Latency.** The first request after a quiet period waits for Model Service instances to start (about 40 s). Progress is shown
  while it runs.
