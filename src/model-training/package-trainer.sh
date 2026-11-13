#!/bin/sh
# Packs the training code package/ into trainer.tar.gz and uploads it to gs://<bucket>/trainer/greenpath-trainer.tar.gz
# The pipeline's training step (workflow/model.py) takes the code from here when it starts a training machine on Vertex, so run this again after changing the training code
# Run it directly on the Mac (gcloud must be installed and logged in): sh package-trainer.sh
set -e
cd "$(dirname "$0")"

# Only take the bucket line from the .env in the repo root (don't read tokens)
GCS_BUCKET_NAME=$(grep -E '^GREENPATH_BUCKET=' ../../.env | cut -d= -f2- | tr -d "\"' ")

rm -f trainer.tar trainer.tar.gz
COPYFILE_DISABLE=1 tar --exclude='__pycache__' --exclude='*.egg-info' --exclude='.DS_Store' -cvf trainer.tar package
gzip trainer.tar
gcloud storage cp trainer.tar.gz "gs://$GCS_BUCKET_NAME/trainer/greenpath-trainer.tar.gz"
