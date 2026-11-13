#!/bin/bash
# Use before going to the cloud: build the image for Google's machine architecture (linux/amd64) and push it to Artifact Registry. Then run `python cli.py --deploy --service model` in src/deployment
# Run it again after changing the model (--prepare) or the code. The image has the model, but no keys or tokens.
# Commands for first-time setup (once per project, once per person) are in data-processor/docker-build-push.sh

set -e

# Only take the project name line from .env (don't read tokens)
export $(grep '^GOOGLE_CLOUD_PROJECT=' "$(pwd)/../../.env")

export IMAGE_NAME="greenpath-model-service"
export IMAGE_URI="us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/greenpath/$IMAGE_NAME"

# Stop if the model isn't prepared
if [ ! -f artifacts/best_model.pt ]; then
    echo "artifacts/ is empty: run 'python cli.py --prepare' in src/deployment first"
    exit 1
fi

# amd64: Cloud Run machines are amd64; on an Apple chip Mac the build is emulated, slower than docker-shell.sh
docker build --platform linux/amd64 -t $IMAGE_URI:latest --push -f Dockerfile .

echo "Pushed $IMAGE_URI:latest"
