#!/bin/bash
# For local use: build the image, then start the container
#   sh docker-shell.sh                                  # opens a shell in the container, you run python cli.py ... yourself
#   sh docker-shell.sh cli.py --prepare ...             # runs it straight through, then exits
#
# Identity and secrets (neither goes into the image, both are only given to the container at run time):
#   - Google identity: the gcloud login credentials on your computer (ADC, made by gcloud auth application-default login) are mounted read-only into the container,
#     so reading and writing the bucket uses your Google account; no service account key needed.
#   - Variables in .env (GREENPATH_BUCKET, GOOGLE_CLOUD_PROJECT): passed into the container as is with `--env-file`, the values don't show up in the command and don't go into the image.
#   - In the cloud (Vertex) there is no computer of yours: identity becomes the service account set for the job, no code change needed (gcsfs uses google_default to find the identity).

# Stop if any step fails
set -e

export IMAGE_NAME="greenpath-data-processor"
export BASE_DIR=$(pwd)
export ENV_FILE=$(pwd)/../../.env          # .env in the repo root (not in git)

# Build the image (host architecture)
docker build -t $IMAGE_NAME -f Dockerfile .

# Start the container
#   Code folder is mounted at /app: no rebuild needed after editing cli.py; the default local results in outputs/ show up right in this folder
#   gcloud credentials are mounted read-only into the container user's home folder, the Google libraries find them on their own
docker run --rm --name $IMAGE_NAME -ti \
-v "$BASE_DIR":/app \
-v "$HOME/.config/gcloud":/home/app/.config/gcloud:ro \
--env-file "$ENV_FILE" \
$IMAGE_NAME "$@"
