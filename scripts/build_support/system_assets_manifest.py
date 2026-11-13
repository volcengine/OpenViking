"""Generate the packaged system-assets manifest."""

import hashlib
import json
from pathlib import Path

from openviking.core.namespace_constants import (
    SYSTEM_ASSET_ROOT_MAPPINGS,
    SYSTEM_FIXED_DIRECTORIES,
)

MANIFEST_FILENAME = "manifest.json"
MANIFEST_SCHEMA_VERSION = 1


def _update_digest(digest, path: bytes, content: bytes) -> None:
    digest.update(len(path).to_bytes(8, "big"))
    digest.update(path)
    digest.update(len(content).to_bytes(8, "big"))
    digest.update(content)


def build_system_assets_manifest(assets_dir: Path) -> bytes:
    """Return the canonical manifest for all managed assets."""
    digest = hashlib.sha256()
    for uri in sorted(SYSTEM_FIXED_DIRECTORIES):
        _update_digest(digest, uri.encode("utf-8"), b"")

    for relative_root, target_root in SYSTEM_ASSET_ROOT_MAPPINGS:
        source_root = assets_dir / relative_root
        if not source_root.is_dir():
            raise FileNotFoundError(f"packaged system asset directory not found: {source_root}")
        for source_path in sorted(path for path in source_root.rglob("*") if path.is_file()):
            relative_path = source_path.relative_to(source_root).as_posix()
            target_uri = f"{target_root}/{relative_path}"
            _update_digest(digest, target_uri.encode("utf-8"), source_path.read_bytes())

    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "sha256": digest.hexdigest(),
    }
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_system_assets_manifest(assets_dir: Path) -> Path:
    """Generate manifest.json in the given system-assets directory."""
    destination = assets_dir / MANIFEST_FILENAME
    destination.write_bytes(build_system_assets_manifest(assets_dir))
    return destination
