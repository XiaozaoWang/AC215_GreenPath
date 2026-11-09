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
├── api-service/      Backend API (FastAPI)
└── frontend/         Frontend (Next.js)
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

## Run the app locally

```
docker compose up            # app http://localhost:3000, API docs http://localhost:9000/docs
```
