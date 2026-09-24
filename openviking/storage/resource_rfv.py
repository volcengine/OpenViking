# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Typed R/F/V inputs for planning maintenance reindex operations."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from openviking.concurrency import bounded_map
from openviking.core.namespace import context_type_for_uri
from openviking.server.error_mapping import is_not_found_error
from openviking.storage.abstract_overview import body_for_preview
from openviking.storage.internal_names import is_storage_internal_name
from openviking.storage.resource_diff import (
    ContentState,
    IndexState,
    ResourceDiffEntry,
    ResourceDiffResult,
)
from openviking.storage.resource_rnfv import (
    RequestIntent,
    VectorIndexSnapshot,
    VectorRecordSnapshot,
    canonical_vector_records_by_level,
)
from openviking.utils.content_hash import content_md5
from openviking_cli.exceptions import NotFoundError


@dataclass(frozen=True)
class RFVEntry:
    """One current formal-tree node and its per-level source fingerprints."""

    uri: str
    relative_path: str
    is_dir: bool
    level_md5s: Mapping[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = {0, 1} if self.is_dir else {2}
        invalid = set(self.level_md5s) - expected
        if invalid:
            raise ValueError(f"invalid RFV levels for {self.relative_path}: {sorted(invalid)}")


@dataclass(frozen=True)
class RFVFormalSnapshot:
    entries: Mapping[str, RFVEntry]
    complete: bool = True


@dataclass(frozen=True)
class RFVSnapshot:
    request: RequestIntent
    formal: RFVFormalSnapshot
    vectors: VectorIndexSnapshot
    source_contents: Mapping[tuple[str, int], str | bytes] = field(default_factory=dict)
    source_raw_contents: Mapping[tuple[str, int], str | bytes] = field(default_factory=dict)
    source_metadata: Mapping[str, Any] | None = None


def _relative_uri(root: str, uri: str) -> str | None:
    root = root.rstrip("/")
    if uri == root:
        return ""
    prefix = root + "/"
    return uri[len(prefix) :] if uri.startswith(prefix) else None


def _is_sidecar(relative_path: str) -> bool:
    return relative_path.rsplit("/", 1)[-1] in {".abstract.md", ".overview.md"}


def _is_control_file(relative_path: str) -> bool:
    name = relative_path.rsplit("/", 1)[-1]
    return name in {".source.json", ".image_mappings.json", ".artifact_manifest.json"}


async def build_rfv_snapshot(
    *,
    viking_fs: Any,
    vikingdb: Any,
    target_uri: str,
    ctx: Any,
    request_intent: RequestIntent,
    recursive: bool = True,
    source_read_concurrency: int = 8,
    root_is_dir: bool | None = None,
    formal_inventory: tuple[bool, list[Mapping[str, Any]], bool] | None = None,
) -> RFVSnapshot:
    """Read one complete F/V inventory and each selected embedding source once."""
    root = target_uri.rstrip("/")
    projection = request_intent.required_vector_fields()
    if request_intent.processing_mode != "vectors_only":
        projection = projection | {"abstract"}
    if target_uri.rstrip("/").startswith(("viking://user/", "viking://agent/skills/")):
        projection = projection | {"name", "description", "tags"}

    async def read_formal_inventory() -> tuple[bool, list[Mapping[str, Any]], bool]:
        if formal_inventory is not None:
            return formal_inventory
        is_dir = root_is_dir
        if is_dir is None:
            stat = await viking_fs.stat(root, ctx=ctx, skip_count=True)
            is_dir = bool(stat.get("isDir", stat.get("is_dir")))
        if not is_dir or not recursive:
            return is_dir, [], True
        raw_entries = await viking_fs.tree(
            root,
            output="original",
            show_all_hidden=True,
            node_limit=None,
            level_limit=None,
            ctx=ctx,
        )
        complete = not any(entry.get("access") == "denied" for entry in raw_entries)
        return is_dir, list(raw_entries), complete

    formal_task = asyncio.create_task(read_formal_inventory())
    vector_task = asyncio.create_task(
        vikingdb.get_incremental_inventory_under_uri(
            root, ctx=ctx, output_fields=sorted(projection), recursive=recursive
        )
    )
    try:
        (root_is_dir, raw_entries, complete), inventory = await asyncio.gather(
            formal_task, vector_task
        )
    except BaseException:
        for task in (formal_task, vector_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(formal_task, vector_task, return_exceptions=True)
        raise
    complete = complete and not any(entry.get("access") == "denied" for entry in raw_entries)
    if not complete:
        raise ValueError("cannot build RFV snapshot from an incomplete formal tree")

    nodes: dict[str, tuple[str, bool]] = {"": (root, root_is_dir)}
    sidecars: dict[tuple[str, int], str] = {}
    source_metadata_uri: str | None = None
    for raw in raw_entries:
        if raw.get("access") == "denied":
            continue
        uri = str(raw.get("uri") or "")
        relative = _relative_uri(root, uri)
        if relative is None:
            complete = False
            continue
        relative_path = relative.strip("/")
        if not uri or not relative_path:
            continue
        name = relative_path.rsplit("/", 1)[-1]
        if _is_sidecar(relative_path):
            parent = relative_path.rsplit("/", 1)[0] if "/" in relative_path else ""
            level = 0 if name == ".abstract.md" else 1
            sidecars[(parent, level)] = uri
            continue
        if relative_path == ".source.json":
            source_metadata_uri = uri
            continue
        if is_storage_internal_name(name) or _is_control_file(relative_path):
            continue
        nodes[relative_path] = (uri, bool(raw.get("isDir")))

    if root_is_dir and not recursive:
        for level, name in ((0, ".abstract.md"), (1, ".overview.md")):
            sidecars[("", level)] = f"{root}/{name}"

    records_by_uri_level: dict[tuple[str, int], Mapping[str, Any]] = {}
    for record in inventory.values():
        uri = str(record.get("uri") or "")
        try:
            level = int(record.get("level", -1))
        except (TypeError, ValueError):
            continue
        records_by_uri_level.setdefault((uri, level), record)

    source_contents: dict[tuple[str, int], str | bytes] = {}
    source_raw_contents: dict[tuple[str, int], str | bytes] = {}
    level_md5s: dict[str, dict[int, str]] = {path: {} for path in nodes}
    source_jobs: list[tuple[str, str, int, bool]] = []
    for path, (uri, is_dir) in nodes.items():
        if is_dir:
            for level in (0, 1):
                source_uri = sidecars.get((path, level))
                if source_uri:
                    source_jobs.append((path, source_uri, level, True))
        else:
            source_jobs.append((path, uri, 2, False))

    async def read_source(job: tuple[str, str, int, bool]) -> None:
        path, source_uri, level, is_sidecar = job
        try:
            raw = await viking_fs.read_file_bytes(source_uri, ctx=ctx)
        except Exception as exc:
            if is_sidecar and (
                isinstance(exc, (FileNotFoundError, KeyError, NotFoundError))
                or is_not_found_error(exc)
            ):
                return
            raise
        node_uri = nodes[path][0]
        if is_sidecar:
            source_raw_contents[(node_uri, level)] = raw
            body = body_for_preview(raw)
            if not body.strip():
                return
            source_contents[(node_uri, level)] = body
            source_md5 = content_md5(body.encode("utf-8"))
            level_md5s[path][level] = source_md5
            source = body
        else:
            source_md5 = content_md5(raw)
            level_md5s[path][level] = source_md5
            source = raw
        record = records_by_uri_level.get((node_uri, level))
        keep_file_source = (
            not is_sidecar
            and path.rsplit("/", 1)[-1] == "SKILL.md"
            and context_type_for_uri(root) == "skill"
        )
        if keep_file_source or (
            not is_sidecar
            and (
                request_intent.force or record is None or str(record.get("md5") or "") != source_md5
            )
        ):
            source_contents[(node_uri, level)] = source

    await bounded_map(
        source_jobs,
        read_source,
        concurrency=max(1, source_read_concurrency),
    )
    source_metadata = None
    if source_metadata_uri is not None:
        try:
            raw_metadata = await viking_fs.read_file_bytes(source_metadata_uri, ctx=ctx)
            parsed_metadata = json.loads(raw_metadata)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed_metadata = None
        if isinstance(parsed_metadata, dict):
            kind = parsed_metadata.get("kind") or parsed_metadata.get("type")
            source_uri = parsed_metadata.get("uri") or parsed_metadata.get("source")
            if kind and source_uri:
                source_metadata = {
                    "kind": str(kind),
                    "uri": str(source_uri),
                    "path": str(source_uri),
                }
    vector_records: dict[str, VectorRecordSnapshot] = {}
    for record_id, record in inventory.items():
        uri = str(record.get("uri") or "")
        relative_path = _relative_uri(root, uri)
        if relative_path is None:
            raise ValueError(f"RFV vector record is outside target: {uri}")
        if not recursive and relative_path:
            continue
        level = int(record.get("level", -1))
        fields = {
            name: record[name] for name in projection - {"id", "uri", "level"} if name in record
        }
        vector_records[str(record_id)] = VectorRecordSnapshot(
            str(record_id), uri, relative_path, level, fields
        )

    formal_entries = {
        path: RFVEntry(uri, path, is_dir, level_md5s[path]) for path, (uri, is_dir) in nodes.items()
    }
    return RFVSnapshot(
        request=request_intent,
        formal=RFVFormalSnapshot(formal_entries, complete=complete),
        vectors=VectorIndexSnapshot(vector_records, projection, complete=True),
        source_contents=source_contents,
        source_raw_contents=source_raw_contents,
        source_metadata=source_metadata,
    )


def _summarize_index_state(
    *,
    expected_levels: set[int],
    level_states: Mapping[int, IndexState],
    invalid_levels: set[int],
) -> IndexState:
    if invalid_levels:
        return IndexState.LEVEL_CONFLICT
    values = set(level_states.values())
    if not expected_levels:
        return IndexState.ABSENT
    if not level_states:
        return IndexState.MISSING
    if values == {IndexState.ABSENT}:
        return IndexState.ABSENT
    if IndexState.ORPHAN in values:
        return IndexState.ORPHAN
    if IndexState.STALE in values:
        return IndexState.STALE
    if IndexState.MISSING in values:
        return IndexState.PARTIAL if len(level_states) > 1 else IndexState.MISSING
    return IndexState.COMPLETE


def resolve_rfv_state(snapshot: RFVSnapshot) -> ResourceDiffResult:
    """Resolve current formal nodes and vectors without inventing an N snapshot."""

    if not snapshot.formal.complete or not snapshot.vectors.complete:
        raise ValueError("cannot resolve an incomplete RFV snapshot")
    required = snapshot.request.required_vector_fields()
    if not snapshot.vectors.projected_fields.issuperset(required):
        raise ValueError("RFV vector projection misses required fields")
    root = snapshot.request.target_uri.rstrip("/")
    for path, formal in snapshot.formal.entries.items():
        expected_uri = root if not path else f"{root}/{path}"
        if formal.relative_path != path or formal.uri.rstrip("/") != expected_uri:
            raise ValueError(f"RFV formal entry is outside target: {formal.uri}")
    for record in snapshot.vectors.records_by_id.values():
        expected_uri = root if not record.relative_path else f"{root}/{record.relative_path}"
        if record.uri.rstrip("/") != expected_uri:
            raise ValueError(f"RFV vector record is outside target: {record.uri}")

    records_by_path: dict[str, list[VectorRecordSnapshot]] = {}
    for record in snapshot.vectors.records_by_id.values():
        records_by_path.setdefault(record.relative_path, []).append(record)

    entries: dict[str, ResourceDiffEntry] = {}
    all_paths = set(snapshot.formal.entries) | set(records_by_path)
    for path in sorted(all_paths):
        formal = snapshot.formal.entries.get(path)
        records = records_by_path.get(path, [])
        canonical, _duplicates = canonical_vector_records_by_level(records)
        if formal is None:
            entries[path] = ResourceDiffEntry(
                relative_path=path,
                content_state=ContentState.ABSENT,
                index_state=IndexState.ORPHAN,
                old_kind=None,
                new_kind=None,
            )
            continue

        kind = "directory" if formal.is_dir else "file"
        expected_levels = {0, 1} if formal.is_dir else {2}
        invalid_levels = set(canonical) - expected_levels
        level_states: dict[int, IndexState] = {}
        for level in sorted(expected_levels):
            source_md5 = str(formal.level_md5s.get(level) or "")
            record = canonical.get(level)
            if formal.is_dir and not source_md5:
                level_states[level] = (
                    IndexState.MISSING
                    if snapshot.request.processing_mode != "vectors_only"
                    else IndexState.ABSENT
                )
                continue
            if record is None:
                level_states[level] = IndexState.MISSING
                continue
            stored_md5 = str(record.fields.get("md5") or "")
            level_states[level] = (
                IndexState.STALE
                if snapshot.request.force or not source_md5 or stored_md5 != source_md5
                else IndexState.COMPLETE
            )

        index_state = _summarize_index_state(
            expected_levels=expected_levels,
            level_states=level_states,
            invalid_levels=invalid_levels,
        )
        entries[path] = ResourceDiffEntry(
            relative_path=path,
            content_state=ContentState.UNCHANGED,
            index_state=index_state,
            old_kind=kind,
            new_kind=kind,
            md5=(formal.level_md5s.get(2) if not formal.is_dir else None),
            level_states=level_states,
            level_md5s=dict(formal.level_md5s),
        )
    return ResourceDiffResult(entries=entries)


__all__ = [
    "RFVEntry",
    "RFVFormalSnapshot",
    "RFVSnapshot",
    "build_rfv_snapshot",
    "resolve_rfv_state",
]
