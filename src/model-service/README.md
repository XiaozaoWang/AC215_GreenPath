# model-service

Private Cloud Run service `greenpath-model-service` (us-central1): send H3 cell ids, get 15 scenery scores (0 to 1) per cell. It reads the aerial tiles from the bucket itself.
api-service finds it through the `MODEL_SERVICE_URL` env var. The client that implements everything below is `src/api-service/api/model_client.py`.

## Endpoints

- `POST /predict` with `{"h3_indices": ["8a2802374807fff", ...]}`: 1 to 2000 H3 res-10 ids (more returns 422, duplicates are fine).
  Returns `{"run_id": "...", "results": [{"h3_index": "...", "status": "ok", "scores": {...}}, ...]}`, one result per id, in request order.
- `status`: `ok` (15 scores, 0 to 1, 4 decimals), `not_downloaded` (cell not in a collected area), `no_naip_coverage` / `mostly_black` (tile not usable). `scores` is `null` whenever status is not `ok`.
- `GET /health`: `{"status": "ok", "run_id", "training_id", "targets", "cells"}`. `targets` is the score order, `cells` the number of scorable cells.
- `POST /predict_image`: multipart upload of one 256x256 aerial PNG in field `file`, returns `{"run_id", "scores"}` (for testing).

Score names, in order: `tree_canopy, forest_environment, grassland, open_landscape, road_width, water_access, urban_density, street_enclosure, farmland_pastoral, industrial_area, historic_character, commercial_district, scenic_appeal, architectural_character, visual_clutter`.

## Access

The caller's service account (e.g. `greenpath-api-service@<project>.iam.gserviceaccount.com`) needs `roles/run.invoker` on the service:
`gcloud run services add-iam-policy-binding greenpath-model-service --region us-central1 --member=serviceAccount:<that account> --role=roles/run.invoker`
Every call sends an ID token from `google.oauth2.id_token.fetch_id_token` with audience = the service URL (on Cloud Run this uses the caller's service account, no key file).
Locally, use an `http://` URL and no token: `gcloud run services proxy greenpath-model-service --region us-central1 --port 8080`, or this service via `sh docker-shell.sh`, then `MODEL_SERVICE_URL=http://host.docker.internal:8080`.

## Speed and batching

- Each instance: 4 vCPU / 4 GiB, concurrency 1, about 6 cells/s. Up to 100 instances, 0 when idle.
- One request runs on one instance, so api-service splits a region into up to 30 batches of at least 10 cells and sends them at once (Boston, 2098 cells: 30 batches of about 70, about 12 s each).
- api-service runs one instance with a budget of 100 concurrent calls shared by all users, so 3 users get full speed (3 x 30 = 90 batches).
- Cold start takes about 40 s per instance (startup CPU boost is on). Cloud Run may reject calls to a cold service (500, 429, 503), so `model_client.py` retries 429/5xx after waiting 2, 4, 8, then 15 s, up to 8 retries and about 90 s of waiting per batch. Time spent in Cloud Run's queue does not count, because Cloud Run can hold a request for 2 minutes before rejecting it. Request timeout is 300 s.

## Run and deploy

`sh docker-shell.sh` builds and starts the service locally (try it at http://localhost:8080/docs). It needs `artifacts/`, made by `python cli.py --prepare` in `src/deployment`.
Deploy: `sh docker-build-push.sh`, then inside `src/deployment/docker-shell.sh` run `python cli.py --deploy --service model` and `python cli.py --test --service model [--local]`.
