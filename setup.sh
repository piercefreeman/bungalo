# Central setup script for bungalo, intended for installation directly
# with bash.
#! /bin/bash

# Pull the latest container
docker pull ghcr.io/piercefreeman/bungalo:latest

# Shutdown the current container if it exists
docker rm -f bungalo || true

# Run the new container
docker run -d \
     --name bungalo \
     --restart=always \
     --privileged \
     --network host \
     --device=/dev/bus/usb \
     -v ~/.bungalo:/root/.bungalo \
     -v /dev/bus/usb:/dev/bus/usb \
     ghcr.io/piercefreeman/bungalo:latest
