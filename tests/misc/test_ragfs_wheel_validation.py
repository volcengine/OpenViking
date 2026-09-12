import stat
import zipfile
from pathlib import Path

import pytest

from build_support.ragfs_wheel import WheelValidationError, validate_linux_ragfs_wheel


def _make_wheel(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, contents in members.items():
            archive.writestr(name, contents)
    return path


def _make_readelf(tmp_path: Path, output: str, return_code: int = 0) -> Path:
    readelf = tmp_path / "readelf"
    escaped_output = output.replace("\\", "\\\\").replace('"', '\\"')
    readelf.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "{escaped_output}"\nexit {return_code}\n',
        encoding="utf-8",
    )
    readelf.chmod(readelf.stat().st_mode | stat.S_IXUSR)
    return readelf


def _native_member() -> dict[str, bytes]:
    return {"ragfs_python/ragfs_python.abi3.so": b"not an ELF file"}


def test_clean_linux_ragfs_wheel_passes(tmp_path: Path):
    wheel = _make_wheel(tmp_path / "clean.whl", _native_member())
    readelf = _make_readelf(
        tmp_path,
        "Dynamic section at offset 0x0 contains 1 entry:\n"
        " 0x0000000000000001 (NEEDED) Shared library: [libc.so.6]",
    )

    validate_linux_ragfs_wheel(wheel, readelf_binary=str(readelf))


def test_asan_dependency_is_rejected(tmp_path: Path):
    wheel = _make_wheel(tmp_path / "asan.whl", _native_member())
    readelf = _make_readelf(
        tmp_path,
        " 0x0000000000000001 (NEEDED) Shared library: [libasan.so.8]",
    )

    with pytest.raises(WheelValidationError, match="links the ASan runtime"):
        validate_linux_ragfs_wheel(wheel, readelf_binary=str(readelf))


def test_bundled_asan_runtime_is_rejected_before_readelf(tmp_path: Path):
    members = {
        **_native_member(),
        "ragfs_python.libs/libasan.so.8": b"asan runtime",
    }
    wheel = _make_wheel(tmp_path / "bundled-asan.whl", members)

    with pytest.raises(WheelValidationError, match="packages an ASan runtime"):
        validate_linux_ragfs_wheel(wheel, readelf_binary="does-not-exist")


def test_readelf_failure_is_rejected(tmp_path: Path):
    wheel = _make_wheel(tmp_path / "invalid.whl", _native_member())
    readelf = _make_readelf(tmp_path, "not an ELF file", return_code=1)

    with pytest.raises(WheelValidationError, match="readelf failed"):
        validate_linux_ragfs_wheel(wheel, readelf_binary=str(readelf))
