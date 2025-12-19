#!/usr/bin/env python3
"""
Copyright (C) 2025 Thomas Stewart <thomas@stewarts.org.uk>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.

  Zscaler ZPA App Connector Prometheus exporter.
  - Tails journald for zpa-connector-child messages.
  - Parses Mtunnels(...) metrics lines.
  - Exposes metrics via /metrics on port 8080.
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# prometheus_client is preferred, but in constrained environments (e.g. offline
# tests) it might not be installed. Fall back to a tiny local implementation
# that supports the subset of functionality we use.
try:  # pragma: no cover - exercised indirectly by integration tests
    from prometheus_client import (  # type: ignore
        Gauge,
        generate_latest,
        CONTENT_TYPE_LATEST,
        REGISTRY,
    )
except ModuleNotFoundError:  # pragma: no cover - simple runtime fallback
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

    class _SimpleGauge:
        def __init__(self, name: str, documentation: str, labelnames=None):
            self.name = name
            self.documentation = documentation
            self.labelnames = list(labelnames or [])
            self._children: dict[tuple[str, ...], _SimpleGauge] = {}
            self._value: float | None = None
            REGISTRY._register(self)  # type: ignore[attr-defined]

        # Mimic prometheus_client Gauge.labels
        def labels(self, **kwargs):
            key = tuple(kwargs.get(label, "") for label in self.labelnames)
            if key not in self._children:
                child = _SimpleGauge(self.name, self.documentation, self.labelnames)
                child._labels = kwargs
                self._children[key] = child
            return self._children[key]

        def set(self, value: float) -> None:
            self._value = float(value)

        def samples(self):
            if self._children:
                for child in self._children.values():
                    if child._value is not None:
                        yield child._labels, child._value
            elif self._value is not None:
                yield {}, self._value

    class _SimpleRegistry:
        def __init__(self):
            self._metrics: list[_SimpleGauge] = []

        def _register(self, metric: _SimpleGauge) -> None:
            self._metrics.append(metric)

        def collect(self):
            return self._metrics

    def generate_latest(registry: "_SimpleRegistry" = None) -> bytes:
        registry = registry or REGISTRY
        lines: list[str] = []
        for metric in registry.collect():
            lines.append(f"# HELP {metric.name} {metric.documentation}")
            lines.append(f"# TYPE {metric.name} gauge")
            for labels, value in metric.samples():
                if labels:
                    label_str = ",".join(
                        f'{k}="{v}"' for k, v in sorted(labels.items())
                    )
                    lines.append(f"{metric.name}{{{label_str}}} {value}")
                else:
                    lines.append(f"{metric.name} {value}")
        lines.append("")
        return "\n".join(lines).encode()

    Gauge = _SimpleGauge
    REGISTRY = _SimpleRegistry()

# ---------------------------------------------------------------------------
# Configuration (can be overridden via environment variables if desired)
# ---------------------------------------------------------------------------

# The syslog identifier is fixed to the ZPA connector child process and is not
# configurable via environment variables.
JOURNAL_SYSLOG_IDENTIFIER = "zpa-connector-child"
EXPORTER_PORT = int(os.environ.get("EXPORTER_PORT", "8080"))

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

# By convention, use lowercase + underscores, with a component prefix.
# Using Gauges as we are reading latest values from logs, not directly
# instrumenting internal counters.

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

MTUNNEL_TYPE = Gauge(
    "zpa_mtunnel_type",
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

# Signals the first successful mtunnel parse so the /metrics endpoint can
# avoid returning a payload without mtunnel samples.
FIRST_MTUNNEL_SCRAPE_READY = threading.Event()

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

        # Avoid serving a payload with only HELP/TYPE metadata before any
        # Mtunnels samples have been seen. Wait briefly for the first parse
        # before responding to the caller.
        if not FIRST_MTUNNEL_SCRAPE_READY.wait(timeout=1.0):
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Mtunnel metrics not yet available\n")
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
            # raising HTTP 500s constantly.
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

    # Group-based metrics (values separated by "|")
    grouped_patterns = [
        (r"current active\s+([\d|]+)", MTUNNEL_CURRENT_ACTIVE),
        (r"total\s+([\d|]+)", MTUNNEL_TOTAL),
        (r"to broker\s+([\d|]+)", MTUNNEL_TO_BROKER),
        (r"to private broker\s+([\d|]+)", MTUNNEL_TO_PRIVATE_BROKER),
    ]

    for pattern, gauge in grouped_patterns:
        m = re.search(pattern, rest)
        if m:
            values = [int(v) for v in m.group(1).split("|") if v]
            for grp, val in zip(groups, values):
                gauge.labels(group=grp).set(val)

    # Scalar metrics
    scalar_patterns = [
        (r"unbound/errored\s+(\d+)", MTUNNEL_UNBOUND_ERRORED),
        (r"peak active\s+(\d+)\s+at cloud time", MTUNNEL_PEAK_ACTIVE),
        (r"total mtunnel alloc\s+(\d+)", MTUNNEL_TOTAL_ALLOC),
        (r"total mtunnel free\s+(\d+)", MTUNNEL_TOTAL_FREE),
        (r"reaped\s+(\d+)", MTUNNEL_REAPED),
    ]

    for pattern, gauge in scalar_patterns:
        m = re.search(pattern, rest)
        if m:
            gauge.set(int(m.group(1)))

    # types(tcp|udp|icmp|mtls|de|tcp_de|udp_de) 1173242|157354|95|0|0|0|0
    m = re.search(r"types\(([^)]+)\)\s+([\d|]+)", rest)
    if m:
        proto_str, values_str = m.groups()
        protos = [p.strip() for p in proto_str.split("|")]
        values = [int(v) for v in values_str.split("|")]
        for proto, val in zip(protos, values):
            MTUNNEL_TYPE.labels(protocol=proto).set(val)

    FIRST_MTUNNEL_SCRAPE_READY.set()

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
        "[INFO] Starting journalctl tail for zpa-connector-child\n"
    )

    while True:
        try:
            proc = subprocess.Popen(
                [
                    "journalctl",
                    "-f",  # follow
                    "-o",
                    "cat",  # message only
                    "-t",
                    "zpa-connector-child",
                ],
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
    http_thread = threading.Thread(
        target=run_http_server,
        kwargs={"port": EXPORTER_PORT},
        daemon=True,
    )
    http_thread.start()

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
