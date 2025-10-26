# Central setup script for bungalo, intended for installation directly
# with bash.
#! /bin/bash

# Pull the latest container
docker pull ghcr.io/piercefreeman/bungalo:latest

# We require cifs to be loaded for the NAS backup to work
sudo modprobe cifs

# Shutdown the current container if it exists
docker rm -f bungalo || true

# Run the new container
# Note: We use Docker-in-Docker for Jellyfin, so no need to mount /var/run/docker.sock
docker run -d \
     --name bungalo \
     --restart=always \
     --privileged \
     --network host \
     --device=/dev/bus/usb \
     --cap-add=SYS_ADMIN \
     --device /dev/fuse \
     -v ~/.bungalo:/root/.bungalo \
     -v /dev/bus/usb:/dev/bus/usb \
     ghcr.io/piercefreeman/bungalo:latest
