#!/bin/bash
# Like model-service/docker-build-push.sh: build/push on the host, then deploy from src/deployment (python cli.py --deploy --service frontend).
set -e

# Read only the project ID from .env; credentials stay in the normal Docker/gcloud login.
# Strip CR from Windows .env files before forming the registry address.
export $(grep '^GOOGLE_CLOUD_PROJECT=' "$(pwd)/../../.env" | tr -d '\r')
export IMAGE_NAME="greenpath-frontend"
export IMAGE_URI="us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/greenpath/$IMAGE_NAME"
export IMAGE_TAG="${APP_IMAGE_TAG:-latest}"

# Cloud Run uses amd64, including when the developer's computer uses another architecture.
docker build --platform linux/amd64 -t "$IMAGE_URI:$IMAGE_TAG" --push -f Dockerfile .
echo "Pushed $IMAGE_URI:$IMAGE_TAG"
