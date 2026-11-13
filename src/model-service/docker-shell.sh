#!/bin/bash
# For local use: build the image, start the Model Service like on Cloud Run, try it on your own computer before deploying
#   sh docker-shell.sh          start the service, open http://localhost:8080/docs in the browser to try it; Ctrl+C to stop
#   sh docker-shell.sh bash     don't start the service, open a terminal in the container
#
# First run python cli.py --prepare in src/deployment to put the model into artifacts/
# Code is not mounted into the container: what runs is the same image that gets deployed, so if it works locally it works in the cloud too
# The only difference from Cloud Run is identity: locally it reads the bucket with your gcloud login (mounted read-only), in the cloud with the service account

# Stop if any step fails
set -e

export IMAGE_NAME="greenpath-model-service"
export ENV_FILE=$(pwd)/../../.env          # .env in the repo root (not in git), it has GREENPATH_BUCKET

# Areas that can be scored (datasets the collector collected, comma separated); for the cloud it is set by DATASETS in src/deployment/cli.py
export DATASETS="${DATASETS:-1026_Full_data}"

# Stop if the model isn't prepared
if [ ! -f artifacts/best_model.pt ]; then
    echo "artifacts/ is empty: run 'python cli.py --prepare' in src/deployment first"
    exit 1
fi

# Build the image (this machine's architecture)
docker build -t $IMAGE_NAME -f Dockerfile .

# Start the container: port 8080 maps to 8080 on your computer; gcloud credentials are mounted read-only into the container user's home folder, Google's libraries find them automatically
docker run --rm --name $IMAGE_NAME -ti \
-p 8080:8080 \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
-e DATASETS="$DATASETS" \
$IMAGE_NAME "$@"
