#!/bin/bash
# Build the image and open a shell in it, then run python cli.py ...
# The whole repo is mounted: --prepare reads model-training/ and writes model-service/artifacts/.
# Your gcloud login (ADC) is mounted read-only, so the bucket and Cloud Run are used as you. No key files.
# .env is passed with --env-file (GOOGLE_CLOUD_PROJECT, GREENPATH_BUCKET), never baked into the image.

set -e

export GCLOUD_CONFIG_DIR="${GCLOUD_CONFIG_DIR:-$HOME/.config/gcloud}"   # override on Windows (AppData/Roaming/gcloud)
export IMAGE_NAME="greenpath-deployment"
export REPO_DIR=$(cd ../.. && pwd)
export ENV_FILE=$REPO_DIR/.env

docker build -t $IMAGE_NAME -f Dockerfile .

docker run --rm --name $IMAGE_NAME -ti \
-v "$REPO_DIR":/app \
-w /app/src/deployment \
-v "$GCLOUD_CONFIG_DIR":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME
