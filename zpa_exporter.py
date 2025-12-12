#!/usr/bin/env python3
"""
Zscaler ZPA App Connector Prometheus exporter.

- Tails journald (journalctl -f) for zpa-connector-child messages.
- Parses Mtunnels(...) metrics lines.
- Exposes metrics via /metrics on port 8080 in Prometheus format **or** writes
  a
  Prometheus textfile collector `.prom` file for consumption by the Node
  Exporter.

Requires:
    pip install prometheus_client

Run:
    python zpa_exporter.py

In a container you’ll typically:
    - Mount the host journal (e.g. /run/log/journal or /var/log/journal)
    - Ensure journalctl is available in the image
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from prometheus_client import (
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

# ---------------------------------------------------------------------------
# Configuration (can be overridden via environment variables if desired)
# ---------------------------------------------------------------------------

JOURNAL_SYSLOG_IDENTIFIER = os.environ.get(
    "ZPA_SYSLOG_IDENTIFIER", "zpa-connector-child"
)
EXPORTER_MODE = os.environ.get("EXPORTER_MODE", "http").lower()
EXPORTER_PORT = int(os.environ.get("EXPORTER_PORT", "8080"))
TEXTFILE_DIR = os.environ.get("TEXTFILE_DIR")
TEXTFILE_BASENAME = os.environ.get("TEXTFILE_BASENAME", "zpa_exporter.prom")
TEXTFILE_WRITE_INTERVAL = float(
    os.environ.get("TEXTFILE_WRITE_INTERVAL", "15")
)
JOURNAL_CMD = [
    "journalctl",
    "-f",          # follow
    "-o", "cat",   # message only
    "-t", JOURNAL_SYSLOG_IDENTIFIER,
]

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

# By convention, use lowercase + underscores, with a component prefix.
# Using Gauges as we are reading latest values from logs, not directly
# instrumenting internal counters. 2

MTUNNEL_CURRENT_ACTIVE = Gauge(
    "zpa_mtunnel_current_active",
    "Current active mtunnels per group",
    ["group"],
)

MTUNNEL_TOTAL = Gauge(
    "zpa_mtunnel_total_count",
    "Total mtunnels per group (as reported by ZPA)",
    ["group"],
)

MTUNNEL_TO_BROKER = Gauge(
    "zpa_mtunnel_to_broker_count",
    "Mtunnels to broker per group",
    ["group"],
)

MTUNNEL_TO_PRIVATE_BROKER = Gauge(
    "zpa_mtunnel_to_private_broker_count",
    "Mtunnels to private broker per group",
    ["group"],
)

MTUNNEL_UNBOUND_ERRORED = Gauge(
    "zpa_mtunnel_unbound_errored_count",
    "Unbound/errored mtunnels (total)",
)

MTUNNEL_PEAK_ACTIVE = Gauge(
    "zpa_mtunnel_peak_active",
    "Peak active mtunnels (all groups)",
)

MTUNNEL_TOTAL_ALLOC = Gauge(
    "zpa_mtunnel_total_alloc_count",
    "Total mtunnel allocations",
)

MTUNNEL_TOTAL_FREE = Gauge(
    "zpa_mtunnel_total_free_count",
    "Total mtunnel frees",
)

MTUNNEL_TYPE_COUNT = Gauge(
    "zpa_mtunnel_type_count",
    "Mtunnel counts by protocol type",
    ["protocol"],
)

MTUNNEL_REAPED = Gauge(
    "zpa_mtunnel_reaped_count",
    "Reaped mtunnels (as reported by ZPA)",
)

EXPORTER_LAST_SCRAPE_ERROR = Gauge(
    "zpa_exporter_last_scrape_error",
    "1 if the last journal parse had an error, 0 otherwise",
)

# ---------------------------------------------------------------------------
# HTTP handler (serves /metrics using prometheus_client)
# ---------------------------------------------------------------------------


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not found\n")
            return

        try:
            output = generate_latest(REGISTRY)
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)
        except Exception as exc:  # noqa: BLE001
            # Best practice: expose exporter errors as a metric rather than
            # raising HTTP 500s constantly. 3
            EXPORTER_LAST_SCRAPE_ERROR.set(1)
            sys.stderr.write(f"[ERROR] Failed to generate metrics: {exc}\n")
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Internal error\n")
        else:
            EXPORTER_LAST_SCRAPE_ERROR.set(0)

    def log_message(self, fmt, *args):  # noqa: D401
        """Silence default HTTP request logging to stdout."""
        return


def run_http_server(port: int):
    server = HTTPServer(("0.0.0.0", port), MetricsHandler)
    # If you prefer to avoid single-threaded HTTP, swap to ThreadingHTTPServer:
    # from socketserver import ThreadingMixIn
    # class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    #     daemon_threads = True
    # server = ThreadingHTTPServer(("0.0.0.0", port), MetricsHandler)

    sys.stderr.write(
        f"[INFO] Exporter HTTP server listening on :{port}/metrics\n"
    )
    server.serve_forever()


def write_metrics_to_textfile(directory: str, filename: str) -> None:
    """Render the current metrics registry to a textfile collector file."""

    os.makedirs(directory, exist_ok=True)

    output = generate_latest(REGISTRY)
    temp_path = os.path.join(directory, f".{filename}.tmp")
    final_path = os.path.join(directory, filename)

    with open(temp_path, "wb") as handle:
        handle.write(output)

    # Atomic replace to avoid node_exporter reading partial files.
    os.replace(temp_path, final_path)


def run_textfile_writer(
    directory: str,
    filename: str,
    interval_seconds: float,
) -> None:
    """Periodically write metrics to a Prometheus textfile collector.

    Intended for Prometheus textfile collector locations.
    """

    sys.stderr.write(
        "[INFO] Exporter textfile writer enabled; writing metrics to "
        f"{os.path.join(directory, filename)} every {interval_seconds}s.\n"
    )

    while True:
        try:
            write_metrics_to_textfile(directory, filename)
        except Exception as exc:  # noqa: BLE001
            EXPORTER_LAST_SCRAPE_ERROR.set(1)
            sys.stderr.write(
                f"[ERROR] Failed to write metrics textfile: {exc}\n"
            )
        else:
            EXPORTER_LAST_SCRAPE_ERROR.set(0)

        time.sleep(interval_seconds)


# ---------------------------------------------------------------------------
# Journald parsing
# ---------------------------------------------------------------------------

def parse_mtunnels_line(line: str) -> None:
    """
    Parse an Mtunnels(...) line and update Prometheus metrics.

    Example prefix:
        Mtunnels(all|health-report-based|no-health-report-based), ...
    """
    m = re.match(r"^Mtunnels\(([^)]+)\),\s*(.*)$", line)
    if not m:
        return

    group_str, rest = m.groups()
    groups = [g.strip() for g in group_str.split("|")]

    # Split remaining parts by comma; each part is one logical section.
    parts = [p.strip() for p in rest.split(",") if p.strip()]

    for part in parts:
        # current active 1360|1360|0
        m = re.search(r"^current active\s+([\d|]+)", part)
        if m:
            values = [int(v) for v in m.group(1).split("|")]
            for grp, val in zip(groups, values):
                MTUNNEL_CURRENT_ACTIVE.labels(group=grp).set(val)
            continue

        # total 1330691|1330596|95
        m = re.search(r"^total\s+([\d|]+)$", part)
        if m:
            values = [int(v) for v in m.group(1).split("|")]
            for grp, val in zip(groups, values):
                MTUNNEL_TOTAL.labels(group=grp).set(val)
            continue

        # to broker 1325965|1325870|95
        m = re.search(r"^to broker\s+([\d|]+)$", part)
        if m:
            values = [int(v) for v in m.group(1).split("|")]
            for grp, val in zip(groups, values):
                MTUNNEL_TO_BROKER.labels(group=grp).set(val)
            continue

        # to private broker 0|0|0
        m = re.search(r"^to private broker\s+([\d|]+)$", part)
        if m:
            values = [int(v) for v in m.group(1).split("|")]
            for grp, val in zip(groups, values):
                MTUNNEL_TO_PRIVATE_BROKER.labels(group=grp).set(val)
            continue

        # unbound/errored 4726
        m = re.search(r"^unbound/errored\s+(\d+)$", part)
        if m:
            MTUNNEL_UNBOUND_ERRORED.set(int(m.group(1)))
            continue

        # peak active 2041 at cloud time 1765276725308313 us
        m = re.search(
            r"^peak active\s+(\d+)\s+at cloud time\s+(\d+)\s+us$",
            part,
        )
        if m:
            peak_val = int(m.group(1))
            # cloud time is an opaque counter here; we expose the peak count,
            # and you can add a separate metric if you care about the
            # timestamp.
            MTUNNEL_PEAK_ACTIVE.set(peak_val)
            continue

        # total mtunnel alloc 1330691
        m = re.search(r"^total mtunnel alloc\s+(\d+)$", part)
        if m:
            MTUNNEL_TOTAL_ALLOC.set(int(m.group(1)))
            continue

        # total mtunnel free 1329084
        m = re.search(r"^total mtunnel free\s+(\d+)$", part)
        if m:
            MTUNNEL_TOTAL_FREE.set(int(m.group(1)))
            continue

        # types(tcp|udp|icmp|mtls|de|tcp_de|udp_de) 1173242|157354|95|0|0|0|0
        m = re.search(r"^types\(([^)]+)\)\s+([\d|]+)$", part)
        if m:
            proto_str, values_str = m.groups()
            protos = [p.strip() for p in proto_str.split("|")]
            values = [int(v) for v in values_str.split("|")]
            for proto, val in zip(protos, values):
                MTUNNEL_TYPE_COUNT.labels(protocol=proto).set(val)
            continue

        # reaped 0
        m = re.search(r"^reaped\s+(\d+)$", part)
        if m:
            MTUNNEL_REAPED.set(int(m.group(1)))
            continue

        # TODO: extend parsing for waf/adp/auto/active inspection, pipeline
        # status, websocket stats, api traffic stats, etc. using additional
        # metrics.


def handle_log_message(msg: str) -> None:
    """Dispatch log lines to the relevant parsers."""
    msg = msg.strip()
    if not msg:
        return

    # For now we only care about Mtunnels(...) lines
    if msg.startswith("Mtunnels("):
        try:
            parse_mtunnels_line(msg)
        except Exception as exc:  # noqa: BLE001
            EXPORTER_LAST_SCRAPE_ERROR.set(1)
            sys.stderr.write(f"[ERROR] Failed to parse Mtunnels line: {exc}\n")


def tail_journal_forever():
    """
    Tail journald using journalctl -f and process lines.

    If journalctl exits for some reason, we back off briefly and restart.
    """
    sys.stderr.write(
        "[INFO] Starting journalctl tail for "
        f"SYSLOG_IDENTIFIER={JOURNAL_SYSLOG_IDENTIFIER}\n"
    )

    while True:
        try:
            proc = subprocess.Popen(
                JOURNAL_CMD,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
        except FileNotFoundError:
            sys.stderr.write(
                "[FATAL] journalctl not found in container/image. "
                "Install systemd (or at least journalctl) or run this "
                "exporter on the host.\n"
            )
            sys.exit(1)

        try:
            for line in proc.stdout:
                handle_log_message(line)
        except KeyboardInterrupt:
            proc.terminate()
            break
        finally:
            # If journalctl exits unexpectedly, log and retry
            ret = proc.poll()
            if ret is not None and ret != 0:
                err = proc.stderr.read()
                sys.stderr.write(
                    f"[WARN] journalctl exited with {ret}. stderr: {err}\n"
                )
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

        # Back off briefly before restarting journalctl
        time.sleep(2)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    # Start metrics exposure according to requested mode
    if EXPORTER_MODE == "http":
        http_thread = threading.Thread(
            target=run_http_server,
            kwargs={"port": EXPORTER_PORT},
            daemon=True,
        )
        http_thread.start()
    elif EXPORTER_MODE == "textfile":
        if not TEXTFILE_DIR:
            sys.stderr.write(
                "[FATAL] TEXTFILE_DIR must be set when "
                "EXPORTER_MODE=textfile.\n"
            )
            sys.exit(1)

        writer_thread = threading.Thread(
            target=run_textfile_writer,
            kwargs={
                "directory": TEXTFILE_DIR,
                "filename": TEXTFILE_BASENAME,
                "interval_seconds": TEXTFILE_WRITE_INTERVAL,
            },
            daemon=True,
        )
        writer_thread.start()
    else:
        sys.stderr.write(
            "[FATAL] Unknown EXPORTER_MODE. Use 'http' (default) or "
            "'textfile'.\n"
        )
        sys.exit(1)

    # Handle SIGTERM/SIGINT cleanly
    def _signal_handler(signum, frame):  # noqa: ARG001
        sys.stderr.write(f"[INFO] Received signal {signum}, exiting.\n")
        sys.exit(0)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Block in journal tail loop
    tail_journal_forever()


if __name__ == "__main__":
    main()
