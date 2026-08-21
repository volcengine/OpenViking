"""Verify that an installed wheel exposes one consistent version."""

import importlib.metadata
import os
import subprocess
from pathlib import Path

import openviking


def main() -> None:
    wheel_version = importlib.metadata.version("openviking")
    package_version = openviking.__version__
    if wheel_version != package_version:
        raise SystemExit(
            f"Wheel metadata version ({wheel_version}) does not match "
            f"openviking.__version__ ({package_version})"
        )

    binary_name = "ov.exe" if os.name == "nt" else "ov"
    bundled_cli = Path(openviking.__file__).resolve().parent / "bin" / binary_name
    if not bundled_cli.is_file():
        raise SystemExit(f"Bundled Rust CLI was not installed at {bundled_cli}")

    output = subprocess.run(
        [str(bundled_cli), "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    cli_version = output.split()[-1]
    if cli_version != wheel_version:
        raise SystemExit(
            f"Bundled Rust CLI version ({cli_version}) does not match "
            f"wheel metadata version ({wheel_version})"
        )

    print(f"Verified wheel, package, and CLI version: {wheel_version}")


if __name__ == "__main__":
    main()
