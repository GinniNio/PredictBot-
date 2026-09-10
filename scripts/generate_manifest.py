"""Generate a completed release-manifest.json from the template.

This script fills in the placeholder fields in release-manifest.template.json
with values computed from the actual built wheel on disk, the current git
commit, and the package version declared in pyproject.toml. It never invents
or reuses a stale hash: the SHA-256 is always recalculated from the wheel
bytes at generation time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - repo requires-python >=3.10
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[1]


def read_project_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return data["project"]["version"]


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def generate_manifest(
    *,
    template_path: Path,
    wheel_path: Path,
    commit_sha: str,
    pyproject_path: Path,
) -> dict:
    manifest = json.loads(template_path.read_text(encoding="utf-8"))

    manifest["artifact_version"] = read_project_version(pyproject_path)
    manifest["git_commit"] = commit_sha
    manifest["package_file"] = wheel_path.name
    manifest["package_sha256"] = f"sha256:{sha256_of_file(wheel_path)}"
    manifest["classification_ceiling"] = "RESEARCH-MODEL"
    manifest["cash_admitted"] = False

    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template",
        type=Path,
        default=REPO_ROOT / "release-manifest.template.json",
        help="Path to the placeholder manifest template",
    )
    parser.add_argument("--wheel", type=Path, required=True, help="Path to the built wheel")
    parser.add_argument("--commit-sha", required=True, help="Full commit SHA CI is running on")
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=REPO_ROOT / "pyproject.toml",
        help="Path to pyproject.toml (source of artifact_version)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "release-manifest.json",
        help="Where to write the completed manifest",
    )
    args = parser.parse_args(argv)

    if not args.wheel.is_file():
        raise SystemExit(f"Wheel not found: {args.wheel}")

    manifest = generate_manifest(
        template_path=args.template,
        wheel_path=args.wheel,
        commit_sha=args.commit_sha,
        pyproject_path=args.pyproject,
    )

    rendered = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
