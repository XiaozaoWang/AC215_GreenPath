#!/bin/bash
# Use before going to the cloud: build the image for Google's machine architecture (linux/amd64), push it to the group project's Artifact Registry.
# The Vertex pipeline pulls by image name, so after changing code, run this again if you want the pipeline to use the new code.
# The image only has code and dependencies, no keys or tokens.
#
# Before the first use:
#   gcloud artifacts repositories create greenpath --repository-format=docker --location=us-central1   # once for the whole project
#   gcloud auth configure-docker us-central1-docker.pkg.dev                                            # once per person: docker push uses your gcloud login

set -e

# take only the project name line from .env (the token is not read)
export $(grep '^GOOGLE_CLOUD_PROJECT=' "$(pwd)/../../.env")

export IMAGE_NAME="greenpath-data-labeler"
export IMAGE_URI="us-central1-docker.pkg.dev/$GOOGLE_CLOUD_PROJECT/greenpath/$IMAGE_NAME"

# amd64: Vertex machines are amd64; on Apple silicon Macs this is an emulated build, slower than docker-shell.sh
docker build --platform linux/amd64 -t $IMAGE_URI:latest --push -f Dockerfile .

echo "Pushed $IMAGE_URI:latest"
