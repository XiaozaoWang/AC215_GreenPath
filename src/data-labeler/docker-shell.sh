#!/bin/bash
# For local use: build the image, then start the container
#   sh docker-shell.sh                                     # opens a shell in the container, run python cli.py ... yourself
#   sh docker-shell.sh cli.py --dataset ... --areas ...    # runs it straight through, then exits
#
# Identity and secrets (none go into the image, they are only given to the container at runtime):
#   - Google identity: your computer's gcloud login credentials (ADC, made by gcloud auth application-default login) are mounted read-only into the container.
#     In the container, reading/writing the bucket and calling Gemini use your Google account, with your permissions in the group project. No service account key and no Gemini API key needed.
#   - Variables in .env (bucket name, GOOGLE_CLOUD_PROJECT = group project, GOOGLE_CLOUD_LOCATION): passed into the container as-is with `--env-file`;
#     the values don't show up in the command and aren't written into the image. Scoring doesn't need the Mapillary token.
#   - In the cloud (Vertex) your computer isn't there: the identity becomes the service account set for the job, the pipeline passes the project in env var GOOGLE_CLOUD_PROJECT.

# stop if any step fails
set -e

export IMAGE_NAME="greenpath-data-labeler"
export BASE_DIR=$(pwd)
export ENV_FILE=$(pwd)/../../.env          # .env at the repo root (not in git): GREENPATH_BUCKET, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION

# build the image (native architecture, fast on Apple silicon Macs too)
docker build -t $IMAGE_NAME -f Dockerfile .

# start the container
#   code dir is mounted at /app: no rebuild needed after changing cli.py or prompts/; QC figures are written to qc/ here
#   gcloud credentials are mounted read-only in the container user's home dir, Google's libraries find them automatically
docker run --rm --name $IMAGE_NAME -ti \
-v "$BASE_DIR":/app \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME "$@"
