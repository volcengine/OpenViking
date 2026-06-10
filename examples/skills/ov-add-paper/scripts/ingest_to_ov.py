#!/usr/bin/env python3
"""Validate an ARA paper artifact and ingest it with ov add-resource."""

from __future__ import annotations

import argparse
import json
import os
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


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True)


def target_exists(ov: str, target_uri: str) -> bool:
    return run_capture([ov, "-o", "json", "stat", target_uri]).returncode == 0


def print_target_recovery(ov: str, target_uri: str, timeout: int | None) -> None:
    stat = run_capture([ov, "-o", "json", "stat", target_uri])
    if stat.returncode != 0:
        print(
            f"target recovery check: {target_uri} is not visible after failed ingest",
            file=sys.stderr,
        )
        return

    print(f"target recovery check: {target_uri} exists", file=sys.stderr)
    try:
        payload = json.loads(stat.stdout)
        result = payload.get("result") if isinstance(payload, dict) else None
        if isinstance(result, dict):
            print(
                "target recovery status: "
                f"isLocked={result.get('isLocked')} count={result.get('count')}",
                file=sys.stderr,
            )
    except Exception:
        pass

    if timeout is not None and timeout > 0:
        wait = run_capture([ov, "-o", "json", "wait", "--timeout", str(timeout)])
        if wait.returncode == 0:
            print("target recovery wait: completed", file=sys.stderr)
            return
        print(f"target recovery wait failed: {wait.stderr.strip()}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--to", dest="target_uri", default=None)
    parser.add_argument("--wait", action="store_true", help="wait for OV processing")
    parser.add_argument("--timeout", type=int, default=None, help="timeout seconds for --wait")
    parser.add_argument(
        "--ov-bin",
        default=None,
        help="ov executable path; defaults to OV_BIN or the first ov on PATH",
    )
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

    ov = args.ov_bin or os.environ.get("OV_BIN") or shutil.which("ov")
    if not ov:
        print("ov CLI not found in PATH", file=sys.stderr)
        return 127
    if not shutil.which(ov):
        print(f"ov CLI not found or not executable: {ov}", file=sys.stderr)
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

    if target_exists(ov, target_uri):
        print(
            f"target URI already exists: {target_uri}. "
            "Choose a new --to URI or delete the existing resource explicitly.",
            file=sys.stderr,
        )
        return 3

    code = run(cmd)
    if code != 0:
        print_target_recovery(ov, target_uri, args.timeout if args.wait else None)
    return code


if __name__ == "__main__":
    sys.exit(main())
