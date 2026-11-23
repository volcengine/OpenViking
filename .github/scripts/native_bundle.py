"""Pack/restore same-run test binaries, validating provenance and stable ABI."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import sysconfig
import tarfile
from pathlib import Path

FEATURES = "ragfs:default(extension-module,s3);engine:sse3,avx2,avx512;cli:default"
ABI = "cp310-abi3"


def runtime_libc():
    if platform.system() == "Linux":
        return os.confstr("CS_GNU_LIBC_VERSION")
    return list(platform.libc_ver())


def identity(root):
    return {
        "schema": 1,
        "sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "os": platform.system(),
        "arch": platform.machine().lower(),
        # A Ubuntu 24 test bundle is never used by the Ubuntu 20 wheel builder.
        "platform": sysconfig.get_platform(),
        "libc": runtime_libc(),
        "abi": ABI,
        "features": FEATURES,
        "build_options": {
            key: os.environ.get(key, "")
            for key in (
                "OV_X86_BUILD_VARIANTS",
                "CARGO_BUILD_TARGET",
                "RUSTFLAGS",
                "CFLAGS",
                "CXXFLAGS",
            )
        },
    }


def allowed(path):
    parts = Path(path).parts
    return (
        (len(parts) == 3 and parts[:2] == ("openviking", "bin") and parts[2] in ("ov", "ov.exe"))
        or (
            len(parts) == 3
            and parts[:2] == ("openviking", "lib")
            and parts[2].startswith("ragfs_python.abi3.")
            and parts[2].endswith((".so", ".pyd"))
        )
        or (
            len(parts) == 5
            and parts[:4] == ("openviking", "storage", "vectordb", "engine")
            and parts[4].startswith("_")
            and parts[4].endswith((".abi3.so", ".abi3.pyd"))
        )
    )


def pack(root, archive):
    files = sorted(
        path
        for path in (root / "openviking").rglob("*")
        if path.is_file() and allowed(path.relative_to(root).as_posix())
    )
    names = [path.relative_to(root).as_posix() for path in files]
    if not any("/lib/" in name for name in names):
        raise ValueError("Missing stable-ABI ragfs binding")
    if not any("/engine/" in name for name in names):
        raise ValueError("Missing stable-ABI engine")
    if not any("/bin/" in name for name in names):
        raise ValueError("Missing ov CLI")
    manifest = identity(root)
    manifest["files"] = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in zip(names, files, strict=True)
    }
    manifest_path = archive.with_suffix(".json")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(manifest_path, arcname="manifest.json")
        for name, path in zip(names, files, strict=True):
            bundle.add(path, arcname=name, recursive=False)


def restore(root, archive):
    if (
        sys.implementation.name != "cpython"
        or sys.version_info < (3, 10)
        or sysconfig.get_config_var("Py_GIL_DISABLED")
    ):
        raise ValueError("Bundle requires CPython >= 3.10")
    with tarfile.open(archive) as bundle:
        manifest = json.load(bundle.extractfile("manifest.json"))
        expected = identity(root)
        for key, value in expected.items():
            if manifest.get(key) != value:
                raise ValueError(f"Incompatible bundle {key}: {manifest.get(key)!r} != {value!r}")
        members = [member for member in bundle.getmembers() if member.name != "manifest.json"]
        if len(members) != len(manifest["files"]):
            raise ValueError("Bundle file count does not match manifest")
        validated = {}
        for member in members:
            if not member.isfile() or not allowed(member.name) or member.name in validated:
                raise ValueError(f"Invalid bundle member: {member.name}")
            data = bundle.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != manifest["files"].get(member.name):
                raise ValueError(f"Bundle checksum mismatch: {member.name}")
            validated[member.name] = data
        # Validate everything before changing the package tree.
        for name, data in validated.items():
            destination = root / name
            if not destination.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"Bundle destination escapes checkout: {name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            os.chmod(destination, 0o755)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["pack", "restore"])
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    globals()[args.operation](Path.cwd(), args.archive)


if __name__ == "__main__":
    main()
