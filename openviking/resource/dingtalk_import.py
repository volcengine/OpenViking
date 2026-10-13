# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""DingTalk import completeness checks and conservative sync metadata."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Iterable

from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.utils.uri import VikingURI

DINGTALK_SYNC_SIDECAR = ".dingtalk_sync.json"


def record_local_parse_skips(meta: dict[str, Any]) -> list[str]:
    """Record DingTalk files skipped by the local directory parser."""
    if not isinstance(meta.get("dingtalk_manifest"), list):
        return []

    failed = meta["failed_files"] if isinstance(meta.get("failed_files"), list) else []
    unsupported = (
        meta["unsupported_files"] if isinstance(meta.get("unsupported_files"), list) else []
    )
    total = len(failed) + len(unsupported)
    if not total:
        return []

    previous = (
        meta["dingtalk_parse_skipped"]
        if isinstance(meta.get("dingtalk_parse_skipped"), dict)
        else {}
    )
    previous_total = int(previous.get("total", 0) or 0)
    report = dict(meta.get("dingtalk_report") or {})
    report["skipped"] = max(0, int(report.get("skipped", 0) or 0) - previous_total) + total
    report["local_parse_skipped"] = total
    meta["dingtalk_report"] = report
    meta["dingtalk_parse_skipped"] = {
        "failed": len(failed),
        "unsupported": len(unsupported),
        "total": total,
    }

    skipped = [
        item
        for item in (meta.get("skipped_files") or [])
        if not isinstance(item, dict) or item.get("source") != "dingtalk_local_parse"
    ]
    for kind, items in (("failed", failed), ("unsupported", unsupported)):
        for item in items:
            entry = dict(item) if isinstance(item, dict) else {"path": str(item)}
            detail = entry.get("error") or entry.get("reason") or kind
            entry.update(
                {
                    "status": "skip",
                    "reason": f"local {kind}: {detail}",
                    "source": "dingtalk_local_parse",
                }
            )
            skipped.append(entry)
    meta["skipped_files"] = skipped

    return [
        f"DingTalk import skipped {total} local file(s): "
        f"{len(failed)} failed during parsing, {len(unsupported)} unsupported."
    ]


def _is_local_parse_warning(
    warning: str,
    failed: list[Any],
    unsupported: list[Any],
) -> bool:
    if warning.startswith("DingTalk import skipped "):
        return True
    if (
        unsupported
        and warning.startswith("Directory contains ")
        and " unsupported file(s)" in warning
    ):
        return True
    failed_paths = {
        str(item.get("path")) for item in failed if isinstance(item, dict) and item.get("path")
    }
    return any(
        warning.startswith(f"Failed to parse {path}:")
        or warning.startswith(f"Failed to upload {path}:")
        for path in failed_paths
    )


def incomplete_import_details(
    meta: dict[str, Any], warnings: Iterable[str]
) -> dict[str, Any] | None:
    """Return bounded diagnostics when a DingTalk directory parse is incomplete."""
    if not isinstance(meta.get("dingtalk_manifest"), list):
        return None

    report = meta["dingtalk_report"] if isinstance(meta.get("dingtalk_report"), dict) else {}
    failed = meta["failed_files"] if isinstance(meta.get("failed_files"), list) else []
    unsupported = (
        meta["unsupported_files"] if isinstance(meta.get("unsupported_files"), list) else []
    )
    skipped = meta["skipped_files"] if isinstance(meta.get("skipped_files"), list) else []
    warning_list = [str(item) for item in warnings if str(item)]
    local_stats = (
        meta["dingtalk_parse_skipped"]
        if isinstance(meta.get("dingtalk_parse_skipped"), dict)
        else {}
    )
    local_parse_recorded = int(local_stats.get("failed", -1) or 0) == len(failed) and int(
        local_stats.get("unsupported", -1) or 0
    ) == len(unsupported)
    blocking_skipped = [
        item
        for item in skipped
        if not (
            local_parse_recorded
            and isinstance(item, dict)
            and item.get("source") == "dingtalk_local_parse"
        )
    ]
    blocking_warnings = [
        warning
        for warning in warning_list
        if not (local_parse_recorded and _is_local_parse_warning(warning, failed, unsupported))
    ]
    if not (
        (failed and not local_parse_recorded)
        or (unsupported and not local_parse_recorded)
        or blocking_skipped
        or blocking_warnings
        or int(report.get("failed", 0) or 0)
        or int(report.get("unsupported", 0) or 0)
    ):
        return None

    return {
        "dingtalk_report": dict(report),
        "dingtalk_limits": meta.get("dingtalk_limits", {}),
        "failed_files": failed[:20],
        "unsupported_files": unsupported[:20],
        "skipped_files": skipped[:20],
        "warnings": warning_list[:20],
    }


def _entry_paths(entry: dict[str, Any]) -> set[str]:
    relative = entry.get("relative_path")
    if not isinstance(relative, str) or not relative or relative == ".":
        return set()
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        return set()
    paths = {path.as_posix()}
    if path.suffix:
        paths.add(path.with_suffix("").as_posix())
    return paths


def _retained_entry_paths(entry: dict[str, Any]) -> set[str]:
    paths = _entry_paths(entry)
    source_paths = entry.get("source_paths")
    if isinstance(source_paths, list):
        exact_paths = set()
        for source_path in source_paths:
            if not isinstance(source_path, str) or not source_path or source_path == ".":
                continue
            parsed = PurePosixPath(source_path)
            if parsed.is_absolute() or ".." in parsed.parts:
                continue
            exact_paths.add(parsed.as_posix())
        if exact_paths:
            paths.update(exact_paths)
            return paths
    relative = entry.get("relative_path")
    if not isinstance(relative, str) or not relative or relative == ".":
        return paths
    parent = PurePosixPath(relative).parent
    for dependency_dir in ("assets", "attachments"):
        dependency = (
            parent / dependency_dir if str(parent) != "." else PurePosixPath(dependency_dir)
        )
        paths.add(dependency.as_posix())
    return paths


async def prepare_dingtalk_artifact(
    viking_fs: Any,
    *,
    store: Any,
    artifact_ref: Any,
    doc_rel: str,
    target_uri: str,
    meta: dict[str, Any],
    ctx: Any,
) -> tuple[str | None, list[str]]:
    """Include retained outputs in the artifact before the shared update planner runs."""
    manifest = meta.get("dingtalk_manifest")
    if not isinstance(manifest, list):
        return None, []

    old: dict[str, Any] = {}
    old_sidecar_uri = VikingURI(target_uri).join(DINGTALK_SYNC_SIDECAR).uri
    if await viking_fs.exists(old_sidecar_uri, ctx=ctx):
        try:
            old = json.loads(await viking_fs.read_file(old_sidecar_uri, ctx=ctx))
        except json.JSONDecodeError as exc:
            raise InvalidArgumentError(
                "Stored DingTalk sync metadata is invalid; refusing to replace the target."
            ) from exc
        if not isinstance(old, dict) or not isinstance(old.get("manifest"), list):
            raise InvalidArgumentError(
                "Stored DingTalk sync metadata is invalid; refusing to replace the target."
            )

    previous_digest = meta.get("dingtalk_previous_digest")
    if previous_digest:
        from openviking.resource.dingtalk_incremental import digest

        if digest(old) != previous_digest:
            raise InvalidArgumentError(
                "DingTalk target changed during preparation; retry the import."
            )

    old_manifest = old["manifest"] if isinstance(old.get("manifest"), list) else []
    current_by_id = {
        item.get("node_id"): item
        for item in manifest
        if isinstance(item, dict) and isinstance(item.get("node_id"), str)
    }
    retained = {
        path
        for path in old.get("retained_paths", [])
        if isinstance(path, str)
        and path
        and not PurePosixPath(path).is_absolute()
        and ".." not in PurePosixPath(path).parts
    }
    missing = {
        node_id
        for node_id in old.get("missing_nodes", [])
        if isinstance(node_id, str) and node_id and node_id not in current_by_id
    }
    moved: list[str] = []
    for item in old_manifest:
        if not isinstance(item, dict) or not isinstance(item.get("node_id"), str):
            continue
        current = current_by_id.get(item["node_id"])
        if current is None:
            retained.update(_retained_entry_paths(item))
            missing.add(item["node_id"])
        elif current.get("relative_path") != item.get("relative_path"):
            retained.update(_retained_entry_paths(item))
            moved.append(item["node_id"])

    current_paths = set().union(
        *(
            _entry_paths(item)
            for item in manifest
            if isinstance(item, dict) and item.get("reused") is not True
        ),
        set(),
    )
    retained = {
        path
        for path in retained
        if not any(path == current or path.startswith(current + "/") for current in current_paths)
    }
    for item in manifest:
        if not isinstance(item, dict) or not (item.get("reused") or item.get("unreadable")):
            continue
        old_item = next(
            (
                old
                for old in old_manifest
                if isinstance(old, dict) and old.get("node_id") == item.get("node_id")
            ),
            item,
        )
        retained.update(_retained_entry_paths(old_item))
    payload = {
        "manifest": manifest,
        "retained_paths": sorted(retained),
        "missing_nodes": sorted(missing),
        "report": meta.get("dingtalk_report", {}),
        "limits": meta.get("dingtalk_limits", {}),
        "limitations": meta.get("dingtalk_limitations", []),
        "run_id": meta.get("dingtalk_run_id"),
        "processing_key": meta.get("dingtalk_processing_key"),
        "complete": False,
    }
    # A failed parse can still have readable siblings. Protect the previous
    # successful output for that source node before compiling any deletions.
    failed_paths = {
        str(item.get("path", ""))
        for item in [*meta.get("failed_files", []), *meta.get("unsupported_files", [])]
        if isinstance(item, dict)
    }
    for entry in old_manifest:
        if isinstance(entry, dict) and failed_paths.intersection(_retained_entry_paths(entry)):
            retained.update(_retained_entry_paths(entry))
    payload["retained_paths"] = sorted(retained)
    if retained:
        from openviking.storage.internal_names import is_storage_internal_name
        from openviking.storage.resource_rnfv import CONTROL_BASENAMES

        existing: set[str] = set()
        pending = [doc_rel]
        while pending:
            for item in await store.list(artifact_ref, pending.pop()):
                if item.is_dir:
                    pending.append(item.rel_path)
                else:
                    existing.add(item.rel_path)
        entries = await viking_fs.tree(
            target_uri,
            show_all_hidden=True,
            node_limit=None,
            level_limit=None,
            output="original",
            ctx=ctx,
        )
        for entry in entries:
            path = entry.get("rel_path", "")
            if not path or entry.get("isDir"):
                continue
            parts = PurePosixPath(path).parts
            if (
                any(is_storage_internal_name(part) for part in parts)
                or parts[-1] in CONTROL_BASENAMES
            ):
                continue
            if not any(
                path == keep or path.startswith(keep.rstrip("/") + "/") for keep in retained
            ):
                continue
            destination = f"{doc_rel}/{path}" if doc_rel else path
            if destination in existing:
                continue
            data = await viking_fs.read_file_bytes(entry["uri"], ctx=ctx)
            await store.write_bytes(artifact_ref, destination, data)

    warnings: list[str] = []
    if missing:
        warnings.append(
            f"DingTalk sync retained {len(missing)} source node(s) missing from this refresh; "
            "they may have been deleted, moved, or become inaccessible."
        )
    if moved:
        warnings.append(
            f"DingTalk sync retained {len(moved)} previous source path(s) after node moves."
        )
    unreadable = meta.get("dingtalk_report", {}).get("unreadable_nodes", [])
    if unreadable:
        warnings.append(
            f"DingTalk sync skipped {len(unreadable)} unreadable source file(s); "
            "they will be retried on the next sync."
        )
    return json.dumps(payload, ensure_ascii=False, sort_keys=True), warnings


async def persist_sync_sidecar(
    viking_fs: Any,
    *,
    target_uri: str,
    content: str | None,
    ctx: Any,
    lease_ref: dict[str, Any] | None,
) -> None:
    if content is None:
        return
    await viking_fs.write_file(
        VikingURI(target_uri).join(DINGTALK_SYNC_SIDECAR).uri,
        content,
        ctx=ctx,
        lease_ref=lease_ref,
    )
