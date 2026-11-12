# GreenPath — Milestone 3

Scenic cycling routes: every H3 cell (~130 m) gets 15 scenery scores (tree canopy, water access, historic character, …) predicted from a single aerial image.

## Repository layout

```
src/
├── data-collector/   Collect: H3 cells + aerial tiles (NAIP) + map features (OSM) + street view (Mapillary)
├── data-labeler/     Label: Gemini scores aerial tiles and street photos on 16 dimensions
├── data-processor/   Process: drop suspicious cells, calibrate street scores to the aerial scale, fuse into a training table
├── model-training/   Train: DINOv2-S + linear head, aerial tile in → 15 scores (0–1) out
├── workflow/         Vertex AI pipeline that chains the four steps above in the cloud
├── data-versioning/  DVC snapshots of each training set (table + aerial tiles), stored in gs://<bucket>/dvc_store/
├── model-service/    Model service on Cloud Run: H3 cells in → 15 scores out (FastAPI, CPU)
├── model-deployment/ Deploy tool: prepare the trained model, deploy model-service to Cloud Run, check its scores
├── api-service/      Backend API (FastAPI): preference parsing (Gemini), scoring, route planning
├── routing-graph/    Offline tool: build the road graph that route planning reads (its output is committed)
└── frontend/         Frontend (Next.js): map, route options, explanation of the chosen route
```

## Data pipeline (runs on Vertex AI)

```
collect → label → process → train
datasets/<DATASET>/  →  training/<TRAINING_ID>/  →  results/<TRAINING_ID>/{dry|final}/<run_id>/best_model.pt
```

- Each step is a container; all data lives in the GCS bucket, and steps hand off through files in the bucket.
- Collect, label, process: our own images, pushed to Artifact Registry with `docker-build-push.sh`.
- Train: Google's prebuilt training image; our code is packaged and uploaded to `gs://<bucket>/trainer/` by `model-training/package-trainer.sh`.
- To change the dataset name, training-set name, areas, or dry run vs. full training, edit only the constants at the top of `src/workflow/cli.py`: `DATASET`, `TRAINING_ID`, `AREAS`, `DRY_RUN`.

## Setup

1. Copy `.env.example` to `.env` (never commit it) and fill in `GREENPATH_BUCKET`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `MAPILLARY_TOKEN`.
2. `gcloud auth login` and `gcloud auth application-default login` (containers use your login credentials; no key file needed).
3. Push images (only after code changes): run `sh docker-build-push.sh` in `data-collector`, `data-labeler` and `data-processor`.
4. Upload the training code (only after it changes): `cd src/model-training && sh package-trainer.sh`.

## Run the pipeline

```
cd src/workflow && sh docker-shell.sh      # enter the workflow container
python cli.py --pipeline                   # collect → label → process → train
python cli.py --data_collector             # or one step: --data_labeler / --data_processor / --model_training
```

Monitor in the GCP console: Vertex AI → Pipelines (the whole run) and Training → Custom jobs (training logs).

## Version the training data (DVC)

After a pipeline run produces a new training set, snapshot it by hand (see `src/data-versioning/README.md`):

```
cd src/data-versioning && sh docker-shell.sh
python cli.py --training-id <TRAINING_ID>   # download the training table + the tiles it uses
dvc add greenpath_dataset && dvc push       # hash it and upload to gs://<bucket>/dvc_store/
```

Then commit `greenpath_dataset.dvc` and tag it `dataset_<TRAINING_ID>` on the Mac.

## Deploy the model (Cloud Run)

One-time GCP setup and the full step list: `src/model-deployment/README.md`.

```
cd src/model-deployment && sh docker-shell.sh
python cli.py --prepare        # copy the trained model into model-service/artifacts/
                               # then in src/model-service: sh docker-build-push.sh
python cli.py --deploy         # deploy model-service to Cloud Run (private)
python cli.py --test           # its scores must match the training-time predictions
```

## Run the app locally

```
docker compose up            # app http://localhost:3000, API docs http://localhost:9000/docs
```

Route planning needs the Model Service (private on Cloud Run) and Gemini (Vertex AI: your gcloud login, no API key). Compose reads `MODEL_SERVICE_URL` (default `http://host.docker.internal:8080`), so start the proxy first, and put the Gemini key in `../secrets/llm-service-account.json` (or set `LLM_PROVIDER=mock`). Without Docker, in three terminals:

```
gcloud run services proxy greenpath-model-service --region us-central1 --port 8080    # 1: keep running
cd src/api-service && GCP_PROJECT=<project-id> MODEL_SERVICE_URL=http://localhost:8080 MODEL_MAX_CONCURRENCY=30 \
  uv run uvicorn api.service:app --port 9000                                          # 2: API
cd src/frontend && API_SERVICE_URL=http://localhost:9000 npm run dev                  # 3: app http://localhost:3000
```

The first request after a quiet period waits for the Model Service to start (about 40 s). Try the API without the app:

```
curl -s -X POST localhost:9000/route/plan -H 'Content-Type: application/json' \
  -d '{"origin":{"lat":42.3467,"lon":-71.0972},"destination":{"lat":42.3588,"lon":-71.0707}}'    # shortest route + cells
curl -s -N -X POST localhost:9000/route/stream -H 'Content-Type: application/json' \
  -d '{"origin":{"lat":42.3467,"lon":-71.0972},"destination":{"lat":42.3588,"lon":-71.0707},"text":"a shady, quiet ride"}'   # progress events, then the routes
```

Check `used_fallback` in the result: `true` means Gemini failed and only the shortest route is returned. Endpoints and options: `src/api-service/ROUTING.md`.

## Build the road graph (only for a new area)

```
cd src/routing-graph
python build_graph.py --city <area_id> --pbf <file>.osm.pbf    # writes src/api-service/data/graphs/<area_id>/
```

The Boston graph (`city_ma_boston_big`) is already in the repository.

## Test and redeploy

```
cd src/api-service && uv run pytest        # backend, including route planning
cd src/frontend && npm test                # proxy and stream reader
```

Deploy api-service and frontend with `src/app-deployment/README.md`; the road graph and baseline are inside the image. The deployed API is private: call it with `-H "Authorization: Bearer $(gcloud auth print-identity-token)"`.
