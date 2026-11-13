# GreenPath data processor

Turns the data-labeler scores into the training table. All logic is in `cli.py`; it does not call Gemini, download images, train, or change the source labels.

## Run

```sh
sh docker-shell.sh                      # shell in the container, then run python cli.py ...
sh docker-shell.sh cli.py --prepare     # full prep, writes gs://<bucket>/training/<dataset-id>_<time>/
sh docker-build-push.sh                 # push the image to Artifact Registry for the Vertex pipeline
```

The Vertex pipeline (`src/workflow/cli.py`) runs `cli.py --prepare --bucket <bucket> --dataset-id <dataset> --training-id <id> --output-dir gs://<bucket>/training/<id>`. If the output folder already exists it fails, so old training sets are never overwritten.

## What `--prepare` does

1. Reads `datasets/<dataset-id>/` (cells, OSM features, street view, aerial and street labels), checks it is complete and consistent, drops cells with `tile_ok` false.
2. Splits before removing anything: 4 places (plus areas within 3 km) are Test, the rest goes to Train/Val by H3 level 8 blocks (about 15% Val, 5 folds, seed 42).
3. Removes whole cells hit by the three detection rules (round 1) or by the manual flags CSV `datasets/<dataset-id>/corrections/v2/manual_flags.csv` (round 2; skipped if missing, `--manual-flags-csv none` turns it off).
4. Maps street scores to the aerial scale by quantiles fit on Train + Val only, then fuses them with fixed weights into 15 targets in 0-1 (`visual_clutter` is street only, `hilly_terrain` is not used).
5. Writes `dataset.parquet` (87 columns), `exclusions.parquet` and `meta.json`.

Without `--prepare` it only runs the three rules (street photos taken indoors or in tunnels, broken or misread aerial tiles, low suburban farmland/grassland scores among high neighbors) and writes `gs://<bucket>/candidates/<time>/candidates.parquet`. Nothing is removed.

## Tests

```sh
uv run --locked python -m unittest discover -s tests
```
