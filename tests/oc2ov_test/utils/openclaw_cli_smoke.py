"""Fail-fast smoke for the OpenClaw JSON command used by OC2OV P0 tests."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Sequence

try:
    from .cli_diagnostics import compact_subprocess_diagnostic, format_openclaw_cli_failure
except ImportError:  # pragma: no cover - direct workflow script execution
    from cli_diagnostics import compact_subprocess_diagnostic, format_openclaw_cli_failure


def run_smoke(session_id: str, timeout: int = 180) -> None:
    command = [
        "openclaw",
        "agent",
        "--session-id",
        session_id,
        "--message",
        "Reply exactly READY.",
        "--json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            format_openclaw_cli_failure(
                "OpenClaw JSON smoke command failed", result.stderr, result.returncode
            )
        )

    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError(
            format_openclaw_cli_failure(
                "OpenClaw JSON smoke returned empty stdout", result.stderr, result.returncode
            )
        )

    try:
        json.loads(stdout)
    except json.JSONDecodeError as exc:
        diagnostic = compact_subprocess_diagnostic(stdout)
        raise RuntimeError(
            f"OpenClaw JSON smoke returned invalid JSON: {diagnostic}"
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)

    try:
        run_smoke(args.session_id, timeout=args.timeout)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"::error::{exc}")
        return 1

    print("OpenClaw JSON smoke passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
