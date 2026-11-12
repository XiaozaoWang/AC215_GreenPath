#!/bin/bash

# exit immediately if a command exits with a non-zero status
set -e

# Set vairables
export BASE_DIR=$(pwd)
export SECRETS_DIR=$(pwd)/../../../secrets/
# Team defaults; these variables can also be supplied by the caller.
export GCP_PROJECT="${GCP_PROJECT:-organic-victory-508017-v9}"
export GREENPATH_BUCKET="${GREENPATH_BUCKET:-ac215-notebook}"
export GREENPATH_DATASET="${GREENPATH_DATASET:-1026_Full_data}"
export MODEL_SERVICE_URL="${MODEL_SERVICE_URL:-http://host.docker.internal:8080}"
export GOOGLE_APPLICATION_CREDENTIALS="${GOOGLE_APPLICATION_CREDENTIALS_CONTAINER:-/secrets/llm-service-account.json}"
export IMAGE_NAME="smartpath-api-service"

# 建 docker 网络（已经有了就跳过），和 docker-compose.yml 用同一张网
docker network inspect smartpath-network >/dev/null 2>&1 || docker network create smartpath-network


# Build the image based on the Dockerfile
docker build -t $IMAGE_NAME -f Dockerfile .

# Run the container
docker run --rm --name $IMAGE_NAME -ti \
-p 9000:9000 \
-e DEV=1 \
-v "$BASE_DIR":/app \
-v "$SECRETS_DIR":/secrets \
-e GOOGLE_APPLICATION_CREDENTIALS=$GOOGLE_APPLICATION_CREDENTIALS \
-e GCP_PROJECT=$GCP_PROJECT \
-e GREENPATH_BUCKET=$GREENPATH_BUCKET \
-e GREENPATH_DATASET=$GREENPATH_DATASET \
-e MODEL_SERVICE_URL=$MODEL_SERVICE_URL \
-e MODEL_MAX_CONCURRENCY -e GEMINI_MODEL -e GEMINI_LOCATION \
--network smartpath-network \
$IMAGE_NAME