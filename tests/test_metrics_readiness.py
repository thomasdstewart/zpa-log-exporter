import socket
import sys
import threading
from http.server import HTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zpa_exporter import FIRST_PARSE_DONE, MetricsHandler, MTUNNEL_PEAK_ACTIVE  # noqa: E402


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_metrics_handler_waits_for_parse():
    FIRST_PARSE_DONE.clear()
    port = find_free_port()
    server = HTTPServer(("127.0.0.1", port), MetricsHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()

    try:
        url = f"http://127.0.0.1:{port}/metrics"
        try:
            with urlopen(url) as resp:
                status = resp.getcode()
                body = resp.read().decode()
        except HTTPError as exc:
            status = exc.code
            body = exc.read().decode()

        assert status == 503
        assert body.strip() == "Metrics not yet available"

        MTUNNEL_PEAK_ACTIVE.set(1)
        FIRST_PARSE_DONE.set()

        with urlopen(url) as resp:
            status_ok = resp.getcode()
            body_ok = resp.read().decode()

        assert status_ok == 200
        assert "zpa_mtunnel_peak_active 1.0" in body_ok
    finally:
        server.shutdown()
        thread.join(timeout=2)
        FIRST_PARSE_DONE.clear()
