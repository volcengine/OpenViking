# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""OVPack fixtures for server tests."""

import hashlib
import io
import json
import zipfile


def _content_sha256(entries: list[dict[str, object]]) -> str:
    payload = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _index_records_bytes(records: list[dict[str, object]] | None = None) -> bytes:
    records = records or []
    if not records:
        return b""
    lines = [
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for record in records
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_ovpack_bytes(
    root_name: str = "pkg",
    files: dict[str, bytes] | None = None,
) -> bytes:
    files = files or {"content.md": b"# Demo\n"}
    manifest_entries: list[dict[str, object]] = [{"path": "", "kind": "directory"}]
    content_entries: list[dict[str, object]] = []

    for rel_path, content in sorted(files.items()):
        file_sha256 = hashlib.sha256(content).hexdigest()
        file_entry = {
            "path": rel_path,
            "kind": "file",
            "size": len(content),
            "sha256": file_sha256,
        }
        manifest_entries.append(file_entry)
        content_entries.append(
            {
                "path": rel_path,
                "size": file_entry["size"],
                "sha256": file_entry["sha256"],
            }
        )

    index_records = _index_records_bytes()
    manifest = {
        "kind": "openviking.ovpack",
        "format_version": 2,
        "root": {
            "name": root_name,
            "uri": f"viking://resources/{root_name}",
            "scope": "resources",
        },
        "entries": manifest_entries,
        "content_sha256": _content_sha256(content_entries),
        "index": {
            "records": {
                "path": "_ovpack/index_records.jsonl",
                "count": 0,
                "sha256": hashlib.sha256(index_records).hexdigest(),
            }
        },
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(f"{root_name}/", "")
        zf.writestr(f"{root_name}/files/", "")
        zf.writestr(f"{root_name}/_ovpack/", "")
        zf.writestr(f"{root_name}/_ovpack/index_records.jsonl", index_records)
        zf.writestr(f"{root_name}/_ovpack/manifest.json", json.dumps(manifest))
        for rel_path, content in files.items():
            zf.writestr(f"{root_name}/files/{rel_path}", content)
    return buffer.getvalue()
