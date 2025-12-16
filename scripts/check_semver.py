#!/usr/bin/env python3
"""Ensure VERSION file contains a valid semantic version."""
from __future__ import annotations

import re
from pathlib import Path

SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def main() -> int:
    version_path = Path("VERSION")
    if not version_path.exists():
        print("VERSION file not found.")
        return 1

    version = version_path.read_text().strip()
    if not version:
        print("VERSION file is empty.")
        return 1

    if not SEMVER_PATTERN.match(version):
        print(
            "Invalid semantic version. "
            "Expected MAJOR.MINOR.PATCH with optional pre-release/build metadata."
        )
        print(f"Found: '{version}'")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
