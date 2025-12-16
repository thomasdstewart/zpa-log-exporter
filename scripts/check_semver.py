#!/usr/bin/env python3
"""Pre-commit hook to verify files contain valid Semantic Versions."""
from __future__ import annotations

import re
import sys
from pathlib import Path

SEMVER_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def _check_file(path: Path) -> bool:
    content = path.read_text(encoding="utf-8").strip()
    if not SEMVER_REGEX.fullmatch(content):
        print(f"{path}: invalid semantic version '{content}'")
        return False
    return True


def main(argv: list[str]) -> int:
    targets = [Path(arg) for arg in argv] if argv else [Path("VERSION")]

    ok = True
    for target in targets:
        if not target.exists():
            print(f"{target}: file not found")
            ok = False
            continue

        if not _check_file(target):
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
