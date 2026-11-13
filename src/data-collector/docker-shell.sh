#!/bin/bash
# For local use: build the image, then start the container
#   sh docker-shell.sh                                  # opens a shell in the container, run python cli.py ... yourself
#   sh docker-shell.sh cli.py --config ... --areas ...  # runs it straight through, then exits
#
# Identity and secrets (none go into the image, they are only given to the container at runtime):
#   - Google identity: your computer's gcloud login credentials (ADC, made by gcloud auth application-default login) are mounted read-only into the container.
#     In the container, reading/writing the bucket and reading Secret Manager use your Google account, with your permissions in the group project. No service account key file needed.
#   - Variables in .env (bucket name, Mapillary token, project name): passed into the container as-is with `--env-file`;
#     the values don't show up in the command and aren't written into the image. (.env is not read with bash source, because a | in the token would be split as a command)
#   - In the cloud (Vertex) your computer isn't there: the identity becomes the service account set for the job, the token comes from Secret Manager (see mapillary_token in cli.py).

# stop if any step fails
set -e

export IMAGE_NAME="greenpath-data-collector"
export BASE_DIR=$(pwd)
export ENV_FILE=$(pwd)/../../.env                   # .env at the repo root (not in git): GREENPATH_BUCKET, MAPILLARY_TOKEN, GOOGLE_CLOUD_PROJECT
export OSM_DIR=$(pwd)/../../../persistent-folder/osm # outside the repo: local cache of whole-state OSM maps (a few hundred MB each), no need to copy them again after the container is removed

# build the image (native architecture, fast on Apple silicon Macs too)
docker build -t $IMAGE_NAME -f Dockerfile .

# start the container
#   code dir is mounted at /app: no rebuild needed after changing cli.py; QC figures are written to qc/ here
#   gcloud credentials are mounted read-only in the container user's home dir, Google's libraries find them automatically
docker run --rm --name $IMAGE_NAME -ti \
-v "$BASE_DIR":/app \
-v "$OSM_DIR":/app/tmp \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME "$@"
