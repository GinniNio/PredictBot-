"""Verify a built wheel against a hash-pinned release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    actual = hashlib.sha256(args.wheel.read_bytes()).hexdigest()
    expected = manifest["package_sha256"].removeprefix("sha256:")
    if actual != expected:
        raise SystemExit(f"SHA-256 mismatch: expected {expected}, got {actual}")
    print(f"verified sha256:{actual}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
