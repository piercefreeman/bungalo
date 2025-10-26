#!/bin/bash

# entrypoint.sh - Starts Docker daemon inside container, then launches Bungalo

set -e

echo "Starting Docker daemon for Docker-in-Docker..."

# Start Docker daemon in the background
dockerd \
    --host=unix:///var/run/docker.sock \
    --storage-driver=vfs \
    >/var/log/dockerd.log 2>&1 &

DOCKERD_PID=$!
echo "Docker daemon started with PID $DOCKERD_PID"

# Wait for Docker daemon to be ready
echo "Waiting for Docker daemon to be ready..."
TIMEOUT=30
ELAPSED=0
while ! docker info >/dev/null 2>&1; do
    if [ $ELAPSED -ge $TIMEOUT ]; then
        echo "ERROR: Docker daemon failed to start within ${TIMEOUT}s"
        echo "Docker daemon logs:"
        cat /var/log/dockerd.log
        exit 1
    fi
    sleep 1
    ELAPSED=$((ELAPSED + 1))
done

echo "Docker daemon is ready!"
docker version

# Now start Bungalo
echo "Starting Bungalo..."
exec uv run bungalo run-all

