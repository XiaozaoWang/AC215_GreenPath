#!/bin/bash
# For local use: build the image, then start the container and open a shell with DVC installed
#   sh docker-shell.sh
#
# Differences from the tutorial:
#   - Mounts the whole repo (not only this folder): DVC needs to find .git in the repo root
#   - No service account key: the gcloud login credentials (ADC) on your computer are mounted read-only, so reading and writing the bucket uses your Google account
#   - The bucket isn't mounted (no --privileged / fuse needed), the bucket name comes from .env
#   - Do git commit / tag / push yourself on the Mac, the container only runs python cli.py and dvc commands

# Stop if any step fails
set -e

export IMAGE_NAME="greenpath-data-versioning"
export REPO_DIR=$(cd ../.. && pwd)          # repo root green-path-m3/
export ENV_FILE=$REPO_DIR/.env               # .env in the repo root (not in git)

# Build the image (host architecture)
docker build -t $IMAGE_NAME -f Dockerfile .

# Start the container
#   The whole repo is mounted at /app, starting in /app/src/data-versioning: the downloaded data and the .dvc file show up right in this folder on the Mac
#   gcloud credentials are mounted read-only into the container user's home folder, gcsfs and DVC find them on their own
docker run --rm --name $IMAGE_NAME -ti \
-v "$REPO_DIR":/app \
-w /app/src/data-versioning \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME
