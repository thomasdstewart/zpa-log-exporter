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


def build_env(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PATH", "")
    env["PATH"] = f"{BIN_DIR}:{env['PATH']}"
    env["PYTHONUNBUFFERED"] = "1"
    if extra_env:
        env.update(extra_env)
    return env


def start_exporter(env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "zpa-log-exporter.py"],
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


def assert_contains(haystack: str, needle: str) -> None:
    """Assert that *needle* is in *haystack*, printing the haystack on failure."""

    if needle not in haystack:
        # Print to stderr so pytest always captures and shows it.
        print("--- metrics output start ---", file=sys.stderr)
        print(haystack, file=sys.stderr)
        print("--- metrics output end ---", file=sys.stderr)
        assert needle in haystack


def log_process_pipes(proc: subprocess.Popen) -> None:
    """Print stdout/stderr from *proc* to stderr for easier debugging."""

    if proc.stdout:
        out = proc.stdout.read()
        if out:
            print("--- process stdout ---", file=sys.stderr)
            print(out, file=sys.stderr)
    if proc.stderr:
        err = proc.stderr.read()
        if err:
            print("--- process stderr ---", file=sys.stderr)
            print(err, file=sys.stderr)


def test_http_mode_serves_expected_metrics():
    port = find_free_port()
    env = build_env({"EXPORTER_PORT": str(port)})

    proc = start_exporter(env)
    try:
        url = f"http://127.0.0.1:{port}/metrics"
        deadline = time.time() + 10
        body = ""

        while time.time() < deadline:
            if proc.poll() is not None:
                log_process_pipes(proc)
                assert False, "Exporter exited prematurely"
            try:
                with urlopen(url) as resp:
                    body = resp.read().decode()
                if "zpa_mtunnel_total_count" in body:
                    break
            except URLError:
                time.sleep(0.2)

        if not body:
            log_process_pipes(proc)

        expected_lines = [
            'zpa_mtunnel_current_active{group="all"} 1234.0',
            "zpa_mtunnel_peak_active 2345.0",
            'zpa_mtunnel_type{protocol="tcp"} 1.234567e+06',
        ]

        for line in expected_lines:
            assert_contains(body, line)
    finally:
        wait_for_process_exit(proc)
