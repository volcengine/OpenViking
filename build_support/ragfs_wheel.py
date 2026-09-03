"""Validation helpers for the native RAGFS extension in Linux wheels."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Sequence


class WheelValidationError(RuntimeError):
    """Raised when a built RAGFS wheel contains an unsafe native dependency."""


def _is_ragfs_extension(member_name: str) -> bool:
    basename = Path(member_name).name
    return basename == "ragfs_python.pyd" or (
        basename.startswith("ragfs_python.abi3.") and basename.endswith(".so")
    )


def _packaged_asan_members(member_names: Sequence[str]) -> list[str]:
    return [
        name
        for name in member_names
        if Path(name).name.lower().startswith("libasan")
    ]


def _resolve_readelf(readelf_binary: str | None) -> str:
    resolved = readelf_binary or shutil.which("readelf")
    if not resolved:
        raise WheelValidationError(
            "cannot validate the RAGFS wheel: `readelf` was not found; "
            "install binutils before building Linux wheels"
        )
    return resolved


def _readelf_dynamic_section(readelf_binary: str, native_path: Path) -> str:
    try:
        result = subprocess.run(
            [readelf_binary, "-d", str(native_path)],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise WheelValidationError(
            f"cannot execute `{readelf_binary}` while validating {native_path.name}: {exc}"
        ) from exc

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    if result.returncode != 0:
        raise WheelValidationError(
            f"readelf failed for {native_path.name} with exit code {result.returncode}:\n{output}"
        )
    return output


def validate_linux_ragfs_wheel(
    wheel_path: str | Path,
    *,
    readelf_binary: str | None = None,
) -> None:
    """Reject a Linux RAGFS wheel that links or bundles the ASan runtime.

    The RAGFS extension is loaded by Python with ``dlopen``. A native ASan
    dependency is therefore unsafe in a normal release process: the runtime
    is not guaranteed to be first in the initial library list. This check is
    intentionally performed against the wheel archive before it is installed.
    """

    wheel_path = Path(wheel_path)
    if not wheel_path.is_file():
        raise WheelValidationError(f"RAGFS wheel does not exist: {wheel_path}")

    try:
        archive = zipfile.ZipFile(wheel_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise WheelValidationError(f"cannot read RAGFS wheel {wheel_path}: {exc}") from exc

    with archive:
        member_names = archive.namelist()
        packaged_asan = _packaged_asan_members(member_names)
        if packaged_asan:
            names = ", ".join(packaged_asan)
            raise WheelValidationError(
                f"{wheel_path} packages an ASan runtime ({names}); release wheels must not "
                "contain libasan"
            )

        native_members = [name for name in member_names if _is_ragfs_extension(name)]
        if not native_members:
            raise WheelValidationError(
                f"{wheel_path} contains no ragfs_python stable-ABI extension"
            )

        readelf = _resolve_readelf(readelf_binary)
        with tempfile.TemporaryDirectory(prefix="openviking-ragfs-wheel-") as temp_dir:
            temp_root = Path(temp_dir)
            for index, member_name in enumerate(native_members):
                native_path = temp_root / f"ragfs_python-{index}.so"
                native_path.write_bytes(archive.read(member_name))
                dynamic_section = _readelf_dynamic_section(readelf, native_path)
                asan_lines = [
                    line for line in dynamic_section.splitlines() if "asan" in line.lower()
                ]
                if asan_lines:
                    details = "\n".join(asan_lines)
                    raise WheelValidationError(
                        f"{wheel_path} links the ASan runtime from {member_name}; "
                        "release wheels must not reference libasan:\n"
                        f"{details}"
                    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reject Linux RAGFS wheels that link or package libasan."
    )
    parser.add_argument(
        "wheels",
        nargs="+",
        type=Path,
        help="one or more wheel archives to validate",
    )
    parser.add_argument(
        "--readelf",
        dest="readelf_binary",
        help="readelf executable to use (defaults to readelf from PATH)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        for wheel_path in args.wheels:
            validate_linux_ragfs_wheel(
                wheel_path,
                readelf_binary=args.readelf_binary,
            )
            print(f"[OK] {wheel_path}: no libasan dependency or bundled runtime")
    except WheelValidationError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
