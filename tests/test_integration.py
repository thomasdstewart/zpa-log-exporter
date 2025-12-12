import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


tests_dir = Path(__file__).resolve().parent
BIN_DIR = tests_dir / "bin"
SAMPLE_LINE = (tests_dir / "data" / "sample_mtunnels_line.txt").read_text().strip()


def build_env(mode: str, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PATH", "")
    env["PATH"] = f"{BIN_DIR}:{env['PATH']}"
    env["EXPORTER_MODE"] = mode
    env["TEXTFILE_WRITE_INTERVAL"] = "0.2"
    env["PYTHONUNBUFFERED"] = "1"
    env["ZPA_SYSLOG_IDENTIFIER"] = "zpa-connector-child"
    if extra_env:
        env.update(extra_env)
    return env


def start_exporter(env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "zpa_exporter.py"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def wait_for_process_exit(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_textfile_mode_writes_expected_metrics(tmp_path: Path):
    prom_path = tmp_path / "zpa_exporter.prom"
    env = build_env(
        "textfile",
        {
            "TEXTFILE_DIR": str(tmp_path),
            "TEXTFILE_BASENAME": prom_path.name,
        },
    )

    proc = start_exporter(env)
    try:
        deadline = time.time() + 10
        content = ""
        while time.time() < deadline:
            if prom_path.exists():
                content = prom_path.read_text()
                if SAMPLE_LINE.split(",", 1)[0] in content or "zpa_mtunnel_total_count" in content:
                    break
            time.sleep(0.2)

        assert prom_path.exists(), "Exporter did not create the textfile output"
        assert 'zpa_mtunnel_total_count{group="all"} 1234567.0' in content
        assert 'zpa_mtunnel_current_active{group="no-health-report-based"} 0.0' in content
        assert 'zpa_mtunnel_type_count{protocol="icmp"} 12.0' in content
        assert "zpa_mtunnel_unbound_errored_count 1234.0" in content
        assert "zpa_mtunnel_peak_active 2345.0" in content
    finally:
        wait_for_process_exit(proc)


def test_http_mode_serves_expected_metrics():
    port = find_free_port()
    env = build_env("http", {"EXPORTER_PORT": str(port)})

    proc = start_exporter(env)
    try:
        url = f"http://127.0.0.1:{port}/metrics"
        deadline = time.time() + 10
        body = ""

        while time.time() < deadline:
            try:
                with urlopen(url) as resp:
                    body = resp.read().decode()
                if "zpa_mtunnel_total_count" in body:
                    break
            except URLError:
                time.sleep(0.2)

        assert 'zpa_mtunnel_total_count{group="health-report-based"} 1234567.0' in body
        assert "zpa_mtunnel_unbound_errored_count 1234.0" in body
    finally:
        wait_for_process_exit(proc)
