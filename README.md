# bungalo

Control library for our home network.

## Getting Started

### Hardware Setup
- The main `Bungalo` server should be connected directly to the UPS over USB. It should be accessibile via `lsusb`.
- The server should be in the same LAN as the network devices that we want to restart via ssh.
- The BIOS for the server should be updated to enable "always restart on power failure" so systemctl can bootup and re-launch our Docker worker.

### Docker Setup
1. Build the Docker image:
   ```bash
   docker build -t bungalo .
   ```

2. Create your config file at `~/.bungalo/config.toml` (see Config section below)

3. Run the container in privileged mode to access USB devices:
   ```bash
   docker run -d \
     --name bungalo \
     --privileged \
     --network host \
     -v ~/.bungalo:/root/.bungalo \
     -v /dev/bus/usb:/dev/bus/usb \
     bungalo
   ```

   The flags explained:
   - `--privileged`: Required for USB device access
   - `--network host`: Allows direct access to host network for SSH operations
   - `-v ~/.bungalo:/root/.bungalo`: Mounts your config directory
   - `-v /dev/bus/usb:/dev/bus/usb`: Mounts USB devices

## Features

### Graceful Shutdown

The `nut` module provides support for polling for the current status of UPS systems. It's a widely supported utility so should be supported by most devices; in our case a CyberPower CP1500PFCRM2U.

During bootstrapping we will write the necessary system files for NUT so the syscontrol daemons can launch and start polling. This has the added benefit of turning the server into a NUT server so supported NUT clients can access the UPS status over the network and/or trap commands instead of requiring the server to manage every operation itself.

During steady state operation, we poll for the UPS status from our locally running NUT daemon every 10s. When the total charge dips below the `nut_shutdown_threshold` threshold, we will perform an ssh shutdown into the network devices so they have time to shutdown gracefully without data loss.

### Authorized Keys

The `SSHManager` manages a local bungalo ssh credential that we place into the bungalo owned folder at `~/.bungalo`.

### Config

To configure the server behavior, add a config file to: `~/.bungalo/config.toml`:

```toml
slack_webhook_url: https://hooks.slack.com/...

[[managed_hardware]]
  name: NAS
  local_ip: 192.168.1.172
  username: root

[[managed_hardware]]
  name: Dream Machine
  local_ip: 192.168.1.1
  username: root
```

## Future Work

- Unifi devices don't support wake-on-lan, so once they're shutdown there's no way to remotely start them back up. We'll have to combine it with a remotely controllable Power Distribution Unit if we want to add the restart behavior.

