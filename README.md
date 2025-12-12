# zpa-log-exporter

Prometheus exporter for Zscaler ZPA App Connectors. The exporter tails
`journalctl -f -t zpa-connector-child` and parses `Mtunnels(...)` log lines to
produce metrics.

The exporter can expose metrics in two ways:

- HTTP endpoint on `/metrics` (default mode)
- Prometheus textfile collector output for consumption by the Node Exporter

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `ZPA_SYSLOG_IDENTIFIER` | `zpa-connector-child` | Syslog identifier to follow in journald. |
| `EXPORTER_MODE` | `http` | `http` serves `/metrics`; `textfile` writes a `.prom` file. |
| `EXPORTER_PORT` | `8080` | Port for the HTTP mode. |
| `TEXTFILE_DIR` | _required for textfile mode_ | Directory where `.prom` file is written. |
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
export TEXTFILE_DIR=/var/lib/node_exporter/textfile_collector
python zpa_exporter.py
# Writes /var/lib/node_exporter/textfile_collector/zpa_exporter.prom every 15s
```

In both modes the process must be able to read the host's journald logs. In a
container, mount the journal directory (for example `/run/log/journal`) and
ensure `journalctl` is available.
