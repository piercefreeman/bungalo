# bungalo

Control library for our home network.

## Getting Started

- The main `Bungalo` server should be connected directly to the UPS (in our case Cyberpower) over USB. It should be accessibile via `lsusb`.
- The server should be in the same LAN as the Unifi devices that we want to restart via ssh.
- The BIOS for the server should be updated to enable "always restart on power failure" so systemctl can bootup and re-launch our Docker worker.

## Features

### Graceful Shutdown

The `nut` module provides support for.

## Future Work

- Unifi devices don't support wake-on-lan, so once they're shutdown there's no way to remotely start them back up. We'll have to combine it with a remotely controllable Power Distribution Unit if we want to add the restart behavior.

