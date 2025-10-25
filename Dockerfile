# Build the Next.js frontend
FROM node:20-bullseye AS frontend_build
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build && rm -rf .next/cache

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

# Copy Node runtime from build stage
COPY --from=frontend_build /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend_build /usr/local/bin/npm /usr/local/bin/npm
COPY --from=frontend_build /usr/local/bin/npx /usr/local/bin/npx
COPY --from=frontend_build /usr/local/lib/node_modules /usr/local/lib/node_modules

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

# Copy frontend build artifacts
COPY --from=frontend_build /app/frontend/.next /app/frontend/.next
COPY --from=frontend_build /app/frontend/public /app/frontend/public
COPY --from=frontend_build /app/frontend/package.json /app/frontend/package.json
COPY --from=frontend_build /app/frontend/server-entry.js /app/frontend/server-entry.js

# Ensure Next.js standalone bundle has access to its static assets
RUN mkdir -p /app/frontend/.next/standalone/.next \
    && cp -r /app/frontend/.next/static /app/frontend/.next/standalone/.next/static

# Sync the project
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# Create necessary directories
RUN mkdir -p /root/.bungalo

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run all background processes
CMD ["uv", "run", "bungalo", "run-all"]
