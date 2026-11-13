#!/bin/bash

set -e

export IMAGE_NAME="smartpath-frontend"

# Create the docker network (skip if it already exists), same network as docker-compose.yml
docker network inspect smartpath-network >/dev/null 2>&1 || docker network create smartpath-network

# Build the image based on the Dockerfile
docker build -t $IMAGE_NAME -f Dockerfile.dev .

# Run the container
docker run --rm --name $IMAGE_NAME -ti -v "$(pwd)/:/app/" -p 3000:3000 --network smartpath-network  $IMAGE_NAME