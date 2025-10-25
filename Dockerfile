# Install uv
FROM ubuntu:24.04
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install system dependencies
RUN apt-get update && apt-get install -y \
    nut \
    openssh-client \
    usbutils \
    git \
    rclone \
    procps \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Set up NUT user and permissions
RUN groupadd -g 999 nut || true && \
    useradd -r -g nut -u 999 nut || true && \
    mkdir -p /var/run/nut /var/lib/nut /etc/nut && \
    chown nut:nut /var/run/nut /var/lib/nut /etc/nut && \
    chmod 750 /var/run/nut /var/lib/nut /etc/nut

# Our usb devices are owned by root (see ls -la /dev/bus/usb), so we need
# to add nut to the root group
RUN usermod -aG root nut

# Remove the default nut files because these will be added during
# bootstrapping in Python
RUN rm -rf /etc/nut/nut.conf /etc/nut/ups.conf /etc/nut/upsd.conf /etc/nut/upsd.users

# Change the working directory to the `app` directory
WORKDIR /app

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

# Copy the project into the image
ADD . /app

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# Create necessary directories
RUN mkdir -p /root/.bungalo

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV DOCKER_CONTEXT=true

# Run all background processes
CMD ["uv", "run", "bungalo", "run-all"]
