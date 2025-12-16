# zpa-log-exporter

Prometheus exporter for Zscaler ZPA App Connectors. The exporter tails
`journalctl -f -t zpa-connector-child` and parses `Mtunnels(...)` log lines to
produce metrics.

The exporter can expose metrics in two ways:

- HTTP endpoint on `/metrics` (default mode)
- Prometheus textfile collector output for consumption by the Node Exporter

## Versioning

Project releases follow [Semantic Versioning](https://semver.org/). The current
version lives in the `VERSION` file, and a pre-commit hook (`check-semver-version-file`)
enforces that it always contains a valid SemVer value. If you hit a merge
conflict on `VERSION`, choose the highest version, update the file accordingly,
and rerun the hook (or `python scripts/check_semver.py VERSION`) to verify the
resolution.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `EXPORTER_MODE` | `http` | `http` serves `/metrics`; `textfile` writes a `.prom` file. |
| `EXPORTER_PORT` | `8080` | Port for the HTTP mode. |
| `TEXTFILE_DIR` | `/var/lib/node_exporter/textfile_collector` | Directory where `.prom` file is written. |
| `TEXTFILE_BASENAME` | `zpa_exporter.prom` | File name inside `TEXTFILE_DIR` for textfile mode. |
| `TEXTFILE_WRITE_INTERVAL` | `15` | Seconds between writes in textfile mode. |

## Usage

### HTTP mode (default)
```bash
python zpa_exporter.py
# Exposes http://0.0.0.0:8080/metrics
```

### Node Exporter textfile collector mode
```bash
export EXPORTER_MODE=textfile
python zpa_exporter.py
# Writes /var/lib/node_exporter/textfile_collector/zpa_exporter.prom every 15s
```

The `.prom` file is created only after the exporter parses at least one
`Mtunnels(...)` log line; the write interval begins after that initial
successful parse.

In both modes the process must be able to read the host's journald logs. In a
container, mount the journal directory (for example `/run/log/journal`) and
ensure `journalctl` is available.

Example metrics excerpt:

```
zpa_mtunnel_current_active{group="all"} 1234
zpa_mtunnel_peak_active 2345
zpa_mtunnel_type{protocol="tcp"} 1.234567e+06
```

## Container image

A GitHub Action builds the Docker image from the provided `Dockerfile` and
publishes it to the GitHub Container Registry (GHCR) at
`ghcr.io/thomasdstewart/zpa-log-exporter`. Pull and run it with:

```bash
docker pull ghcr.io/thomasdstewart/zpa-log-exporter:latest
docker run --rm -p 8080:8080 \
  -v /run/log/journal:/run/log/journal:ro \
  ghcr.io/thomasdstewart/zpa-log-exporter:latest
```

Adjust the bind mount and environment variables as needed for your deployment.

The image expects the host's journald directory to be mounted read-only so it
can stream ZPA connector logs. If you do not use journald, or if your log files
live elsewhere, build a custom image with an alternate log source that emits
`zpa-connector-child` logs.

## Development

Linting and basic hygiene checks are configured via [pre-commit](https://pre-commit.com/).
Install the hook locally and run it across the repository with:

```bash
pip install pre-commit
pre-commit install
pre-commit run --all-files
```

## Running as a system-wide systemd unit with Podman

To run the exporter as a managed service with Podman while ensuring a fresh
container is created on each start, use a `--rm` container with `Restart=no` in a
systemd unit. Place the unit in `/etc/systemd/system/zpa-log-exporter.service`,
run `systemctl daemon-reload`, and enable it with
`systemctl enable --now zpa-log-exporter.service`.

### EXPORTER_MODE=http (default)

Expose the `/metrics` endpoint over HTTP. The example below mounts the host
journal and binds the HTTP port:

```ini
[Unit]
Description=ZPA Log Exporter (Podman - HTTP mode)
Wants=network-online.target
After=network-online.target

[Service]
Environment="PODMAN_SYSTEMD_UNIT=%n"
Restart=no
ExecStartPre=/usr/bin/podman pull ghcr.io/thomasdstewart/zpa-log-exporter:latest
ExecStart=/usr/bin/podman run --rm \
  --name zpa-log-exporter \
  -p 8080:8080 \
  -v /run/log/journal:/run/log/journal:ro \
  ghcr.io/thomasdstewart/zpa-log-exporter:latest
ExecStop=/usr/bin/podman stop --ignore --time=10 zpa-log-exporter

[Install]
WantedBy=multi-user.target
```

### EXPORTER_MODE=textfile

Write metrics to a `.prom` file instead of opening a port. Mount both the host
journal and the textfile directory that Node Exporter will read from (override
`TEXTFILE_DIR` as needed):

```ini
[Unit]
Description=ZPA Log Exporter (Podman - textfile mode)
Wants=network-online.target
After=network-online.target

[Service]
Environment="PODMAN_SYSTEMD_UNIT=%n"
Environment="EXPORTER_MODE=textfile"
Environment="TEXTFILE_DIR=/var/lib/node_exporter/textfile_collector"
Restart=no
ExecStartPre=/usr/bin/podman pull ghcr.io/thomasdstewart/zpa-log-exporter:latest
ExecStart=/usr/bin/podman run --rm \
  --name zpa-log-exporter \
  --env=EXPORTER_MODE \
  --env=TEXTFILE_DIR \
  -v /run/log/journal:/run/log/journal:ro \
  -v /var/lib/node_exporter/textfile_collector:/var/lib/node_exporter/textfile_collector \
  ghcr.io/thomasdstewart/zpa-log-exporter:latest
ExecStop=/usr/bin/podman stop --ignore --time=10 zpa-log-exporter

[Install]
WantedBy=multi-user.target
```
