# model-training

Training code for the GreenPath model: one 256x256 aerial tile in, 15 scores (0 to 1) out.
It reads the training table made by data-processor and trains on Vertex AI. Training is started by the workflow, not submitted from here.

- `package/setup.py`: dependencies the training machine installs at startup
- `package/trainer/task.py`: the training job (runs on Vertex as `python -m trainer.task`)
- `package/trainer/network.py`: builds the model, preprocesses images, saves and loads weights (the Model Service uses the same file)
- `package-trainer.sh`: packs `package/` and uploads it to `gs://<bucket>/trainer/greenpath-trainer.tar.gz`

No Dockerfile: Vertex uses Google's prebuilt training images (`sklearn-cpu` for a dry run, `pytorch-gpu` with one T4 for a real run).

## Run

1. After changing the training code, upload it once (on the Mac, gcloud installed and logged in): `sh package-trainer.sh`
2. In the workflow container: `python cli.py --pipeline` (whole pipeline) or `python cli.py --model_training` (training only).
   Set the training set and mode with `TRAINING_ID` and `DRY_RUN` at the top of `workflow/cli.py`.
   Dry run (default): CPU, 96 + 48 cells, 2 epochs, a few minutes. Real run: all train + val, up to 25 epochs, needs T4 quota.

## Input and output

- Reads `gs://<bucket>/training/<training-id>/dataset.parquet` and `meta.json`, plus the tiles that `tile_path` points to. Test rows are dropped.
- Writes `gs://<bucket>/results/<training-id>/{dry,final}/<run_id>/`: `best_model.pt` (for deployment), `config.json`, `history.parquet`, `metrics.parquet`, `predictions.parquet`.
- Updates `gs://<bucket>/results/<training-id>/{dry,final}/latest.json`, which names the latest run.

## Final model

Trained on `full_v2` on a Colab T4 (no GPU quota on Vertex yet):
`gs://<bucket>/results/full_v2/final/dinov2_s-linear-seed42-ebcc811f/best_model.pt`, val MAE 0.0550, test MAE 0.0699.
Background and experiments: `1107-vertexAI/HANDOFF_*.md`.
