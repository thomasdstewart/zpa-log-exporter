# zpa-log-exporter

[![Tests](https://github.com/thomasdstewart/zpa-log-exporter/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/thomasdstewart/zpa-log-exporter/actions/workflows/tests.yml)
[![Lint](https://github.com/thomasdstewart/zpa-log-exporter/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/thomasdstewart/zpa-log-exporter/actions/workflows/lint.yml)
[![Publish](https://github.com/thomasdstewart/zpa-log-exporter/actions/workflows/publish.yml/badge.svg?branch=main)](https://github.com/thomasdstewart/zpa-log-exporter/actions/workflows/publish.yml)

Prometheus exporter for Zscaler ZPA App Connectors. The exporter tails
`journalctl -f -t zpa-connector-child` and parses `Mtunnels(...)` log lines to
produce metrics.

The exporter exposes metrics via an HTTP endpoint on `/metrics`.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `EXPORTER_PORT` | `8080` | Port for the HTTP metrics endpoint. |

## Usage

```bash
python zpa-log-exporter.py
# Exposes http://0.0.0.0:8080/metrics
```

The process must be able to read the host's journald logs. In a
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

## Development

Linting and basic hygiene checks are configured via [pre-commit](https://pre-commit.com/).
Install the hook locally and run it across the repository with:

```bash
apt-get install pre-commit
pre-commit install
pre-commit run --all-files
```

## Release automation

To cut a new SemVer release and trigger a tagged Docker build:

1. Open the **Actions** tab in GitHub and select the **Release** workflow.
2. Click **Run workflow**, choose the bump type (`patch`, `minor`, or `major`),
   and run it against the `main` branch (the default branch).

The workflow uses [anothrNick/github-tag-action](https://github.com/anothrNick/github-tag-action)
to bump the next `vX.Y.Z` tag and [softprops/action-gh-release](https://github.com/softprops/action-gh-release)
to publish a GitHub release with generated notes. The new tag automatically
triggers the container publish workflow so images for the release are built and
pushed, including the moving `latest` tag and the versioned tag produced by the
release.
The project uses [Semantic Versioning](https://semver.org/); the current version
is recorded in the `VERSION` file and enforced by a pre-commit hook. Update the
version in `VERSION` when making releases (for example, `1.2.3` or
`1.2.3-rc.1+build`).

## Running as a system-wide systemd unit with Podman

To run the exporter as a managed service with Podman place the unit in `/etc/systemd/system/zpa-log-exporter.service`,
run `systemctl daemon-reload`, and enable it with
`systemctl enable --now zpa-log-exporter.service`. This will expose the `/metrics` endpoint over HTTP it mounts the host
journal and binds the HTTP port.

```ini
[Unit]
Description=ZPA Log Exporter
Wants=network-online.target
After=network-online.target

[Service]
Environment="PODMAN_SYSTEMD_UNIT=%n"
Restart=no
ExecStart=/usr/bin/podman run --rm --pull=always --replace --label io.containers.autoupdate=image \
  --name zpa-log-exporter --publish 8080:8080 --volume /run/log/journal:/run/log/journal:ro \
  ghcr.io/thomasdstewart/zpa-log-exporter:latest
ExecStop=/usr/bin/podman rm --ignore --force --time=10 zpa-log-exporter

[Install]
WantedBy=multi-user.target
```
