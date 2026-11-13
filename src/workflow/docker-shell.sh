#!/bin/bash
# For local use: build the pipeline tool image, open a shell in the container, and run python cli.py --pipeline etc. there
#
# Identity (not in the image, only given to the container at run time):
#   - Submitting the pipeline uses your Google account: the gcloud login credentials (ADC) are mounted read-only. You are an Owner of the group project,
#     so you can submit Vertex pipelines and also let them run as the greenpath-ml-workflow service account. No service account key file needed.
#   - GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GREENPATH_BUCKET from .env: passed into the container with `--env-file`.
#   - In the cloud the pipeline runs as the greenpath-ml-workflow service account; the collect step gets the Mapillary token from Secret Manager itself, not needed here.

# Stop if any step fails
set -e

export IMAGE_NAME="greenpath-workflow"
export BASE_DIR=$(pwd)
export ENV_FILE=$(pwd)/../../.env          # .env in the repo root (not in git)

# Build the image (host architecture)
docker build -t $IMAGE_NAME -f Dockerfile .

# Start the container: code folder mounted at /app (the compiled yaml shows up here too); gcloud credentials mounted read-only
docker run --rm --name $IMAGE_NAME -ti \
-v "$BASE_DIR":/app \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME "$@"
