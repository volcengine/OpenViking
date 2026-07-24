# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Sync manifest for single-doc resources (issue #3029).

A `.viking_sync_manifest.json` dotfile at a resource root records exactly which
files/dirs OpenViking generated for that resource (Feishu/web/etc.), so a resync
can safely tell *our* stale files apart from *user-added* or *user-edited* ones
and delete only the former. Schema: see `.wiki/issue-3029-...md` §3.7.

This module is the manifest core only (Prompt A): read / write / divergence.
It does not touch `sync_dir` / `semantic_processor` — that is Prompt B.

Orthogonality note: this manifest's delete axis ("a file is in the TARGET but
ABSENT from source — delete it or keep it?") is *different* from the ovpack
on-conflict axis `OVPACK_ON_CONFLICT_VALUES` = {fail, overwrite, skip}
(`openviking/storage/ovpack/format.py`), which answers "a file exists in BOTH —
which wins?". Keep the two vocabularies distinct; do not collapse them.

Atomic-write decision: the manifest lives at a `viking://` URI, so raw
`os.replace` on an arbitrary path is not available. This codebase writes small
JSON control files with a plain `viking_fs.write_file(..., lock_handle=...)`
(e.g. the mapping control file in semantic_processor). We mirror that: a single
`write_file` under the caller's resource `lock_handle` IS the atomic equivalent
here because (a) the resource lock already serializes read+write, (b) the
underlying encrypted-write path stages to a temp file and swaps, and (c) we
write the manifest LAST, so a crash mid-write leaves the OLD manifest intact
(the fail-safe the ticket needs) rather than a torn set.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from openviking.storage.internal_names import SYNC_MANIFEST_FILE
from openviking.storage.ovpack.format import sha256_hex

# Single source of truth for the filename lives in internal_names (so the hidden-
# listing / prune lists and this module can never drift). Kept aliased here for
# the module's existing callers/tests.
SYNC_MANIFEST_FILENAME = SYNC_MANIFEST_FILE
SUPPORTED_SCHEMA_VERSION = 1


def _to_posix(relpath: str) -> str:
    """Normalize any OS separators to POSIX slashes for storage/matching."""
    return relpath.replace("\\", "/")


def _key(relpath: str) -> str:
    """Case-fold key for relpath matching.

    On Windows `os.path.normcase` lowercases (and swaps separators), giving the
    case-insensitive match the ticket requires; on POSIX it is identity, so
    matching stays case-sensitive. Referenced at call time so tests can
    monkeypatch `os.path.normcase` to simulate Windows.
    """
    return os.path.normcase(_to_posix(relpath))


@dataclass
class ManifestFile:
    relpath: str  # POSIX slashes
    sha256: str  # 64-hex
    size: int


@dataclass
class Manifest:
    source: dict[str, Any]
    synced_at: str  # UTC ISO-8601, e.g. "2026-07-06T10:20:57Z"
    files: list[ManifestFile] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    schema_version: int = SUPPORTED_SCHEMA_VERSION

    def get(self, relpath: str) -> Optional[ManifestFile]:
        """Look up a tracked file by case-folded relpath (Windows-safe)."""
        # ponytail: linear scan; manifests are tiny (one doc). Build a dict
        # index only if a resource ever tracks thousands of files.
        target = _key(relpath)
        for f in self.files:
            if _key(f.relpath) == target:
                return f
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "synced_at": self.synced_at,
            "files": [
                {"relpath": _to_posix(f.relpath), "sha256": f.sha256, "size": f.size}
                for f in self.files
            ],
            "dirs": [_to_posix(d) for d in self.dirs],
        }


def manifest_entry(relpath: str, data: bytes) -> ManifestFile:
    """Build a manifest entry for `data`, hashing via the shared `sha256_hex`."""
    return ManifestFile(relpath=_to_posix(relpath), sha256=sha256_hex(data), size=len(data))


def divergent(current_hash: str, manifest: Optional[Manifest], relpath: str) -> bool:
    """True iff the manifest tracks `relpath` AND its hash differs from current.

    A True means the user edited a file we generated, so a resync must preserve
    it, never overwrite/delete it (§3.3/§3.8). Absent manifest or untracked
    relpath => False (not ours to reason about).
    """
    if manifest is None:
        return False
    entry = manifest.get(relpath)
    return entry is not None and entry.sha256 != current_hash


def _manifest_uri(target_root: str) -> str:
    return f"{target_root.rstrip('/')}/{SYNC_MANIFEST_FILENAME}"


async def read_manifest(target_root, viking_fs, ctx=None, lock_handle=None) -> Optional[Manifest]:
    """Read the manifest at `target_root`, or None (fail safe).

    Returns None on: absent file, unreadable/corrupt JSON, or a
    `schema_version` newer than we support (unknown format => treat as no
    manifest => caller does merge-only). `lock_handle` is accepted for API
    symmetry; the caller already holds the resource lock and reads need no lock.
    """
    uri = _manifest_uri(target_root)
    try:
        raw = await viking_fs.read_file(uri, ctx=ctx)
    except Exception:
        return None  # absent / unreadable => fail safe
    try:
        data = json.loads(raw)
    except Exception:
        return None  # corrupt JSON => fail safe
    if not isinstance(data, dict):
        return None
    version = data.get("schema_version")
    if not isinstance(version, int) or version > SUPPORTED_SCHEMA_VERSION:
        return None  # newer/unknown schema => fail safe
    try:
        files = [
            ManifestFile(relpath=_to_posix(f["relpath"]), sha256=f["sha256"], size=int(f["size"]))
            for f in data.get("files", [])
        ]
        return Manifest(
            source=data.get("source", {}),
            synced_at=data.get("synced_at", ""),
            files=files,
            dirs=[_to_posix(d) for d in data.get("dirs", [])],
            schema_version=version,
        )
    except Exception:
        return None  # malformed entries => fail safe


async def write_manifest_atomic(
    target_root, manifest, viking_fs, ctx=None, lock_handle=None
) -> None:
    """Write the manifest atomically under the caller's resource lock.

    See the module docstring for why a single `write_file` under `lock_handle`
    is the atomic equivalent here (no torn set; a crash leaves the old file).
    Must be called LAST, only after the tree sync fully succeeds.
    """
    uri = _manifest_uri(target_root)
    payload = json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    await viking_fs.write_file(uri, payload, ctx=ctx, lock_handle=lock_handle)
