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
2. Syncing the full NAS contents to a remote cloud. We're currently architected with two redundancy zones, one in Virginia and one in Amsterdam. We use [`rclone sync`](https://rclone.org/commands/rclone_sync/) instead of "Cloud Replication" so we have more control over encryption keys and notification status. Sync makes the remote match the NAS, including removing remote files that were deleted locally; rclone suppresses destination deletes if the run encounters errors.

Backblaze B2 keeps deleted files as hidden versions by default. Each backup bucket must have a [lifecycle rule](https://www.backblaze.com/docs/cloud-storage-lifecycle-rules) with `daysFromHidingToDeleting` set to `30`, retaining deleted and overwritten versions for 30 days before permanent deletion. Do not set `daysFromUploadingToHiding`, which would also expire current files that still exist on the NAS. Rclone documents the B2 version and deletion behavior in its [B2 backend guide](https://rclone.org/b2/#versions).

Backups made to remote locations are encrypted via rclone's crypt provider:

```toml
[[endpoints.b2]]
  nickname = "b2-eu"
  key_id = "my_key"
  application_key = "my_app"
  encrypt_key = "custom_encryption_key"
```

#### Remote server → NAS

Remote servers can be pulled into a NAS folder through [rclone's SFTP backend](https://rclone.org/sftp/), using SSH key authentication. This runs through the existing `bungalo remote-backup` service (also included in `run-all`), with the same interval, dashboard status, and Slack notifications.

Store an unencrypted private key at `~/.bungalo/ssh/remote-server` on the Docker host and authorize its public key for the remote server's backup user. Restrict the directory to mode `700` and the private key to mode `600`. Put the server's verified host-key entry in `~/.bungalo/ssh/known_hosts`; verify its fingerprint with the server operator before trusting it. For a custom port, the known-hosts entry uses `[server.example.com]:2222`. Missing or mismatched host keys fail the sync.

`setup.sh` already mounts `~/.bungalo` at `/root/.bungalo`, so these files are available inside the container. Use their **container paths** in the config. To keep credentials outside that directory, add a read-only bind mount to your Docker command, for example `--mount type=bind,src=/absolute/ssh-directory,dst=/run/backup-ssh,readonly`, and use paths under `/run/backup-ssh` instead.

```toml
[[endpoints.sftp]]
  nickname = "remote-server"
  host = "server.example.com"
  username = "backup"
  port = 22
  key_file = "/root/.bungalo/ssh/remote-server"
  known_hosts_file = "/root/.bungalo/ssh/known_hosts"

[[backups.sync]]
  src = "sftp:remote-server:///srv/data"
  dst = "nas:unifi-nas://Common_Drive/remote-server"
```

The third slash in `sftp:remote-server:///srv/data` makes `/srv/data` absolute. Use `sftp:remote-server://data` for a directory relative to the SSH user's home. Directory contents land directly in the destination folder. The server must support SFTP; it does not need rclone or rsync installed. This uses file-level SFTP transfers, not rsync's block-level delta protocol.

Use a dedicated NAS destination: `sync` mirrors the source, including deleting destination files absent from the source. It does not preserve Unix ownership, permissions, or symlinks as a filesystem archive. This pair only writes to the NAS; it does not create a B2 backup. To include these files in your existing NAS → B2 backup, place this pair **before** the NAS → B2 pair in `backups.sync`, with its destination inside the NAS tree already backed up. Pairs run sequentially in configuration order; a failed pair is reported, and later pairs still run against the data available on the NAS.

### Authorized Keys

The `SSHManager` manages a local bungalo ssh credential that we place into the bungalo owned folder at `~/.bungalo`.

### Config

To configure the server behavior, add a config file to: `~/.bungalo/config.toml`. See `config.example.toml` for the expected format.

If you access the dashboard over a different interface (for example, through Tailscale), set the optional `root.self_ip` field so Slack links point at a reachable address:

```toml
[root]
self_ip = "100.68.0.12"
```

### Slack Integration

We still ping Slack after successful syncs and outages, but interactive prompts (like iCloud 2FA) now flow through the Bungalo dashboard. When a task needs input we post a deep link back to the dashboard instead of collecting replies in-channel. The `slack_manifest.json` captures the current configuration of our private Slack app.

### Dashboard

Launching `bungalo run-all` now boots two companion services:

- A FastAPI control plane (`bungalo/web_server.py`) that surfaces runtime state and accepts task responses.
- A Next.js dashboard in `frontend/` powered by shadcn components for a monochrome, serif UI.

Make sure Node.js is available and install dependencies once:

```bash
cd frontend
npm install
```

Environment variables allow overrides, but by default the API listens on `0.0.0.0:5006` and the dashboard on `0.0.0.0:80`.

Set `[root].self_ip` (or export `BUNGALO_EXTERNAL_HOST`) if you want Slack links and the dashboard itself to use a Tailscale or other reachable address; both FastAPI and Next.js bind to `0.0.0.0` inside the container, so advertising the correct hostname ensures browsers connect to the right place.

### Media Server

Define NAS shares that should be exposed to Jellyfin via the `media_server` configuration block. Bungalo will mount each NAS share and launch the Jellyfin container on demand:

```bash
bungalo jellyfin
```

Mounts are published to the container as read-only volumes so Jellyfin can index them without modifying source data. Provide a NAS-backed `transcode` path (writeable) so temporary transcoding artifacts land off-box; we mount it inside the container at `/cache`. We derive the Slack announcement URL from `[root].self_ip`, falling back to localhost if unset.

**Architecture:** Jellyfin runs via Docker-in-Docker (DinD). When the Bungalo container starts, it launches an inner Docker daemon that Jellyfin uses. This allows Jellyfin to access the NAS shares that Bungalo mounts (via SMB/FUSE) inside the container, solving the sibling-container volume mounting problem. The inner Docker daemon uses the `vfs` storage driver for simplicity and runs in privileged mode.

### Footage Ingest

Bungalo can watch the volumes mounted on a Mac and offer new videos for upload to Studio, with Slack as the whole interface. Every `interval`, it connects over SSH and looks through the volumes named in `volumes` for `.mp4`, `.mov`, `.mkv`, `.webm`, `.m4v`, `.avi`, and `.mxf` files it hasn't announced. Only those volumes are read, so anything else mounted on the Mac, like a large network share, is left alone. List volumes by the name you gave them: when two share a name and macOS mounts one at `/Volumes/CARD 1`, `CARD` still matches both, and each is tracked separately. A listed volume that isn't mounted is skipped until it appears. Each volume with new videos gets a Slack post with contact sheets in its thread, showing one numbered band of 5 frames sampled across each video.

To upload, reply in that thread with the numbers and a public upload link from the project's Footage page in Studio:

> 1 3 5-8 https://studio.example.com/public-upload/KEY

Use `all` instead of numbers to send everything. The link's origin must match `studio_url`, and a comment that selects videos without a valid link gets a reply explaining what's needed. Anyone who can post in the channel can request an upload if they have a valid link. Bungalo adds :eyes: to every comment as soon as it reads it. Comments in one thread run in order, while different threads run side by side, so footage that needs no transcode starts straight away. Each volume is read for one video at a time, and only one transcode runs at a time, though a finished transcode uploads while the next one runs. A thread takes comments for 14 days. It then closes with a note, and any videos not uploaded are offered again in a new post the next time their volume is scanned.

After a video uploads, Bungalo asks in the same thread whether to remove it from the volume. Reply exactly `DELETE` after the latest list to permanently remove the videos on it. Each file is checked again on the Mac as it's removed, and left alone if it changed or its disk was swapped. Network shares have no disk ID to check, so Bungalo won't offer to delete from them. Only the video files are removed, so sidecar files stay. For a volume that's transcoded, Studio has the transcoded version, not the original. Anyone who can post in the channel can confirm a delete.

Frames are extracted on the Mac, and `curl` uploads directly from the Mac to Studio in parts, so video never passes through the Bungalo server. A part that fails is retried, and if an upload stops, even across a Bungalo restart, commenting again with the same link sends only the parts Studio doesn't have. Before each upload Bungalo confirms the same disk is mounted and the file is unchanged. Upload links are never written to its state file or logs, but they do stay visible in the Slack thread until they expire or are revoked in Studio.

```toml
[[endpoints.sftp]]
  nickname = "edit-mac"
  host = "edit-mac.local"
  username = "footage"
  key_file = "/root/.bungalo/ssh/edit-mac"
  known_hosts_file = "/root/.bungalo/ssh/known_hosts"

[media_ingest]
  ssh_endpoint = "edit-mac"
  studio_url = "https://studio.example.com"
  volumes = ["CARD", "EOS_DIGITAL"]
  interval = "PT5M"
```

On the Mac:

- Enable **System Settings → General → Sharing → Remote Login**, then under its info button turn on **Allow full disk access for remote users**. Without it, macOS blocks SSH sessions from reading removable volumes.
- Install ffmpeg with `brew install ffmpeg`.
- Authorize the public key for `key_file` for the SSH user.

To convert selected videos on the Mac before they upload, add a template:

```toml
[media_ingest.transcode]
  template = "/root/.bungalo/media_ingest/transcode.sh"
  output_suffix = "_HQ.mp4"
  work_dir = "/Volumes/Scratch/bungalo"
  volumes = ["NINJAV"]
```

`volumes` is optional and limits conversion to those names from `media_ingest.volumes`. Without it, every scanned volume is converted. Videos from any other volume upload as recorded, keeping their own filenames and resuming part by part.

The template is a shell script stored on the Bungalo server, so use its container path. It runs on the Mac under `/bin/sh -e` once per selected video, and is re-read for each request, so edits apply without a restart. Quote its two variables so any filename is safe:

```sh
ffmpeg -hide_banner -i "$INPUT" \
  -map 0:v:0 -map '0:a?' \
  -vf format=yuv420p10le \
  -c:v hevc_videotoolbox -profile:v main10 -tag:v hvc1 \
  -b:v 25M -maxrate 40M -bufsize 60M \
  -c:a aac -b:a 192k \
  -movflags +faststart \
  "$OUTPUT"
```

`output_suffix` replaces the source's extension to name the upload, so `C0001.MOV` becomes `C0001_HQ.mp4`; Studio must accept the extension it ends in. `work_dir` needs room for the largest output, and defaults to `/tmp/bungalo-media-ingest` on the Mac's boot disk. Each output is deleted once its upload finishes or fails, so a transcoded video that fails starts over when requested again. If a transcode fails, that video is reported in the thread and the rest continue. A bad or missing template is reported in the thread and nothing is uploaded.

The Slack app needs the `files:write` and `reactions:write` scopes, which are in `slack_manifest.json`; reinstall the app after adding them. Run this on its own with `bungalo media-ingest`, or add `[media_ingest]` to include it in `run-all`. What has been announced is recorded in `~/.bungalo/media_ingest/state.json`. Delete it to have everything announced again.

### Home Assistant

Enable Home Assistant by setting `enabled = true` in your config:

```toml
[home_assistant]
  enabled = true
```

Bungalo launches two containers via Docker-in-Docker: the Home Assistant core container and a [Matter Server](https://github.com/matter-js/python-matter-server) sidecar for Matter device support. Both run on host network.

On first launch, Bungalo seeds a set of starter configuration files into `~/.bungalo/home_assistant/config/`, including a sunrise wake-up automation and a matching manual script. These are only written if the files don't already exist — your customizations are never overwritten.

The bundled automations reference a placeholder `light.bedroom` entity. You'll need to update them to match your actual devices, either by editing the YAML files directly in `~/.bungalo/home_assistant/config/` or through the Home Assistant UI under **Settings → Automations & Scenes**. The same applies for any new integrations (Hue, Zigbee, etc.) — configure those through the HA UI at **Settings → Devices & Services**.

A standalone command is also available:

```bash
bungalo home-assistant
```

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
     -v ~/.bungalo/docker:/var/lib/docker \
     -v /dev/bus/usb:/dev/bus/usb \
     --cap-add=SYS_ADMIN \
     --device /dev/fuse \
     bungalo
   ```

   The flags explained:
   - `--privileged`: Required for USB device access and Docker-in-Docker
   - `--network host`: Allows direct access to host network for SSH operations
   - `-v /var/run/docker.sock:/var/run/docker.sock`: **No longer needed** - we use Docker-in-Docker for Jellyfin instead of the host daemon
   - `-v ~/.bungalo:/root/.bungalo`: Mounts your config directory
   - `-v ~/.bungalo/docker:/var/lib/docker`: Persists the inner Docker daemon's layer cache so container images (Jellyfin, Home Assistant) survive restarts
   - `-v /dev/bus/usb:/dev/bus/usb`: Mounts USB devices
   - `--cap-add=SYS_ADMIN` and `--device /dev/fuse`: Required for FUSE mounts (NAS shares)

4. Sometimes you'll need to diagnose USB permissions from within Docker. Run with an interactive session:

    ```bash
    docker run \
        --privileged \
        --network host \
        --device=/dev/bus/usb \
        -v ~/.bungalo:/root/.bungalo \
        -v ~/.bungalo/docker:/var/lib/docker \
        -v /dev/bus/usb:/dev/bus/usb \
        --cap-add=SYS_ADMIN \
        --device /dev/fuse \
        -it bungalo /bin/bash
    ```

   Note: The Docker-in-Docker daemon will start automatically via `/entrypoint.sh`. If you want to manually run commands, you can start the daemon with:
   
   ```bash
   dockerd --host=unix:///var/run/docker.sock --storage-driver=vfs &
   # Wait a few seconds for it to be ready
   docker ps  # Should work once daemon is running
   ```
