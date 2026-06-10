#!/usr/bin/env python3
"""Validate an ARA paper artifact and ingest it with ov add-resource."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def default_target(artifact_dir: Path) -> str:
    slug = artifact_dir.name.strip().lower().replace(" ", "-")
    slug = "".join(ch for ch in slug if ch.isalnum() or ch in "-_./")
    slug = slug.strip("-_/") or "paper"
    return f"viking://resources/papers/{slug}"


def run(cmd: list[str], cwd: Path | None = None) -> int:
    print("+ " + " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--to", dest="target_uri", default=None)
    parser.add_argument("--wait", action="store_true", help="wait for OV processing")
    parser.add_argument("--timeout", type=int, default=None, help="timeout seconds for --wait")
    parser.add_argument("--skip-validation", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    artifact_dir = args.artifact_dir.resolve()
    if not artifact_dir.exists() or not artifact_dir.is_dir():
        print(f"artifact directory not found: {artifact_dir}", file=sys.stderr)
        return 2

    script_dir = Path(__file__).resolve().parent
    validator = script_dir / "validate_ara.py"
    if not args.skip_validation:
        code = run([sys.executable, str(validator), str(artifact_dir)])
        if code != 0:
            print("validation failed; not running ov add-resource", file=sys.stderr)
            return code

    ov = shutil.which("ov")
    if not ov:
        print("ov CLI not found in PATH", file=sys.stderr)
        return 127

    target_uri = args.target_uri or default_target(artifact_dir)
    cmd = [ov, "add-resource", str(artifact_dir), "--to", target_uri]
    if args.wait:
        cmd.append("--wait")
        if args.timeout is not None:
            cmd.extend(["--timeout", str(args.timeout)])

    if args.dry_run:
        print("+ " + " ".join(cmd))
        return 0

    return run(cmd)


if __name__ == "__main__":
    sys.exit(main())
