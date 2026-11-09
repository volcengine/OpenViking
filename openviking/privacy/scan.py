# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Explicit scan and repair workflow for already persisted Privacy values."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from openviking.core.namespace import classify_uri, resolve_request_uri
from openviking.privacy.sanitizer import PrivacySanitizer
from openviking.server.identity import RequestContext
from openviking_cli.exceptions import (
    InvalidArgumentError,
    NotFoundError,
    ResourceExhaustedError,
)

PRIVACY_SCAN_MAX_NODES = 1000
PRIVACY_SCAN_MAX_FILE_BYTES = 1024 * 1024
PRIVACY_SCAN_MAX_TOTAL_BYTES = 16 * 1024 * 1024
PRIVACY_SCAN_MAX_REPLACEMENTS_PER_FILE = 1000
PRIVACY_SCAN_MAX_REFERENCE_DETAILS_PER_FILE = 32
PRIVACY_SCAN_MAX_REPAIRED_FILES = 100
PRIVACY_SCAN_MAX_DURATION_SECONDS = 60.0
PRIVACY_SCAN_FILES_PER_SECOND = 20.0

_SKIP_NAMES = frozenset(
    {
        ".abstract.md",
        ".overview.md",
        ".relations.json",
        ".source.json",
        ".image_mappings.json",
        ".artifact_manifest.json",
    }
)
_SCAN_STATE_GUARD = threading.Lock()
_SCAN_ACTIVE = False


def _acquire_scan_slot() -> None:
    global _SCAN_ACTIVE
    with _SCAN_STATE_GUARD:
        if _SCAN_ACTIVE:
            raise ResourceExhaustedError(
                "Another Privacy scan is already running in this server process",
                details={"max_concurrent_scans": 1},
            )
        _SCAN_ACTIVE = True


def _release_scan_slot() -> None:
    global _SCAN_ACTIVE
    with _SCAN_STATE_GUARD:
        _SCAN_ACTIVE = False


def _entry_size(entry: dict[str, Any]) -> int | None:
    size = entry.get("size")
    return size if isinstance(size, int) and size >= 0 else None


async def scan_privacy_content(
    *,
    service: Any,
    ctx: RequestContext,
    sanitizer: PrivacySanitizer,
    uri: str,
    fix: bool = False,
    node_limit: int = PRIVACY_SCAN_MAX_NODES,
) -> dict[str, Any]:
    """Scan one explicit URI within strict process, IO, and repair budgets."""
    if not uri:
        raise InvalidArgumentError("privacy scan requires one explicit uri")
    if not 1 <= node_limit <= PRIVACY_SCAN_MAX_NODES:
        raise InvalidArgumentError(
            f"privacy scan node_limit must be between 1 and {PRIVACY_SCAN_MAX_NODES}",
            details={"node_limit": node_limit, "max_node_limit": PRIVACY_SCAN_MAX_NODES},
        )

    _acquire_scan_slot()
    try:
        return await _scan_privacy_content(
            service=service,
            ctx=ctx,
            sanitizer=sanitizer,
            uri=uri,
            fix=fix,
            node_limit=node_limit,
        )
    finally:
        _release_scan_slot()


async def _scan_privacy_content(
    *,
    service: Any,
    ctx: RequestContext,
    sanitizer: PrivacySanitizer,
    uri: str,
    fix: bool,
    node_limit: int,
) -> dict[str, Any]:
    root = resolve_request_uri(uri, ctx)
    started_at = time.monotonic()
    files: list[dict[str, Any]] = []
    truncated = False
    try:
        stat = await service.viking_fs.stat(root, ctx=ctx, skip_count=True)
    except (NotFoundError, FileNotFoundError):
        raise NotFoundError(root, "privacy scan target") from None
    if stat.get("isDir", stat.get("is_dir", False)):
        entries = await service.viking_fs.tree(
            root,
            output="original",
            show_all_hidden=True,
            node_limit=node_limit,
            level_limit=None,
            ctx=ctx,
        )
        # VikingFS does not expose a has_more bit on this internal tree call.
        # Treat an exactly full page conservatively as truncated.
        truncated = len(entries) >= node_limit
        files.extend(
            entry
            for entry in entries
            if not entry.get("isDir") and entry.get("uri")
        )
    else:
        files.append({"uri": root, "size": stat.get("size")})

    unique_files: dict[str, dict[str, Any]] = {}
    for entry in files:
        file_uri = str(entry.get("uri") or "")
        if file_uri:
            unique_files.setdefault(file_uri, entry)

    matches: list[dict[str, Any]] = []
    repaired = 0
    reindex_roots: set[str] = set()
    scanned_files = 0
    scanned_bytes = 0
    skipped_files: dict[str, int] = {}
    stop_reason: str | None = None
    fix_truncated = False
    last_read_at = 0.0

    def skip(reason: str) -> None:
        skipped_files[reason] = skipped_files.get(reason, 0) + 1

    for file_uri, entry in unique_files.items():
        elapsed = time.monotonic() - started_at
        if elapsed >= PRIVACY_SCAN_MAX_DURATION_SECONDS:
            truncated = True
            stop_reason = "duration_limit"
            break
        if file_uri.rstrip("/").rsplit("/", 1)[-1] in _SKIP_NAMES:
            skip("internal_metadata")
            continue

        size = _entry_size(entry)
        if size is None:
            try:
                file_stat = await service.viking_fs.stat(file_uri, ctx=ctx, skip_count=True)
                size = _entry_size(file_stat)
            except (NotFoundError, FileNotFoundError):
                skip("not_found")
                continue
        if size is not None and size > PRIVACY_SCAN_MAX_FILE_BYTES:
            skip("file_too_large")
            continue
        if size is not None and scanned_bytes + size > PRIVACY_SCAN_MAX_TOTAL_BYTES:
            truncated = True
            stop_reason = "total_bytes_limit"
            break

        if last_read_at:
            min_interval = 1.0 / PRIVACY_SCAN_FILES_PER_SECOND
            delay = min_interval - (time.monotonic() - last_read_at)
            if delay > 0:
                remaining = PRIVACY_SCAN_MAX_DURATION_SECONDS - (
                    time.monotonic() - started_at
                )
                if remaining <= delay:
                    truncated = True
                    stop_reason = "duration_limit"
                    break
                await asyncio.sleep(delay)
        try:
            raw = await service.viking_fs.read_file_bytes(
                file_uri,
                ctx=ctx,
                max_bytes=PRIVACY_SCAN_MAX_FILE_BYTES,
            )
            last_read_at = time.monotonic()
        except ResourceExhaustedError as exc:
            if exc.details.get("reason") != "read_limit_exceeded":
                raise
            skip("file_too_large")
            continue
        except (NotFoundError, FileNotFoundError):
            skip("not_found")
            continue

        if scanned_bytes + len(raw) > PRIVACY_SCAN_MAX_TOTAL_BYTES:
            truncated = True
            stop_reason = "total_bytes_limit"
            break
        scanned_files += 1
        scanned_bytes += len(raw)
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            skip("non_utf8")
            continue
        result = await sanitizer.sanitize(
            content,
            ctx=ctx,
            uri=file_uri,
            max_replacements=PRIVACY_SCAN_MAX_REPLACEMENTS_PER_FILE,
            fail_on_match_limit=False,
        )
        if result.match_limit_exceeded:
            skip("match_limit_exceeded")
            matches.append(
                {
                    "uri": file_uri,
                    "occurrences": PRIVACY_SCAN_MAX_REPLACEMENTS_PER_FILE + 1,
                    "occurrences_truncated": True,
                    "references": [],
                    "ambiguous": [],
                    "fixable": False,
                    "fixed": False,
                    "reason": "match_limit_exceeded",
                }
            )
            continue
        if not result.replacements and not result.ambiguous_references:
            continue
        all_references = sorted(
            {replacement.reference for replacement in result.replacements}
        )
        all_ambiguous = sorted(set(result.ambiguous_references))
        references_truncated = len(all_references) > PRIVACY_SCAN_MAX_REFERENCE_DETAILS_PER_FILE
        ambiguous_truncated = len(all_ambiguous) > PRIVACY_SCAN_MAX_REFERENCE_DETAILS_PER_FILE
        item = {
            "uri": file_uri,
            "occurrences": len(result.replacements),
            "references": all_references[:PRIVACY_SCAN_MAX_REFERENCE_DETAILS_PER_FILE],
            "references_truncated": references_truncated,
            "ambiguous": [
                list(candidates[:PRIVACY_SCAN_MAX_REFERENCE_DETAILS_PER_FILE])
                for candidates in all_ambiguous[:PRIVACY_SCAN_MAX_REFERENCE_DETAILS_PER_FILE]
            ],
            "ambiguous_truncated": ambiguous_truncated
            or any(
                len(candidates) > PRIVACY_SCAN_MAX_REFERENCE_DETAILS_PER_FILE
                for candidates in all_ambiguous
            ),
            "fixable": not result.ambiguous_references,
            "fixed": False,
        }
        if fix and item["fixable"]:
            if repaired >= PRIVACY_SCAN_MAX_REPAIRED_FILES:
                item["reason"] = "repair_limit_exceeded"
                fix_truncated = True
            else:
                classification = classify_uri(file_uri)
                if classification.is_skill:
                    path = service.viking_fs._uri_to_path(file_uri, ctx=ctx)
                    lease = await service.viking_fs._async_agfs.pathlock_acquire_exact(path)
                    try:
                        await service.viking_fs.write_file(
                            file_uri, result.content, ctx=ctx, lease_ref=lease
                        )
                    finally:
                        await service.viking_fs._async_agfs.pathlock_release(lease)
                    parts = file_uri.split("/skills/", 1)
                    skill_name = parts[1].split("/", 1)[0] if len(parts) == 2 else ""
                    if skill_name:
                        reindex_roots.add(f"{parts[0]}/skills/{skill_name}")
                else:
                    await service.fs.write(file_uri, result.content, ctx=ctx, wait=False)
                item["fixed"] = True
                repaired += 1
        matches.append(item)

    reindex_tasks: list[dict[str, Any]] = []
    if fix:
        for root in sorted(reindex_roots):
            reindex_tasks.append(
                await service.reindex(
                    uri=root,
                    mode="semantic_and_vectors",
                    wait=False,
                    force=True,
                    ctx=ctx,
                )
            )
    return {
        "uri": root,
        "enumerated_files": len(unique_files),
        "scanned_files": scanned_files,
        "scanned_bytes": scanned_bytes,
        "matched_files": len(matches),
        "repaired_files": repaired,
        "dry_run": not fix,
        "truncated": truncated,
        "fix_truncated": fix_truncated,
        "stop_reason": stop_reason,
        "skipped_files": skipped_files,
        "limits": {
            "max_nodes": PRIVACY_SCAN_MAX_NODES,
            "max_file_bytes": PRIVACY_SCAN_MAX_FILE_BYTES,
            "max_total_bytes": PRIVACY_SCAN_MAX_TOTAL_BYTES,
            "max_replacements_per_file": PRIVACY_SCAN_MAX_REPLACEMENTS_PER_FILE,
            "max_reference_details_per_file": PRIVACY_SCAN_MAX_REFERENCE_DETAILS_PER_FILE,
            "max_repaired_files": PRIVACY_SCAN_MAX_REPAIRED_FILES,
            "max_duration_seconds": PRIVACY_SCAN_MAX_DURATION_SECONDS,
            "files_per_second": PRIVACY_SCAN_FILES_PER_SECOND,
            "max_concurrent_scans_per_process": 1,
        },
        "matches": matches,
        "reindex": reindex_tasks,
    }
