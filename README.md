# bungalo

Control library for our home network. Bundles all of the logic our homelab needs into a single deployable Docker image.

Maybe even extendable enough for what you want it to do, too.

## Getting Started

### Hardware Setup

- The main `Bungalo` server should be connected directly to the UPS over USB. It should be accessibile via `lsusb`.
- The server should be in the same LAN as the network devices that we want to restart via ssh.
- The BIOS for the server should be updated to enable "always restart on power failure" so systemctl can bootup and re-launch our Docker worker.

### Docker Setup

We release the latest version as a docker container. On the remote server:

```bash
curl https://raw.githubusercontent.com/piercefreeman/bungalo/refs/heads/main/setup.sh | /bin/bash
```

## Features

### Graceful Shutdown

The `nut` module provides support for polling for the current status of UPS systems. It's a widely supported utility so should be supported by most devices; in our case a CyberPower CP1500PFCRM2U.

During bootstrapping we will write the necessary system files for NUT so the syscontrol daemons can launch and start polling. This has the added benefit of turning the server into a NUT server so supported NUT clients can access the UPS status over the network and/or trap commands instead of requiring the server to manage every operation itself.

During steady state operation, we poll for the UPS status from our locally running NUT daemon every 10s. When the total charge dips below the `nut_shutdown_threshold` threshold, we will perform an ssh shutdown into the network devices so they have time to shutdown gracefully without data loss.

### Data Backups

Our NAS is our network's source of truth for all files. For a full data backup, we conceptually have two separate steps:

1. Cloud -> NAS: Sync proprietary clouds like iCloud, Frame.io, and iPhoto into our local storage.
2. Syncing the full NAS contents to a remote cloud. We're currently architected with two redundency zones, one in Virginia and one in Amsterdam. We copy these files individually with rclone instead of using "Cloud Replication" so we have a bit more control over encryption keys and notification status of completed syncs.

Backups made to remote locations are encrypted via rclone's crypt provider:

```toml
[[endpoints.b2]]
  nickname = "b2-eu"
  key_id = "my_key"
  application_key = "my_app"
  encrypt_key = "custom_encryption_key"
```

### Authorized Keys

The `SSHManager` manages a local bungalo ssh credential that we place into the bungalo owned folder at `~/.bungalo`.

### Config

To configure the server behavior, add a config file to: `~/.bungalo/config.toml`. See `config.example.toml` for the expected format.

### Slack Integration

We ping Slack after successful syncs, when there are power failures, etc. We also use channels as the IO interface for receiving 2FA codes when we need to get additional permission from the user.

The `slack_manifest.json` captures the current configuration of our private slack app.

## Future Work

- Unifi devices don't support wake-on-lan, so once they're shutdown there's no way to remotely start them back up. We'll have to combine it with a remotely controllable Power Distribution Unit if we want to add the restart behavior.

## Running tests

All tests are run with pytest and can either be run explicitly or routed through our Makefile:

```bash
make test
```

To filter for a specific tests.

```bash
make test -- -k test_fully_parameterized_config
```

## Development

1. Build the Docker image:
   ```bash
   docker build -t bungalo .
   ```

2. Create your config file at `~/.bungalo/config.toml` (see Config section below)

3. Run the container in privileged mode to access USB devices. See `setup.sh` for the latest command that's run during auto-setup.

   ```bash
   docker rm -f bungalo
   docker run -d \
     --name bungalo \
     --restart=always \
     --privileged \
     --network host \
     --device=/dev/bus/usb \
     -v ~/.bungalo:/root/.bungalo \
     -v /dev/bus/usb:/dev/bus/usb \
     --cap-add=SYS_ADMIN \
     --device /dev/fuse \
     bungalo
   ```

   The flags explained:
   - `--privileged`: Required for USB device access
   - `--network host`: Allows direct access to host network for SSH operations
   - `-v ~/.bungalo:/root/.bungalo`: Mounts your config directory
   - `-v /dev/bus/usb:/dev/bus/usb`: Mounts USB devices

4. Sometimes you'll need to diagnose USB permissions from within Docker. Run with an interactive session:

    ```bash
    docker run \
        --privileged \
        --network host \
        --device=/dev/bus/usb \
        -v ~/.bungalo:/root/.bungalo \
        -v /dev/bus/usb:/dev/bus/usb \
        --cap-add=SYS_ADMIN \
        --device /dev/fuse \
        -it bungalo /bin/bash
    ```
