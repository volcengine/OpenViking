# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile targets: registry, incremental lifecycle and per-file versions.

State lives under ``/local/{account}/_system/compile/``: ``target_folders.json``
(summary index), ``target_folders/{id}/state.json`` (source of truth),
``target_folders/{id}/operations/{operation_id}.json`` (one record per directory operation),
``target_folders/{id}/operation_outputs/{operation_id}.json`` (immutable-path version references), and
``target_folders/{id}/file_versions/{sha256(uri)}.json`` (one version index per output
file). Each JSON's read-modify-write takes its own PathLock, so the layout stays
consistent across instances when PathLock uses the cache provider.

CompileService delegates to :class:`CompileLifecycle` from ``create``,
``on_start``, ``submit`` and ``on_finish``. A task runs after the request that
authorized it has ended, so Git access uses an internal ``bypass_acl`` context
for the task owner. Version bodies stay in Git; an index entry points at the
snapshot (``snapshot_id`` + ``snapshot_path``) holding its content.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import json
import math
import random
import sys
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import (
    Annotated,
    Any,
    AsyncContextManager,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Union,
)
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from openviking.core.namespace import classify_uri
from openviking.pyagfs import AsyncAGFSClient
from openviking.pyagfs.async_client import fs_ctx_from_agfs_path
from openviking.pyagfs.exceptions import (
    AGFSAlreadyExistsError,
    AGFSNotFoundError,
    AGFSNotSupportedError,
    GitConcurrentCommitError,
)
from openviking.resource.uri_mutation_coordinator import uri_matches_prefix
from openviking.server.identity import RequestContext, Role
from openviking.service.external_task_service import ExternalTaskSnapshot
from openviking.service.task_tracker_concurrency import KeyedAsyncLockPool, run_to_completion
from openviking.storage.acl import AclAction
from openviking.storage.errors import LockAcquisitionError, ResourceBusyError
from openviking.storage.viking_fs._base import LS_ALL_NODES
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
)
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

# Per-call immutable output references are published under the content lease.
_operation_outputs: ContextVar[Optional[dict]] = ContextVar("operation_outputs", default=None)


# ==========================================================================
# Registry storage
# ==========================================================================


LOCK_TIMEOUT_SECONDS = 30.0
HISTORY_RETENTION_SECONDS = 7 * 24 * 3600
SUMMARY_FIELDS = (
    "id",
    "to",
    "status",
    "deleted_at",
    "initialized",
    "auto_update",
    "update_schedule",
    "auto_update_enabled_at",
    "last_triggered_slot",
    "content_updated_at",
    "first_compile_created_at",
    "compile_type",
    "conflicts_num",
)


def new_record(target_id: str, to: str) -> Dict[str, Any]:
    return {
        "id": target_id,
        "to": to,
        "compile_type": "compile",  # Compile category for this target.
        "conflicts_num": 0,  # Number of conflicting files under this target.
        "status": "active",
        "deleted_at": None,  # Last deletion time; retained on reactivation to reject old tasks.
        "from": [],
        "skill": None,
        "instruction": None,
        "initialized": False,
        "auto_update": False,
        "schedule_configured": False,  # Explicit schedule settings survive ordinary submissions.
        "update_schedule": None,
        "auto_update_enabled_at": None,
        "last_triggered_slot": None,
        "last_error": None,
        "progress": {"source_commit": None, "processed_roots": []},
        "scheduled_progress": {},  # Successful scheduled inputs and source progress, isolated from manual runs.
        # Snapshot the per-file version indexes were last synced to.
        "versions_head": None,
        "active_task_id": None,
        "last_run": None,
        "initial_operation_id": None,  # First registered Compile task; missing on legacy targets.
        "content_updated_at": None,  # Business-file change time in Unix seconds; None when unknown.
        "first_compile_created_at": None,  # First task creation in Unix seconds; None when unknown.
        "execution_context": {"executor": None, "credential": None, "args": {}},
    }


def _normalize_schedule(record: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize stored records in place so disabled schedules are null on reads and writes."""
    if not record["auto_update"]:
        record["update_schedule"] = None
    return record


def summarize(record: Dict[str, Any]) -> Dict[str, Any]:
    summary = {key: record.get(key) for key in SUMMARY_FIELDS}
    summary["compile_type"] = record.get("compile_type", "compile")
    summary["conflicts_num"] = record.get("conflicts_num", 0)
    summary["running"] = bool(record.get("active_task_id"))
    return summary


class CompileStore(Protocol):
    """Target, operation and version-index persistence used by Compile.

    Edits save on clean exit. Object locks serialize their read-modify-write
    scopes; lock tokens are opaque and belong to the selected implementation.
    """

    async def register(self, account_id: str, to: str) -> str: ...
    async def find_id(
        self, account_id: str, to: str, *, include_deleted: bool = False
    ) -> Optional[str]: ...
    async def get(self, account_id: str, target_id: str) -> Optional[Dict[str, Any]]: ...
    async def get_by_to(
        self, account_id: str, to: str, *, include_deleted: bool = False
    ) -> Optional[Dict[str, Any]]: ...
    def edit(self, account_id: str, target_id: str) -> AsyncContextManager[Dict[str, Any]]: ...
    async def list_summaries(
        self, account_id: str, inherited_lease: Any = None
    ) -> List[Dict[str, Any]]: ...
    async def list_accounts(self) -> List[str]: ...
    async def mark_content_updated(self, account_id: str, target_id: str) -> None: ...

    async def get_operation(
        self, account_id: str, target_id: str, operation_id: str
    ) -> Optional[Dict[str, Any]]: ...
    async def put_operation(
        self, account_id: str, target_id: str, operation: Dict[str, Any]
    ) -> None: ...
    async def mark_content_changed(
        self, account_id: str, target_id: str, operation_id: str
    ) -> None: ...
    async def list_operations(
        self,
        account_id: str,
        target_id: str,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> List[Dict[str, Any]]: ...
    def lock_operation(
        self,
        account_id: str,
        target_id: str,
        operation_id: str,
        phase: Literal["create", "finish"],
    ) -> AsyncContextManager[None]: ...
    async def get_outputs(
        self, account_id: str, target_id: str, operation_id: str
    ) -> Optional[dict]: ...
    async def merge_outputs(
        self,
        account_id: str,
        target_id: str,
        operation_id: str,
        outputs: List[dict],
        *,
        effects: Optional[List[dict]] = None,
        ordinary_operation: Optional[str] = None,
        metadata_lease: Any = None,
    ) -> None: ...

    async def get_version_index(
        self, account_id: str, target_id: str, uri: str
    ) -> Optional[Dict[str, Any]]: ...
    async def list_version_indexes(
        self, account_id: str, target_id: str
    ) -> List[Dict[str, Any]]: ...
    async def put_version_index(
        self, account_id: str, target_id: str, index: Dict[str, Any], lock_token: Any = None
    ) -> None: ...
    async def delete_version_index(self, account_id: str, target_id: str, uri: str) -> None: ...
    async def clear_output_data(self, account_id: str, target_id: str) -> None: ...
    def lock_version_index(
        self, account_id: str, target_id: str, uri: str, inherited_lock: Any = None
    ) -> AsyncContextManager[Any]: ...


class CompileStateStore:
    """JSON persistence for Compile targets; all writes take the file's PathLock."""

    def __init__(self, agfs: Any) -> None:
        self._agfs = agfs if isinstance(agfs, AsyncAGFSClient) else AsyncAGFSClient(agfs)
        self._cleanup_locks = KeyedAsyncLockPool()
        self._cleanup_scan: Optional[AsyncIterator[Optional[tuple[str, str, str]]]] = None

    # ---------- paths ----------

    @staticmethod
    def root(account_id: str) -> str:
        return f"/local/{account_id}/_system/compile"

    def _targets_path(self, account_id: str) -> str:
        return f"{self.root(account_id)}/target_folders.json"

    def _target_dir(self, account_id: str, target_id: str) -> str:
        return f"{self.root(account_id)}/target_folders/{target_id}"

    def _record_path(self, account_id: str, target_id: str) -> str:
        return f"{self._target_dir(account_id, target_id)}/state.json"

    def _operation_path(self, account_id: str, target_id: str, operation_id: str) -> str:
        return f"{self._target_dir(account_id, target_id)}/operations/{operation_id}.json"

    # ---------- primitives ----------

    async def read_json(self, path: str) -> Optional[Any]:
        try:
            raw = await self._agfs.read(path)
        except (AGFSNotFoundError, FileNotFoundError):
            return None
        return json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)

    async def _ensure_parent(self, path: str, fs_ctx: Any = None) -> None:
        try:
            await self._agfs.ensure_parent_dirs(path, fs_ctx=fs_ctx)
        except AGFSAlreadyExistsError:
            pass

    async def write_json(self, path: str, value: Any, lease: Any = None) -> None:
        """Write JSON; pass the held lease when ``path`` is already locked by the caller."""
        fs_ctx = fs_ctx_from_agfs_path(path)
        if lease is not None:
            fs_ctx["lease_ref"] = lease["lease_ref"]
        await self._ensure_parent(path, fs_ctx)
        data = json.dumps(value, ensure_ascii=False).encode("utf-8")
        await self._agfs.write(path, data, fs_ctx=fs_ctx)

    @asynccontextmanager
    async def locked(self, path: str, inherited_lease: Any = None) -> AsyncIterator[Any]:
        """Lock a JSON path, or reuse a caller-owned tree lease covering that path."""
        if inherited_lease is not None:
            yield inherited_lease
            return
        await self._ensure_parent(path)
        lease = await self._agfs.pathlock_acquire_exact(path, timeout_secs=LOCK_TIMEOUT_SECONDS)
        try:
            yield lease
        finally:
            await self._agfs.pathlock_release(lease)

    # ---------- target_folders.json ----------

    async def list_summaries(
        self, account_id: str, inherited_lease: Any = None
    ) -> List[Dict[str, Any]]:
        path = self._targets_path(account_id)
        async with self.locked(path, inherited_lease):
            data = await self.read_json(path)
            return [_normalize_schedule(item) for item in (data or {}).get("targets", [])]

    async def _sync_summary(self, account_id: str, record: Dict[str, Any]) -> None:
        """Update only this target's entry so concurrent writers never clobber each other."""
        path = self._targets_path(account_id)
        async with self.locked(path) as lease:
            data = await self.read_json(path)
            targets = [
                item for item in (data or {}).get("targets", []) if item.get("id") != record["id"]
            ]
            targets.append(summarize(record))
            await self.write_json(path, {"targets": targets}, lease)

    async def find_id(
        self, account_id: str, to: str, *, include_deleted: bool = False
    ) -> Optional[str]:
        for item in await self.list_summaries(account_id):
            if item.get("to") == to and (include_deleted or item.get("status") == "active"):
                return item.get("id")
        return None

    # ---------- state.json ----------

    async def get(self, account_id: str, target_id: str) -> Optional[Dict[str, Any]]:
        record = await self.read_json(self._record_path(account_id, target_id))
        return _normalize_schedule(record) if record is not None else None

    async def get_by_to(
        self, account_id: str, to: str, *, include_deleted: bool = False
    ) -> Optional[Dict[str, Any]]:
        target_id = await self.find_id(account_id, to, include_deleted=include_deleted)
        return await self.get(account_id, target_id) if target_id else None

    @asynccontextmanager
    async def edit(self, account_id: str, target_id: str) -> AsyncIterator[Dict[str, Any]]:
        """Yield the record under its lock; it is saved (and summarized) on clean exit."""
        path = self._record_path(account_id, target_id)
        async with self.locked(path) as lease:
            record = await self.get(account_id, target_id)
            if record is None:
                raise LookupError(f"Compile target not found: {target_id}")
            yield record
            await self.write_json(path, _normalize_schedule(record), lease)
            await self._sync_summary(account_id, record)

    async def register(self, account_id: str, to: str) -> str:
        """Activate the path's persistent identity, resetting deleted output state."""
        path = self._targets_path(account_id)
        async with self.locked(path) as lease:
            data = await self.read_json(path)
            targets = list((data or {}).get("targets", []))
            existing = next((item["id"] for item in targets if item["to"] == to), None)
            if existing is None:
                target_id = f"target_{uuid4().hex[:16]}"
                record = new_record(target_id, to)
                await self.write_json(self._record_path(account_id, target_id), record)
                targets.append(summarize(record))
                await self.write_json(path, {"targets": targets}, lease)
                return target_id
        # State edits acquire the registry lock when saving, so release it first.
        async with self.edit(account_id, existing) as record:
            if record["status"] == "deleted":
                defaults = new_record(existing, to)
                for key in (
                    "status",
                    "initialized",
                    "progress",
                    "scheduled_progress",
                    "schedule_configured",
                    "versions_head",
                    "active_task_id",
                    "last_run",
                    "content_updated_at",
                    "conflicts_num",
                    "last_error",
                    "auto_update",
                    "auto_update_enabled_at",
                    "last_triggered_slot",
                ):
                    record[key] = defaults[key]
                record["execution_context"]["credential"] = None
        return existing

    async def mark_content_updated(self, account_id: str, target_id: str) -> None:
        """Save the observed business-file change time and synchronize its summary."""
        async with self.edit(account_id, target_id) as record:
            record["content_updated_at"] = time.time()

    # ---------- operations ----------

    async def get_operation(
        self, account_id: str, target_id: str, operation_id: str
    ) -> Optional[Dict[str, Any]]:
        return await self.read_json(self._operation_path(account_id, target_id, operation_id))

    async def put_operation(
        self, account_id: str, target_id: str, operation: Dict[str, Any]
    ) -> None:
        """Save operation state without clearing a concurrently recorded content change.

        The merged marker also updates the caller's metadata so finalization uses
        the same durable value when publishing the target's content update time.
        """
        path = self._operation_path(account_id, target_id, operation["operation_id"])
        async with self.locked(path) as lease:
            stored = await self.read_json(path)
            if stored and stored["meta"].get("content_changed"):
                operation["meta"]["content_changed"] = True
            await self.write_json(path, operation, lease)

    async def mark_content_changed(
        self, account_id: str, target_id: str, operation_id: str
    ) -> None:
        """Persist an actual Compile write once; raise NotFoundError for a missing operation."""
        path = self._operation_path(account_id, target_id, operation_id)
        async with self.locked(path) as lease:
            operation = await self.read_json(path)
            if operation is None:
                raise NotFoundError(operation_id, "operation")
            if not operation["meta"].get("content_changed"):
                operation["meta"]["content_changed"] = True
                await self.write_json(path, operation, lease)

    @asynccontextmanager
    async def lock_operation(
        self,
        account_id: str,
        target_id: str,
        operation_id: str,
        phase: Literal["create", "finish"],
    ) -> AsyncIterator[None]:
        """Serialize one operation phase; creation and finalization use separate locks."""
        async with self.locked(
            f"{self._operation_path(account_id, target_id, operation_id)}.{phase}"
        ):
            yield

    def _outputs_path(self, account_id: str, target_id: str, operation_id: str) -> str:
        return f"{self._target_dir(account_id, target_id)}/operation_outputs/{operation_id}.json"

    async def get_outputs(
        self, account_id: str, target_id: str, operation_id: str
    ) -> Optional[dict]:
        path = self._outputs_path(account_id, target_id, operation_id)
        async with self.locked(path):
            return await self.read_json(path)

    async def merge_outputs(
        self,
        account_id: str,
        target_id: str,
        operation_id: str,
        outputs: List[dict],
        *,
        effects: Optional[List[dict]] = None,
        ordinary_operation: Optional[str] = None,
        metadata_lease: Any = None,
    ) -> None:
        """Persist one reference per output URI; retries preserve already recorded outputs.

        Null version/snapshot references indicate unavailable versions. URI paths
        stay fixed at production time so moving a live file cannot change history.
        Path effects and task references retain content-commit order independently
        of the operation's later refresh and terminal history updates.
        """
        path = self._outputs_path(account_id, target_id, operation_id)
        async with self.locked(path, metadata_lease) as lease:
            data = await self.read_json(path) or {"outputs": []}
            merged = {item["uri"]: item for item in data["outputs"]}
            merged.update((item["uri"], item) for item in outputs)
            data["outputs"] = [merged[uri] for uri in sorted(merged)]
            if effects is not None:
                paths = data.setdefault("effects", [])
                paths.extend(effect for effect in effects if effect not in paths)
            if ordinary_operation is not None:
                references = data.setdefault("ordinary_operations", [])
                if ordinary_operation not in references:
                    references.append(ordinary_operation)
            await self.write_json(path, data, lease)

    async def list_dir(self, directory: str) -> List[str]:
        """JSON file names in ``directory``; empty when it does not exist."""
        try:
            entries = await self._agfs.ls(directory)
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        return [
            str(entry.get("name"))
            for entry in entries
            if str(entry.get("name") or "").endswith(".json")
        ]

    async def remove(self, path: str, *, recursive: bool = False, lease: Any = None) -> None:
        """Remove a path idempotently, reusing the caller's covering lease when supplied."""
        fs_ctx = fs_ctx_from_agfs_path(path)
        if lease is not None:
            fs_ctx["lease_ref"] = lease["lease_ref"]
        try:
            await self._agfs.rm(path, recursive=recursive, fs_ctx=fs_ctx)
        except (AGFSNotFoundError, FileNotFoundError):
            pass

    async def _iter_history(self) -> AsyncIterator[Optional[tuple[str, str, str]]]:
        """Yield history IDs without locks; account and target visits also consume the batch."""
        for account_id in await self.list_accounts():
            yield None
            try:
                targets = await self.list_summaries(account_id)
            except Exception:
                logger.exception("Cannot scan Compile history for account %s", account_id)
                continue
            for target in targets:
                yield None
                target_id = target["id"]
                try:
                    record = await self.get(account_id, target_id)
                    if record is None or record.get("active_task_id"):
                        continue
                    names = await self.list_dir(
                        f"{self._target_dir(account_id, target_id)}/operations"
                    )
                except Exception:
                    logger.exception("Cannot scan Compile history for %s/%s", account_id, target_id)
                    continue
                for name in names:
                    yield account_id, target_id, name[:-5]

    async def delete_expired(self, *, now: float, limit: int) -> int:
        """Scan at most ``limit`` visits and return the number of expired operations removed.

        ``now`` is Unix seconds. Scans resume across calls; failed records remain for
        the next pass. Only history JSON is removed; target state and versions stay.
        """
        if limit <= 0:
            return 0
        deleted = 0
        async with self._cleanup_locks.acquire("scan"):
            if self._cleanup_scan is None:
                self._cleanup_scan = self._iter_history()
            for _ in range(limit):
                try:
                    candidate = await anext(self._cleanup_scan)
                except StopAsyncIteration:
                    self._cleanup_scan = None
                    break
                except Exception:
                    self._cleanup_scan = None
                    logger.exception("Cannot enumerate Compile history")
                    break
                if candidate is None:
                    continue
                try:
                    deleted += await self._delete_expired_operation(
                        *candidate, cutoff=now - HISTORY_RETENTION_SECONDS
                    )
                except Exception:
                    logger.exception("Cannot clean Compile history %s/%s/%s", *candidate)
        return deleted

    async def _delete_expired_operation(
        self, account_id: str, target_id: str, operation_id: str, *, cutoff: float
    ) -> bool:
        """Return whether a terminal history pair older than Unix ``cutoff`` is removed."""
        operation_path = self._operation_path(account_id, target_id, operation_id)
        async with self.lock_operation(account_id, target_id, operation_id, "finish"):
            async with self.locked(self._record_path(account_id, target_id)):
                record = await self.get(account_id, target_id)
                if record is None or record.get("active_task_id"):
                    return False
                async with self.locked(operation_path) as operation_lease:
                    operation = await self.get_operation(account_id, target_id, operation_id)
                    finished_at = (operation or {}).get("finished_at")
                    if (
                        not operation
                        or operation.get("operation_id") != operation_id
                        or operation.get("status") not in TERMINAL
                        or isinstance(finished_at, bool)
                        or not isinstance(finished_at, (int, float))
                        or not math.isfinite(finished_at)
                        or finished_at < 0
                        or finished_at >= cutoff
                    ):
                        return False
                    outputs_path = self._outputs_path(account_id, target_id, operation_id)
                    async with self.locked(outputs_path) as outputs_lease:
                        await self.remove(outputs_path, lease=outputs_lease)
                    await self.remove(operation_path, lease=operation_lease)
                    return True

    async def list_operations(
        self,
        account_id: str,
        target_id: str,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return newest records after cursor; limit bounds memory, not JSON reads."""
        if limit is not None and limit <= 0:
            return []
        directory = f"{self._target_dir(account_id, target_id)}/operations"
        names = await self.list_dir(directory)
        before = None
        cursor_name = f"{cursor}.json" if cursor else None
        if cursor_name is not None:
            if cursor_name not in names:
                return []
            operation = await self.read_json(f"{directory}/{cursor_name}")
            if operation is None:
                return []
            before = (operation.get("created_at") or 0, operation["operation_id"])
        heap: list[tuple[float, str, Dict[str, Any]]] = []
        for name in names:
            if name == cursor_name:
                continue
            operation = await self.read_json(f"{directory}/{name}")
            if operation is None:
                continue
            key = (operation.get("created_at") or 0, operation["operation_id"])
            if before is not None and key >= before:
                continue
            candidate = (*key, operation)
            if limit is None or len(heap) < limit:
                heapq.heappush(heap, candidate)
            elif key > heap[0][:2]:
                heapq.heapreplace(heap, candidate)
        return [item[2] for item in sorted(heap, reverse=True)]

    async def list_accounts(self) -> List[str]:
        try:
            entries = await self._agfs.ls("/local")
        except (AGFSNotFoundError, FileNotFoundError):
            return []
        return [
            str(entry.get("name"))
            for entry in entries
            if entry.get("name")
            and entry.get("name") != "_system"
            and entry.get("isDir", entry.get("is_dir", False))
        ]

    # ---------- file version indexes ----------

    def _versions_dir(self, account_id: str, target_id: str) -> str:
        return f"{self._target_dir(account_id, target_id)}/file_versions"

    def _version_path(self, account_id: str, target_id: str, uri: str) -> str:
        name = hashlib.sha256(uri.encode("utf-8")).hexdigest()
        return f"{self._versions_dir(account_id, target_id)}/{name}.json"

    async def get_version_index(
        self, account_id: str, target_id: str, uri: str
    ) -> Optional[Dict[str, Any]]:
        """Read the stored index for an output URI, or None if it is absent."""
        return await self.read_json(self._version_path(account_id, target_id, uri))

    async def list_version_indexes(self, account_id: str, target_id: str) -> List[Dict[str, Any]]:
        """Read the target's existing indexes, ignoring files removed during enumeration."""
        directory = self._versions_dir(account_id, target_id)
        indexes = []
        for name in await self.list_dir(directory):
            index = await self.read_json(f"{directory}/{name}")
            if index is not None:
                indexes.append(index)
        return indexes

    async def put_version_index(
        self, account_id: str, target_id: str, index: Dict[str, Any], lock_token: Any = None
    ) -> None:
        """Save the index at its URI, using the caller's version lock when supplied."""
        await self.write_json(
            self._version_path(account_id, target_id, index["uri"]), index, lock_token
        )

    async def delete_version_index(self, account_id: str, target_id: str, uri: str) -> None:
        """Remove an output's index; a missing index is already deleted."""
        await self.remove(self._version_path(account_id, target_id, uri))

    async def clear_output_data(self, account_id: str, target_id: str) -> None:
        """Remove output manifests and version indexes, retaining operation history."""
        await self.remove(self._versions_dir(account_id, target_id), recursive=True)
        await self.remove(
            f"{self._target_dir(account_id, target_id)}/operation_outputs", recursive=True
        )

    @asynccontextmanager
    async def lock_version_index(
        self, account_id: str, target_id: str, uri: str, inherited_lock: Any = None
    ) -> AsyncIterator[Any]:
        """Lock an index, reusing a covering content lock during whole-account restore.

        Pass the yielded opaque token to put_version_index while this scope is held.
        """
        async with self.locked(
            self._version_path(account_id, target_id, uri), inherited_lock
        ) as token:
            yield token


# ==========================================================================
# Incremental lifecycle
# ==========================================================================


CONTROL_ARGS = ("mode", "auto_update", "update_schedule")
GIT_RETRIES = 3
SNAPSHOT_BUSY_SECONDS = 30.0
TERMINAL = frozenset({"completed", "failed", "cancelled"})
_RESULT_KEYS = {"added": "created", "modified": "updated", "deleted": "deleted"}
_NULL_OID = "0" * 40


def task_ctx(account_id: str, user_id: str) -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id=account_id, user_id=user_id),
        role=Role.USER,
        bypass_acl=True,
    )


def overlaps(a: str, b: str) -> bool:
    return a == b or a.startswith(f"{b}/") or b.startswith(f"{a}/")


class DailySchedule(BaseModel):
    """Beijing-time trigger time; weekly adds ``weekday`` (1=Monday … 7=Sunday)."""

    model_config = ConfigDict(extra="forbid")

    frequency: Literal["daily"]
    hour: int = Field(default=0, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class WeeklySchedule(DailySchedule):
    frequency: Literal["weekly"]
    weekday: int = Field(default=1, ge=1, le=7)


UpdateSchedule = Annotated[Union[DailySchedule, WeeklySchedule], Field(discriminator="frequency")]


def parse_schedule(value: Any) -> Dict[str, Any]:
    """Validate a schedule and fill defaults."""
    try:
        return TypeAdapter(UpdateSchedule).validate_python(value).model_dump()
    except ValidationError as exc:
        error = exc.errors(include_url=False)[0]
        field = "".join(f".{part}" for part in error["loc"][1:])  # loc[0] is the frequency tag
        raise InvalidArgumentError(f"args.update_schedule{field}: {error['msg']}") from exc


def split_control_args(args: Optional[Mapping[str, Any]]) -> tuple[dict, dict]:
    """Separate kernel control args from Runtime args."""
    runtime = dict(args or {})
    control = {key: runtime.pop(key) for key in CONTROL_ARGS if key in runtime}
    if control.get("mode", "incremental") not in {"incremental", "full"}:
        raise InvalidArgumentError("args.mode must be 'incremental' or 'full'")
    if control.get("update_schedule") is not None:
        control["update_schedule"] = parse_schedule(control["update_schedule"])
    if not isinstance(control.get("auto_update", False), bool):
        raise InvalidArgumentError("args.auto_update must be a boolean")
    return control, runtime


def new_operation(
    operation_id: str,
    operation_type: str,
    params: Dict[str, Any],
    actor: str,
    *,
    status: str = "running",
) -> Dict[str, Any]:
    """Build one directory operation; timestamps use Unix seconds and conflicts default to zero."""
    now = time.time()
    return {
        "operation_id": operation_id,
        "operation_type": operation_type,
        "actor": actor,
        "status": status,
        "created_at": now,
        "started_at": now if status == "running" else None,
        "finished_at": None,
        "params": params,
        "result": None,
        "conflicts_num": 0,
        "error": None,
        "meta": {},
    }


def new_compile_operation(task: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    """Build a queued Compile operation linked to its execution task and original trigger."""
    request = task.meta["request"]
    trigger = request["compile"]["trigger"]
    operation_type = "schedule_compile" if trigger == "scheduled" else "manual_compile"
    if record.get("initial_operation_id") == task.task_id:
        operation_type = "initial"
    return new_operation(
        task.task_id, operation_type, _params(request), task.user_id, status="queued"
    ) | {
        "created_at": task.created_at,
        "meta": {
            "task_id": task.task_id,
            "target_deleted_at": request["compile"]["deleted_at"],  # Target lifecycle boundary.
            "trigger": trigger,
            "target_base": None,
            "target_head": None,
            "snapshot_committed": False,
            "content_changed": False,  # Content changes requiring a finalization timestamp.
            "reindex_task_id": None,
            "base": None,
            "head": None,
            "changed_roots": [],
            "changes": None,
        },
    }


class CompileLifecycle:
    def __init__(self, store: CompileStore, viking_fs: Any) -> None:
        self.store = self._store = store
        self._fs = viking_fs
        self.versions = CompileVersions(store, viking_fs)
        # Installed by CompileScheduler: async (account_id, record) -> None.
        self.reschedule = None
        # Installed by CompileScheduler: async (ctx) -> (executor, credential).
        self.save_credential = None

    # ---------- submission ----------

    async def prepare(
        self,
        payload: Dict[str, Any],
        ctx: RequestContext,
        *,
        instruction_set: bool,
        scheduled_target_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Save the registry for a manual request and return the task payload.

        Scheduled requests are built from an already locked record, so they
        only receive Compile metadata and never write the registry here.
        """
        control, runtime_args = split_control_args(payload.get("args"))
        task_payload = {key: value for key, value in payload.items() if key != "args"}
        if scheduled_target_id is not None:
            target_id, trigger = scheduled_target_id, "scheduled"
            record = await self._store.get(ctx.account_id, target_id)
        else:
            record = await self._save_submission(
                payload, control, runtime_args, instruction_set, ctx
            )
            target_id, trigger = record["id"], "manual"
            runtime_args = {**record["execution_context"]["args"], **runtime_args}
        if runtime_args:
            task_payload["args"] = runtime_args
        task_payload["compile"] = {
            "target_id": target_id,
            "deleted_at": record["deleted_at"],
            "mode": control.get("mode", "incremental"),
            "trigger": trigger,
        }
        return task_payload

    async def _save_submission(
        self,
        payload: Dict[str, Any],
        control: Dict[str, Any],
        runtime_args: Dict[str, Any],
        instruction_set: bool,
        ctx: RequestContext,
    ) -> Dict[str, Any]:
        """Save submission settings and return their state, including the deletion boundary."""
        to, sources = payload["to"], payload["from"]
        await self.validate_layout(ctx.account_id, to, sources)
        target_id = await self._store.register(ctx.account_id, to)
        async with self._store.edit(ctx.account_id, target_id) as record:
            configures_schedule = "auto_update" in control or "update_schedule" in control
            # Legacy targets can hold disabled schedule settings without a configuration marker.
            has_schedule = (
                record.get("schedule_configured", record["skill"] is not None)
                or record["auto_update"]
            )
            if configures_schedule or not has_schedule:
                self.apply_sources(record, sources, payload["skill"])
                if instruction_set and configures_schedule:
                    record["instruction"] = payload.get("instruction") or None
                persisted = {k: v for k, v in runtime_args.items() if k != "last_compile_time"}
                if persisted:
                    record["execution_context"]["args"] = persisted
            if configures_schedule:
                await self.apply_schedule(
                    record,
                    ctx,
                    control.get("auto_update", record["auto_update"]),
                    control.get("update_schedule", record["update_schedule"]),
                )
        if self.reschedule is not None:
            await self.reschedule(ctx.account_id, record)
        return record

    async def validate_layout(self, account_id: str, to: str, sources: List[str]) -> None:
        for source in sources:
            if overlaps(source, to):
                raise InvalidArgumentError(f"Compile source overlaps target: {source}")
        for item in await self._store.list_summaries(account_id):
            if item.get("status") == "active" and item["to"] != to and overlaps(item["to"], to):
                raise InvalidArgumentError(f"Compile target nests another target: {item['to']}")

    @staticmethod
    def apply_sources(record: Dict[str, Any], sources: List[str], skill: str) -> None:
        """Save configured sources and Skill without advancing successful execution progress."""
        record["from"] = list(sources)
        record["skill"] = skill

    async def apply_schedule(
        self,
        record: Dict[str, Any],
        ctx: RequestContext,
        auto_update: bool,
        schedule: Optional[Dict[str, Any]],
    ) -> None:
        """Save update settings; enabling requires a rule, and disabling clears it and the key."""
        from openviking.service.compile_scheduler import now_iso

        if auto_update and schedule is None:
            raise InvalidArgumentError("update_schedule is required when auto_update is true")
        if not auto_update:
            schedule = None
        changed = auto_update != record["auto_update"] or schedule != record["update_schedule"]
        if auto_update:
            if self.save_credential is None:
                # Credentials are encrypted only on scheduler instances, where they are decrypted.
                raise FailedPreconditionError(
                    "Scheduled Compile is disabled on this instance "
                    "(compile_api.scheduler_enabled=false)",
                    details={"reason": "COMPILE_SCHEDULER_DISABLED"},
                )
            executor, credential = await self.save_credential(ctx)
            record["execution_context"]["executor"] = executor
            record["execution_context"]["credential"] = credential
            record["last_error"] = None
        else:
            record["execution_context"]["credential"] = None
        if changed:
            record["auto_update_enabled_at"] = now_iso() if auto_update else None
        record["auto_update"] = auto_update
        record["schedule_configured"] = True
        record["update_schedule"] = schedule

    async def record_created(self, task: Any) -> None:
        """Claim the first Compile identity and earliest creation time under the target lock."""
        meta = task.meta["request"].get("compile")
        if meta is None:
            return
        async with self._store.edit(task.account_id, meta["target_id"]) as record:
            self._record_identity(task, record)

    @staticmethod
    def _record_identity(task: Any, record: Dict[str, Any]) -> None:
        """Retain the target's first task identity and earliest known creation time."""
        if "initial_operation_id" in record and record["initial_operation_id"] is None:
            record["initial_operation_id"] = task.task_id
        # Missing fields identify legacy targets whose first creation time is unknown.
        if "first_compile_created_at" in record:
            first = record["first_compile_created_at"]
            record["first_compile_created_at"] = (
                min(first, task.created_at) if first is not None else task.created_at
            )

    async def record_queued(
        self, task: Any, *, locked_record: Optional[Dict[str, Any]] = None
    ) -> None:
        """Save queued history and outputs under the target lock, reusing a caller-held record."""
        meta = task.meta["request"]["compile"]
        if locked_record is None:
            async with self._store.edit(task.account_id, meta["target_id"]) as record:
                await self.record_queued(task, locked_record=record)
            return
        if not self._task_is_current(locked_record, task):
            return
        async with self._store.lock_operation(
            task.account_id, meta["target_id"], task.task_id, "create"
        ):
            run = await self._store.get_operation(task.account_id, meta["target_id"], task.task_id)
            if run is None:
                self._record_identity(task, locked_record)
                await self._store.merge_outputs(
                    task.account_id, meta["target_id"], task.task_id, []
                )
                await self._store.put_operation(
                    task.account_id,
                    meta["target_id"],
                    new_compile_operation(task, locked_record),
                )

    # ---------- execution hooks ----------

    @staticmethod
    def _task_is_current(record: Dict[str, Any], task: Any) -> bool:
        """Only submissions registered after the last deletion can use active output state."""
        return record["status"] == "active" and (
            task.meta["request"]["compile"]["deleted_at"] == record["deleted_at"]
        )

    @asynccontextmanager
    async def _locked_output(self, to: str, ctx: RequestContext) -> AsyncIterator[Any]:
        """Yield a tree lease for snapshot/index reconciliation, never Runtime execution.

        Busy processors raise ResourceBusyError; every exit releases the lease.
        """
        try:
            lease = await self._fs._async_agfs.pathlock_acquire_tree(
                self._fs._uri_to_path(to, ctx=ctx)
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError("Compile output is being processed", uri=to) from exc
        try:
            yield lease
        finally:
            await self._fs._async_agfs.pathlock_release(lease)

    async def on_start(self, task: Any) -> Any:
        """Claim the target across instances, then snapshot and plan once."""
        meta = task.meta["request"].get("compile")
        if meta is None:
            return "proceed"
        account_id, target_id = task.account_id, meta["target_id"]
        async with self._store.edit(account_id, target_id) as record:
            if not self._task_is_current(record, task):
                return ExternalTaskSnapshot(
                    status="cancelled", error_message="Compile target was deleted"
                )
            run = await self._store.get_operation(account_id, target_id, task.task_id)
            if run and run["status"] in TERMINAL and run["finished_at"] is not None:
                # Replayed completions must not claim the target and restore an older baseline.
                return ExternalTaskSnapshot(status=run["status"], result=run.get("result") or {})
            owner = record["active_task_id"]
            if owner and owner != task.task_id:
                other = await self._store.get_operation(account_id, target_id, owner)
                if other is not None and other["status"] not in TERMINAL:
                    return "busy"
            record["active_task_id"] = task.task_id
            await self.record_queued(task, locked_record=record)
        run = await self._store.get_operation(account_id, target_id, task.task_id)
        if run is None or run["status"] == "queued":
            try:
                run = await self._begin_run(task, record, run)
            except ResourceBusyError:
                return "busy"
        if run["status"] in TERMINAL or run["status"] == "committing":
            # Committing runs resume locally without querying the Runtime session.
            return ExternalTaskSnapshot(
                status="completed" if run["status"] == "committing" else run["status"],
                result=run.get("result") or {},
            )
        return "proceed"

    async def _begin_run(
        self, task: Any, record: Dict[str, Any], run: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Return the persisted run planned from a task, target record and optional queued run.

        System Skills use live reads and URI identity; other Skills use snapshots.
        An unavailable current source snapshot requires full execution; busy scopes raise ResourceBusyError.
        """
        meta, request = task.meta["request"]["compile"], task.meta["request"]
        ctx = task_ctx(task.account_id, task.user_id)
        incremental = meta["mode"] == "incremental"
        system_skill = classify_uri(request["skill"]).is_system
        run = run or new_compile_operation(task, record)
        run_meta = run["meta"]
        run["actor"] = task.user_id
        run["status"] = "running"
        run["started_at"] = time.time()
        run_meta["skill_snapshot"] = {"uri": request["skill"], "snapshot_id": None}
        if not system_skill:
            async with self._locked_output(request["skill"], ctx) as lease:
                run_meta["skill_snapshot"]["snapshot_id"] = await commit_snapshot(
                    self._fs,
                    [request["skill"]],
                    f"compile {task.task_id} skill",
                    ctx,
                    wait_busy=False,
                    lease_ref=lease,
                )
        progress_key = "scheduled_progress" if meta["trigger"] == "scheduled" else "progress"
        progress = record.get(progress_key) or {}
        previous_skill = progress.get("skill_snapshot") or {}
        mode = "full"
        skill_base = previous_skill.get("snapshot_id")
        skill_head = run_meta["skill_snapshot"]["snapshot_id"]
        if (
            incremental
            and (request.get("instruction") or "").strip() == progress.get("instruction")
            and previous_skill.get("uri") == request["skill"]
        ):
            if system_skill:
                mode = "incremental"
            elif skill_base and skill_head:
                # Skill dependencies include package files filtered from ordinary source diffs.
                skill_changes = await self._fs._async_agfs.run(
                    "git_diff_paths",
                    account=ctx.account_id,
                    base=skill_base,
                    head=skill_head,
                    paths=[self._fs._uri_to_tree_path(request["skill"], ctx=ctx)],
                )
                if not skill_changes:
                    mode = "incremental"
        paths = [request["to"], *request["from"]]
        async with self._locked_output(request["to"], ctx) as lease:
            run_meta["target_base"] = await commit_snapshot(
                self._fs,
                paths,
                f"compile {task.task_id} start",
                ctx,
                wait_busy=False,
                lease_ref=lease,
            )
            if not run_meta["target_base"]:
                mode = "full"
            run_meta["execution"] = {"mode": mode}
            await self.versions.record_external(
                task.account_id, record, run_meta["target_base"], ctx
            )
            run_meta.update(
                await self._plan(
                    {} if mode == "full" else progress,
                    request["from"],
                    run_meta["target_base"],
                    ctx,
                )
            )
            if mode == "incremental" and not run_meta["changed_roots"] and record["initialized"]:
                run["status"] = "completed"
            await self._store.put_operation(task.account_id, meta["target_id"], run)
        return run

    async def _plan(
        self, progress: Dict[str, Any], sources: List[str], head: Optional[str], ctx: RequestContext
    ) -> Dict[str, Any]:
        """Compare source roots against one trigger's successful baseline; empty progress scans all roots."""
        base = progress.get("source_commit")
        processed = set(progress.get("processed_roots") or [])
        compared = [source for source in sources if base and source in processed]
        added_roots = [source for source in sources if source not in compared]
        changes = (
            await self._fs.diff_paths(dirs=compared, base=base, head=head, ctx=ctx)
            if compared and head
            else []
        )
        summary = dict.fromkeys(_RESULT_KEYS, 0)
        for change in changes:
            summary[change["change_type"]] += 1
        changed_roots = added_roots + [
            root for root in compared if any(c["uri"].startswith(f"{root}/") for c in changes)
        ]
        return {
            "base": base,
            "head": head,
            "diff_dirs": compared,
            "full_dirs": added_roots,
            "changed_roots": changed_roots,
            "changes": summary,
        }

    async def runtime_changes(
        self, payload: Mapping[str, Any], account_id: str, task_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return Skill identity and effective execution mode with the persisted source plan."""
        meta = payload.get("compile")
        if not meta:
            return None
        run = await self._store.get_operation(account_id, meta["target_id"], task_id)
        if run is None or run["meta"].get("changes") is None:
            return None
        run_meta = run["meta"]
        return {
            "base": run_meta["base"],
            "head": run_meta["head"],
            "diff_dirs": run_meta["diff_dirs"],
            "full_dirs": run_meta["full_dirs"],
            "execution": run_meta.get("execution"),
            "skill_snapshot": run_meta.get("skill_snapshot"),
        }

    async def on_finish(self, task: Any, snapshot: ExternalTaskSnapshot) -> ExternalTaskSnapshot:
        """Commit successful output and accept reindex before completing the run.

        Saved snapshot and reindex identities allow interrupted finalization to
        resume without publishing a second output version or reindex request.
        """
        meta, request = task.meta["request"].get("compile"), task.meta["request"]
        if meta is None:
            return snapshot
        account_id, target_id = task.account_id, meta["target_id"]
        async with self._store.lock_operation(account_id, target_id, task.task_id, "finish"):
            run = await self._store.get_operation(account_id, target_id, task.task_id)
            if run is not None and run["finished_at"] is None:
                run_meta = run["meta"]
                ctx = task_ctx(task.account_id, task.user_id)
                record = await self._store.get(account_id, target_id)
                if not self._task_is_current(record, task):
                    snapshot = ExternalTaskSnapshot(
                        status=run["status"] if run["status"] in TERMINAL else "cancelled",
                        result=run.get("result"),
                        error_message="Compile target was deleted",
                    )
                elif run["started_at"] is None or run["status"] == "completed":
                    pass  # Unstarted and unchanged runs have no output to publish.
                elif snapshot.status == "completed":
                    from openviking.server.dependencies import get_service
                    from openviking.service.reindex_executor import REINDEX_ENQUEUE_ERROR
                    from openviking.service.task_tracker import get_task_tracker

                    if run["status"] != "committing":
                        run["status"] = "committing"
                        await self._store.put_operation(account_id, target_id, run)
                    # Reindex can own content locks once versions are durable.
                    if not run_meta.get("reindex_task_id"):
                        async with self._locked_output(request["to"], ctx) as lease:
                            if not run_meta.get("snapshot_committed"):
                                run_meta["target_head"] = await self._commit(
                                    [request["to"]],
                                    f"compile {task.task_id} finish",
                                    ctx,
                                    lease_ref=lease,
                                )
                                run_meta["snapshot_committed"] = True
                                await self._store.put_operation(account_id, target_id, run)
                            head = run_meta["target_head"]
                            changes = (
                                await self._fs.diff_paths(
                                    dirs=[request["to"]],
                                    base=run_meta["target_base"],
                                    head=head,
                                    ctx=ctx,
                                )
                                if head and run_meta["target_base"]
                                else []
                            )
                            run["result"] = dict.fromkeys(_RESULT_KEYS.values(), 0)
                            for change in changes:
                                run["result"][_RESULT_KEYS[change["change_type"]]] += 1
                            record = await self._store.get(account_id, target_id)
                            await self.versions.record_compile(
                                account_id, record, run, head, changes, ctx
                            )
                            if (
                                any(is_versioned(change["uri"]) for change in changes)
                                and (record.get("content_updated_at") or 0) < run["started_at"]
                            ):
                                run_meta["content_changed"] = True
                    reindex_id = run_meta.get("reindex_task_id")
                    previous = (
                        await get_task_tracker().get(
                            reindex_id, account_id=account_id, user_id=task.user_id
                        )
                        if reindex_id
                        else None
                    )
                    if reindex_id is None or (
                        previous is not None and previous.error == REINDEX_ENQUEUE_ERROR
                    ):
                        run_meta["reindex_task_id"] = str(uuid4())
                        await self._store.put_operation(account_id, target_id, run)
                    await get_service().reindex(
                        uri=request["to"],
                        mode="semantic_and_vectors",
                        force=False,
                        recursive=True,
                        wait=False,
                        ctx=ctx,
                        task_id=run_meta["reindex_task_id"],
                    )
                else:
                    if run_meta["target_base"]:
                        await self._rollback(request["to"], run, ctx)
                    run["error"] = snapshot.error_message or snapshot.status
                run["status"] = snapshot.status
                run["finished_at"] = time.time()
                await self._store.put_operation(account_id, target_id, run)
            await self._release(account_id, target_id, task, run)
            return snapshot

    async def _release(
        self, account_id: str, target_id: str, task: Any, run: Optional[Dict[str, Any]]
    ) -> None:
        first_success = False
        async with self._store.edit(account_id, target_id) as record:
            if not self._task_is_current(record, task):
                return
            task_id = task.task_id
            if (
                record["active_task_id"] == task_id
                or (
                    record["active_task_id"] is None
                    and (record.get("last_run") or {}).get("task_id") == task_id
                )
            ) and run is not None:
                # Write hooks can publish their marker after final operation state is saved.
                run = await self._store.get_operation(account_id, target_id, task_id) or run
                run_meta = run["meta"]
                record["last_run"] = {
                    "task_id": task_id,
                    "status": run["status"],
                    "finished_at": run["finished_at"],
                    "error": run.get("error"),
                }
                if run["finished_at"] is not None and run_meta.get("content_changed"):
                    record["content_updated_at"] = max(
                        record.get("content_updated_at") or 0, run["finished_at"]
                    )
                if run["status"] == "completed":
                    first_success = not record["initialized"]
                    record["initialized"] = True
                    if run_meta.get("head"):
                        progress_key = (
                            "scheduled_progress"
                            if run_meta["trigger"] == "scheduled"
                            else "progress"
                        )
                        record[progress_key] = {
                            "source_commit": run_meta["head"],
                            "processed_roots": list(run["params"]["from"]),
                            "instruction": (run["params"].get("instruction") or "").strip(),
                            "skill_snapshot": run_meta.get("skill_snapshot"),
                        }
                # Rollback snapshots include the ordinary edits retained by the run.
                synced = run_meta.get("target_head") or run_meta.get("target_base")
                if synced:
                    record["versions_head"] = synced
            if record["active_task_id"] == task_id:
                record["active_task_id"] = None
        if first_success and self.reschedule is not None:
            await self.reschedule(account_id, record)

    async def _rollback(self, to: str, run: Dict[str, Any], ctx: RequestContext) -> None:
        """Restore the start snapshot overlaid with this task's confirmed ordinary operations.

        The tree lease settles in-flight content commits before reading the ordered
        task references. Immutable outputs survive deleted version indexes; the
        completion marker closes association before new content can acquire a lease.
        """
        run_meta = run["meta"]
        target_id = await self._store.find_id(ctx.account_id, to)
        restored, deleted = [], []
        try:
            async with self._locked_output(to, ctx) as lease:
                if run_meta.get("rollback_head"):
                    run_meta["target_head"] = run_meta["rollback_head"]
                    return
                manifest = await self._store.get_outputs(
                    ctx.account_id, target_id, run_meta["task_id"]
                )
                head = await self._commit(
                    [to], f"compile {run_meta['task_id']} rollback point", ctx, lease_ref=lease
                )
                if not head:
                    return
                record = await self._store.get(ctx.account_id, target_id)
                changes = {
                    change["uri"]: change["change_type"]
                    for change in await self._fs.diff_paths(
                        dirs=[to], base=run_meta["target_base"], head=head, ctx=ctx
                    )
                }
                desired = {
                    uri: None
                    if change == "added"
                    else {"snapshot_id": run_meta["target_base"], "snapshot_path": uri}
                    for uri, change in changes.items()
                }
                directories, scopes, protected_scopes = set(), set(), set()
                for operation_id in (manifest or {}).get("ordinary_operations", []):
                    operation = await self._store.get_outputs(
                        ctx.account_id, target_id, operation_id
                    )
                    if operation is None:
                        raise FailedPreconditionError("Compile operation effects are unavailable")
                    for effect in operation.get("effects", []):
                        uri = effect["uri"]
                        if not uri_matches_prefix(uri, to):
                            raise FailedPreconditionError(
                                "Compile operation effect is outside target"
                            )
                        if effect["kind"] in {"deleted", "snapshot"}:
                            desired = {
                                path: version
                                for path, version in desired.items()
                                if not uri_matches_prefix(path, uri)
                            }
                            directories = {
                                path for path in directories if not uri_matches_prefix(path, uri)
                            }
                            protected_scopes = {
                                path
                                for path in protected_scopes
                                if not uri_matches_prefix(path, uri)
                            }
                        if effect["kind"] == "deleted":
                            scopes.add(uri)
                        elif effect["kind"] == "directory":
                            directories.add(uri)
                        elif effect["kind"] == "snapshot":
                            if not effect.get("snapshot_id"):
                                raise FailedPreconditionError(
                                    "Compile operation snapshot is unavailable"
                                )
                            scopes = {path for path in scopes if not uri_matches_prefix(path, uri)}
                            if effect["is_dir"]:
                                directories.add(uri)
                                protected_scopes.add(uri)
                            for change in await self._fs.diff_paths(
                                dirs=[uri], base=effect["snapshot_id"], head=head, ctx=ctx
                            ):
                                path = change["uri"]
                                desired[path] = (
                                    None
                                    if change["change_type"] == "added"
                                    else {
                                        "kind": "file",
                                        "snapshot_id": effect["snapshot_id"],
                                        "snapshot_path": path,
                                    }
                                )
                            if not effect["is_dir"]:
                                desired[uri] = {
                                    "kind": "file",
                                    "snapshot_id": effect["snapshot_id"],
                                    "snapshot_path": uri,
                                }
                    for output in operation["outputs"]:
                        uri = output["uri"]
                        if not uri_matches_prefix(uri, to):
                            raise FailedPreconditionError(
                                "Compile operation output is outside target"
                            )
                        if not output.get("snapshot_id"):
                            raise FailedPreconditionError(
                                "Compile operation snapshot is unavailable"
                            )
                        desired[uri] = {"kind": "file", **output, "snapshot_path": uri}
                await self.versions.record_compile(
                    ctx.account_id,
                    record,
                    run,
                    head,
                    [
                        {"uri": uri, "change_type": change}
                        for uri, change in changes.items()
                        if change != "deleted"
                    ],
                    ctx,
                )
                indexes = {
                    index["uri"]: index
                    for index in await self.versions._indexes(ctx.account_id, target_id)
                }
                changed = False
                ordinary_files = {
                    uri
                    for uri, version in desired.items()
                    if version is not None and version.get("kind") == "file"
                }
                protected_dirs = set(directories)
                for uri in ordinary_files | directories:
                    parent = uri.rsplit("/", 1)[0]
                    while parent != to and uri_matches_prefix(parent, to):
                        protected_dirs.add(parent)
                        parent = parent.rsplit("/", 1)[0]
                removals = scopes | {uri for uri, version in desired.items() if version is None}
                pending = list(removals)
                while pending:
                    uri = pending.pop()
                    if uri in ordinary_files or not await self._fs.exists(uri, ctx=ctx):
                        continue
                    # Git snapshots omit empty directories inside a protected ordinary scope.
                    if uri in protected_scopes and uri not in desired:
                        continue
                    if any(
                        uri_matches_prefix(path, uri) for path in ordinary_files | protected_dirs
                    ):
                        stat = await self._fs.stat(uri, ctx=ctx)
                        if stat.get("isDir"):
                            pending.extend(
                                entry["uri"]
                                for entry in await self._fs.ls(
                                    uri, show_all_hidden=True, node_limit=LS_ALL_NODES, ctx=ctx
                                )
                                if not (
                                    uri in protected_dirs
                                    and entry["uri"].rsplit("/", 1)[-1] in _DERIVED
                                )
                            )
                            continue
                    await self._fs.rm(uri, recursive=True, ctx=ctx, lease_ref=lease)
                    deleted.append(self._fs._uri_to_tree_path(uri, ctx=ctx))
                    changed = changed or is_versioned(uri)
                for uri in sorted(protected_dirs, key=lambda value: (value.count("/"), value)):
                    if await self._fs.exists(uri, ctx=ctx) and not (
                        await self._fs.stat(uri, ctx=ctx)
                    ).get("isDir"):
                        await self._fs.rm(uri, recursive=True, ctx=ctx, lease_ref=lease)
                        deleted.append(self._fs._uri_to_tree_path(uri, ctx=ctx))
                    await self._fs.mkdir(uri, exist_ok=True, ctx=ctx, lease_ref=lease)
                    for name in (".abstract.md", ".overview.md"):
                        marker = f"{uri}/{name}"
                        if await self._fs.exists(marker, ctx=ctx):
                            restored.append(self._fs._uri_to_tree_path(marker, ctx=ctx))
                for uri, version in desired.items():
                    if version is None:
                        continue
                    content = await self._fs.show(
                        version["snapshot_id"], path=version["snapshot_path"], ctx=ctx
                    )
                    if await self._fs.exists(uri, ctx=ctx):
                        if (await self._fs.stat(uri, ctx=ctx)).get("isDir"):
                            await self._fs.rm(uri, recursive=True, ctx=ctx, lease_ref=lease)
                            deleted.append(self._fs._uri_to_tree_path(uri, ctx=ctx))
                        elif await self._fs.read_file_bytes(uri, ctx=ctx) == content:
                            continue
                    await self._fs.write_file_bytes(uri, content, ctx=ctx, lease_ref=lease)
                    restored.append(self._fs._uri_to_tree_path(uri, ctx=ctx))
                    changed = changed or is_versioned(uri)
                if changed:
                    await self._store.mark_content_updated(ctx.account_id, target_id)
                for uri, index in indexes.items():
                    if not await self._fs.exists(uri, ctx=ctx):
                        await self._store.delete_version_index(ctx.account_id, target_id, uri)
                        continue
                    retained = [
                        v
                        for v in index["versions"]
                        if not (
                            v["meta"]["task_id"] == run_meta["task_id"] and v["source"] == "compile"
                        )
                    ]
                    if retained == index["versions"]:
                        continue
                    if retained:
                        index["versions"] = retained
                        await self._store.put_version_index(ctx.account_id, target_id, index)
                    else:
                        await self._store.delete_version_index(ctx.account_id, target_id, uri)
                run_meta["target_head"] = await self._commit(
                    [to], f"compile {run_meta['task_id']} rollback", ctx, lease_ref=lease
                )
                run_meta["rollback_head"] = run_meta["target_head"]
                await self._store.put_operation(ctx.account_id, target_id, run)
        finally:
            if restored or deleted:
                try:
                    await self._fs._schedule_restore_reindex_for_paths(
                        written_paths=restored, deleted_paths=deleted, project_dir=to, real_ctx=ctx
                    )
                except Exception:
                    logger.exception("Compile rollback reindex scheduling failed")
                    self._fs._schedule_vector_rebuild(written=restored, deleted=deleted, ctx=ctx)

    # ---------- git ----------

    async def _commit(
        self,
        paths: List[str],
        message: str,
        ctx: RequestContext,
        *,
        lease_ref: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        return await commit_snapshot(self._fs, paths, message, ctx, lease_ref=lease_ref)


async def retry_git(action: Any) -> Any:
    """Retry a Git ref update that lost a CAS race, with a short random backoff."""
    for attempt in range(GIT_RETRIES):
        try:
            return await action()
        except GitConcurrentCommitError:
            if attempt == GIT_RETRIES - 1:
                raise
            await asyncio.sleep(random.uniform(0.1, 0.5))


async def commit_snapshot(
    viking_fs: Any,
    paths: List[str],
    message: str,
    ctx: RequestContext,
    *,
    lease_ref: Optional[Dict[str, Any]] = None,
    wait_busy: bool = True,
) -> Optional[str]:
    """Return the snapshot oid, or None when Git is disabled (Compile then runs in full).

    Busy paths wait up to ``SNAPSHOT_BUSY_SECONDS`` unless ``wait_busy=False``
    lets the caller defer work through its queue.
    """
    deadline = time.monotonic() + SNAPSHOT_BUSY_SECONDS
    kwargs = {"lease_ref": lease_ref} if lease_ref is not None else {}
    while True:
        try:
            result = await retry_git(
                lambda: viking_fs.commit(message=message, paths=paths, ctx=ctx, **kwargs)
            )
            break
        except AGFSNotSupportedError:
            return None
        except ResourceBusyError:
            if not wait_busy or time.monotonic() >= deadline:
                raise
            await asyncio.sleep(0.2)
    oid = result.get("commit_oid")
    return None if not oid or oid == _NULL_OID else oid


def _params(request: Mapping[str, Any]) -> Dict[str, Any]:
    meta = request["compile"]
    return {
        "from": request["from"],
        "to": request["to"],
        "skill": request["skill"],
        "instruction": request.get("instruction"),
        "args": {"mode": meta["mode"], **(request.get("args") or {})},
    }


# ==========================================================================
# Per-file versions
# ==========================================================================


MAX_VERSIONS = 100
_DERIVED = frozenset({".abstract.md", ".overview.md", ".relations.json"})
_VERSION_META_DEFAULTS = {
    "task_id": None,
    "restored_from": None,
    "after_compile_snapshot": False,
}


def is_versioned(uri: str) -> bool:
    return uri.rsplit("/", 1)[-1] not in _DERIVED


class CompileVersions:
    """Version metadata preserves ordinary edits across Compile retries and rollback."""

    def __init__(self, store: CompileStore, viking_fs: Any) -> None:
        self._store = store
        self._fs = viking_fs
        # Installed by CompileScheduler: async (account_id, record) -> None.
        self.reschedule = None

    # ---------- index storage ----------

    async def load(self, account_id: str, target_id: str, uri: str) -> Optional[Dict[str, Any]]:
        return self._normalize_index(
            await self._store.get_version_index(account_id, target_id, uri)
        )

    @staticmethod
    def _normalize_index(index: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Normalize legacy metadata in memory without changing persisted versions on read."""
        if index is not None:
            index.pop("source_roots", None)
            for version in index["versions"]:
                meta = {
                    key: version.pop(key, default)
                    for key, default in _VERSION_META_DEFAULTS.items()
                }
                version["meta"] = {**meta, **(version.get("meta") or {})}
        return index

    async def _indexes(self, account_id: str, target_id: str) -> List[Dict[str, Any]]:
        return [
            self._normalize_index(index)
            for index in await self._store.list_version_indexes(account_id, target_id)
        ]

    async def append(
        self,
        account_id: str,
        target_id: str,
        uri: str,
        snapshot_id: str,
        entry: Dict[str, Any],
        *,
        operation_id: Optional[str] = None,
        metadata_lease: Any = None,
    ) -> Dict[str, Any]:
        """Append a file version under its lock, reusing a version on operation retries.

        A whole-account restore supplies its covering lease for version metadata.
        Independent operations collect exact references before publishing completion.
        """
        context = _operation_outputs.get()
        collecting = (
            context and context["account_id"] == account_id and target_id in context["outputs"]
        )
        operation_id = operation_id or (context["operation_id"] if collecting else None)
        async with self._store.lock_version_index(
            account_id, target_id, uri, metadata_lease
        ) as token:
            index = await self.load(account_id, target_id, uri) or {
                "uri": uri,
                "next": 1,
                "versions": [],
            }
            version = next(
                (
                    v
                    for v in index["versions"]
                    if operation_id
                    and v["meta"].get("operation_id") == operation_id
                    and v["snapshot_id"] == snapshot_id
                    and v["snapshot_path"] == uri
                ),
                None,
            )
            if version is None:
                version = {
                    "version_id": f"v{index['next']}",
                    "snapshot_id": snapshot_id,
                    "snapshot_path": uri,
                    "created_at": time.time(),
                    **entry,
                    "meta": {**_VERSION_META_DEFAULTS, **entry.get("meta", {})},
                }
                if operation_id:
                    version["meta"]["operation_id"] = operation_id
                index["next"] += 1
                index["versions"] = (index["versions"] + [version])[-MAX_VERSIONS:]

            async def persist():
                await self._store.put_version_index(account_id, target_id, index, token)
                if collecting and operation_id == context["operation_id"]:
                    context["outputs"][target_id][uri] = {
                        "uri": uri,
                        "version_id": version["version_id"],
                        "snapshot_id": version["snapshot_id"],
                    }

            await run_to_completion(persist)
        return version

    # ---------- target resolution ----------

    async def owning_target(
        self, account_id: str, uri: str, *, metadata_lease: Any = None
    ) -> Optional[Dict[str, Any]]:
        """Return the active target containing ``uri``, including its first run."""
        for item in await self._store.list_summaries(account_id, metadata_lease):
            to = item.get("to") or ""
            if item.get("status") == "active" and (uri == to or uri.startswith(f"{to}/")):
                record = await self._store.get(account_id, item["id"])
                if record is not None:
                    return record
        return None

    async def record_operation(
        self,
        uris: List[str],
        ctx: RequestContext,
        operation_type: str,
        params: Dict[str, Any],
        operation: Callable[[], Awaitable[Any]],
        *,
        recursive: bool = False,
    ) -> Any:
        """Execute one call and persist its outcome once per managed target.

        Parameters contain references, never file bodies. Recursive restores include
        overlapping targets; per-target URI lists prevent exposing sibling directories.
        Exceptions and cancellation retain their original behavior after history is saved.
        """
        entries = []
        operation_id = f"op_{uuid4().hex}"
        for target in await self._store.list_summaries(ctx.account_id):
            to = target["to"]
            affected = [uri for uri in uris if uri == to or uri.startswith(f"{to}/")]
            if recursive and any(uri == "viking://" or overlaps(uri, to) for uri in uris):
                affected = affected or [to]
            if target.get("status") == "active" and affected:
                scoped_params = {
                    key: value for key, value in params.items() if key not in {"from", "to"}
                }
                for key in ("from", "to"):
                    value = params.get(key)
                    if isinstance(value, str) and uri_matches_prefix(value, to):
                        scoped_params[key] = value
                    elif isinstance(value, str) and recursive and uri_matches_prefix(to, value):
                        scoped_params[key] = to
                if operation_type in {"cp", "mv"} and "to" in params:
                    scoped_params["to"] = params["to"]
                entries.append(
                    (
                        target,
                        new_operation(
                            operation_id,
                            operation_type,
                            scoped_params | {"uris": affected},
                            ctx.user.user_id,
                        ),
                    )
                )
        if not entries:
            return await operation()
        for uri in uris:
            await self._fs._ensure_access(
                uri,
                ctx,
                action=AclAction.READ
                if recursive and operation_type == "restore"
                else AclAction.WRITE,
            )
        context = {
            "account_id": ctx.account_id,
            "operation_id": operation_id,
            "outputs": {target["id"]: {} for target, _ in entries},
            "targets": {target["id"]: target for target, _ in entries},
            "changed": set(),
            "batch": params.get("batch", False),
        }
        token = _operation_outputs.set(context)
        outcome = {}
        operation_error = None
        try:
            for target, entry in entries:
                async with self._store.edit(ctx.account_id, target["id"]) as record:
                    if record["status"] == "active" and record["deleted_at"] == target.get(
                        "deleted_at"
                    ):
                        await self._store.merge_outputs(
                            ctx.account_id, target["id"], operation_id, []
                        )
                await self._store.put_operation(ctx.account_id, target["id"], entry)
            result = await operation()
        except BaseException as exc:
            operation_error = exc
            outcome = {key: getattr(exc, key, []) for key in ("written_paths", "deleted_paths")}
            for _, entry in entries:
                entry["status"] = (
                    "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                )
                entry["error"] = (
                    type(exc).__name__
                    if len(entries) > 1 or operation_type in {"cp", "mv"}
                    else str(exc) or type(exc).__name__
                )
            raise
        else:
            outcome = result if isinstance(result, dict) else {}
            for _, entry in entries:
                entry["status"] = "completed"
            return result
        finally:
            _operation_outputs.reset(token)

            async def finish():
                for target, entry in entries:
                    entry["finished_at"] = time.time()
                    entry["result"] = {
                        key: outcome[key]
                        for key in ("result", "version_id", "written_bytes", "mode")
                        if key in outcome
                    }
                    for key in ("created", "updated", "unchanged", "conflicts"):
                        if isinstance(outcome.get(key), list):
                            entry["result"][key] = [
                                item
                                for item in outcome[key]
                                if (item.get("uri") if isinstance(item, dict) else item)
                                and uri_matches_prefix(
                                    item.get("uri") if isinstance(item, dict) else item,
                                    target["to"],
                                )
                            ]
                    if recursive:
                        for key in ("written_paths", "deleted_paths"):
                            entry["result"][key] = [
                                path
                                for path in outcome.get(key, [])
                                if (self._fs._tree_path_to_uri(path).startswith(f"{target['to']}/"))
                            ]
                    async with self._store.edit(ctx.account_id, target["id"]) as record:
                        current = record["status"] == "active" and record[
                            "deleted_at"
                        ] == target.get("deleted_at")
                        if current:
                            await self._store.merge_outputs(
                                ctx.account_id,
                                target["id"],
                                operation_id,
                                list(context["outputs"][target["id"]].values()),
                            )
                        if (
                            current
                            and recursive
                            and any(
                                is_versioned(path)
                                for key in ("written_paths", "deleted_paths")
                                for path in entry["result"][key]
                            )
                        ):
                            record["content_updated_at"] = time.time()
                    await self._store.put_operation(ctx.account_id, target["id"], entry)

            try:
                await run_to_completion(finish)
            except Exception as exc:
                for target, entry in entries:
                    if entry["status"] == "completed":
                        entry.update(
                            status="failed", error=f"Failed to persist operation outputs: {exc}"
                        )
                    entry["finished_at"] = time.time()
                    try:
                        await self._store.put_operation(ctx.account_id, target["id"], entry)
                    except Exception:
                        logger.exception("Failed to persist operation failure %s", operation_id)
                raise exc from operation_error

    async def record_effects(
        self, effects: List[dict], ctx: RequestContext, *, metadata_lease: Any = None
    ) -> None:
        """Publish confirmed outputs and path effects under the content lease.

        Unfinished Compile tasks retain scope snapshots and ordered operation IDs.
        File version references remain independent of directory baselines.
        """
        context = _operation_outputs.get()
        if context is None or context["account_id"] != ctx.account_id:
            return
        for target_id, original in context["targets"].items():
            record = await self._store.get(ctx.account_id, target_id)
            if (
                record is None
                or record["status"] != "active"
                or record["deleted_at"] != original.get("deleted_at")
            ):
                continue
            scoped = []
            for effect in effects:
                uri = effect["uri"].rstrip("/")
                scopes = {original["to"], record["to"]}
                if any(uri_matches_prefix(uri, scope) for scope in scopes):
                    scoped.append({**effect, "uri": uri})
                elif effect["kind"] in {"deleted", "snapshot"} and uri_matches_prefix(
                    record["to"], uri
                ):
                    scoped.append({**effect, "uri": record["to"]})
            outputs = list(context["outputs"][target_id].values())
            if not scoped and not outputs:
                continue

            async def persist(target_id=target_id, scoped=scoped, outputs=outputs, record=record):
                owner = (await self._write_meta(ctx.account_id, record)).get("task_id")
                if outputs or owner:
                    await self._store.merge_outputs(
                        ctx.account_id,
                        target_id,
                        context["operation_id"],
                        outputs,
                        effects=scoped if owner and scoped else None,
                        metadata_lease=metadata_lease,
                    )
                if owner:
                    await self._store.merge_outputs(
                        ctx.account_id,
                        target_id,
                        owner,
                        [],
                        ordinary_operation=context["operation_id"],
                        metadata_lease=metadata_lease,
                    )

            await run_to_completion(persist)

    async def guard_transfer(
        self,
        from_uri: str,
        to_uri: str,
        ctx: RequestContext,
        *,
        watched_uris: Optional[List[str]] = None,
    ) -> bool:
        """Reject Compile inputs or watched content transferred into a Compile target.

        Destination intersections select the effective source subtree, so sibling
        source branches do not block a transfer. Returns whether a target is touched.
        watched_uris contains this account's Watch roots, including paused tasks.
        """
        source_root, destination = from_uri.rstrip("/"), to_uri.rstrip("/")
        managed = False
        for target in await self._store.list_summaries(ctx.account_id):
            if target.get("status") != "active":
                continue
            to = target["to"]
            if not (overlaps(source_root, to) or overlaps(destination, to)):
                continue
            managed = True
            if not overlaps(destination, to):
                continue
            source = (
                source_root + to[len(destination) :]
                if uri_matches_prefix(to, destination)
                else source_root
            )
            if source != source_root and not await self._fs.exists(source, ctx=ctx):
                continue
            record = await self._store.get(ctx.account_id, target["id"])
            if record is None:
                continue
            sources = set(record["from"])
            for run in await self._store.list_operations(ctx.account_id, target["id"]):
                run_meta = run["meta"]
                if (
                    run_meta.get("task_id")
                    and run["finished_at"] is None
                    and run["status"] not in TERMINAL
                    and run_meta["target_deleted_at"] == record["deleted_at"]
                ):
                    sources.update(run.get("params", {}).get("from", []))
            if any(overlaps(source, root.rstrip("/")) for root in watched_uris or []):
                raise ConflictError(
                    "Watched resources cannot be transferred into a Compile target",
                    resource="COMPILE_TARGET",
                )
            if any(overlaps(source, root.rstrip("/")) for root in sources):
                raise InvalidArgumentError(
                    "Compile sources cannot be transferred into their target"
                )
        return managed

    async def register_transfer(
        self,
        from_uri: str,
        to_uri: str,
        ctx: RequestContext,
        *,
        lease_ref: Any,
        move: bool = False,
    ) -> None:
        """Snapshot final transfer scopes while the caller holds their content lease.

        Directory snapshots form one ordinary baseline, including merged contents.
        Moves retain the final source state and re-key indexes only after source removal.
        """
        context = _operation_outputs.get()
        if context is None or context["account_id"] != ctx.account_id:
            return
        source_root, destination = from_uri.rstrip("/"), to_uri.rstrip("/")
        if move and not await self._fs.exists(source_root, ctx=ctx):
            await self.after_move(source_root, destination, ctx)
        targets = []
        for target_id in context["targets"]:
            await self._store.mark_content_updated(ctx.account_id, target_id)
            record = await self._store.get(ctx.account_id, target_id)
            if record and (await self._write_meta(ctx.account_id, record)).get("task_id"):
                targets.append(record["to"])
        if not targets:
            return
        paths = [source_root, destination] if move else [destination]
        snapshot_paths = {
            target if uri_matches_prefix(target, uri) else uri
            for uri in paths
            for target in targets
            if overlaps(uri, target)
        }
        snapshot = await self._commit(
            sorted(snapshot_paths), "ordinary transfer", ctx, lease_ref=lease_ref
        )
        effects = []
        for uri in paths:
            if not await self._fs.exists(uri, ctx=ctx):
                effects.append({"uri": uri, "kind": "deleted"})
                continue
            stat = await self._fs.stat(uri, ctx=ctx, skip_count=True)
            if not stat.get("isDir", False) and not is_versioned(uri):
                continue
            effects.append(
                {
                    "uri": uri,
                    "kind": "snapshot",
                    "snapshot_id": snapshot,
                    "is_dir": stat.get("isDir", False),
                }
            )
        await self.record_effects(effects, ctx)

    async def _managed(self, uri: str, ctx: RequestContext) -> Dict[str, Any]:
        record = await self.owning_target(ctx.account_id, uri)
        if record is None or not is_versioned(uri):
            raise FailedPreconditionError(
                f"Not a Compile output file: {uri}", details={"reason": "NOT_COMPILE_MANAGED"}
            )
        return record

    async def _running(self, account_id: str, record: Dict[str, Any]) -> bool:
        owner = record["active_task_id"]
        if not owner:
            return False
        run = await self._store.get_operation(account_id, record["id"], owner)
        return run is not None and run["status"] not in TERMINAL

    # ---------- write hooks ----------

    async def record_compile(
        self,
        account_id: str,
        record: Dict[str, Any],
        run: Dict[str, Any],
        head: str,
        changes: List[Dict[str, str]],
        ctx: RequestContext,
    ) -> None:
        """Register final Compile versions under the caller's output tree lease.

        Ordinary versions remain immutable; full runs record all sources, while
        incremental runs extend the latest version's provenance with changed roots.
        """
        if not head:
            await self._store.merge_outputs(account_id, record["id"], run["operation_id"], [])
            return
        run_meta = run["meta"]
        indexes = {index["uri"]: index for index in await self._indexes(account_id, record["id"])}
        ordinary = {
            uri: version
            for uri, index in indexes.items()
            if (version := self._ordinary_version(index, run_meta["task_id"]))
        }
        changes = {change["uri"]: change["change_type"] for change in changes}
        full = (
            run_meta.get("execution", {}).get("mode") == "full"
            or run["params"]["args"]["mode"] == "full"
        )
        manifest = await self._store.get_outputs(account_id, record["id"], run["operation_id"])
        outputs = list((manifest or {}).get("outputs", [])) + [
            {
                "uri": version["snapshot_path"],
                "version_id": version["version_id"],
                "snapshot_id": version["snapshot_id"],
            }
            for index in indexes.values()
            for version in index["versions"]
            if version["source"] == "compile" and version["meta"]["task_id"] == run_meta["task_id"]
        ]
        recorded = {item["uri"] for item in outputs if item["snapshot_id"] is not None}
        try:
            # Ordinary edits can differ even when final output matches the start snapshot.
            for uri in changes.keys() | ordinary.keys():
                if not is_versioned(uri):
                    continue
                if uri in recorded:
                    continue
                index, version = indexes.get(uri), ordinary.get(uri)
                change_type = changes.get(uri, "modified")
                if version:
                    if version["meta"]["after_compile_snapshot"]:
                        continue
                    if change_type != "deleted":
                        try:
                            if not await self._changed(ctx, record["id"], uri, head):
                                continue
                        except (AGFSNotFoundError, NotFoundError):
                            change_type = "deleted"
                if change_type == "deleted":
                    if version is None:
                        await self._store.delete_version_index(account_id, record["id"], uri)
                    continue
                if index and any(
                    v["meta"]["task_id"] == run_meta["task_id"] and v["source"] == "compile"
                    for v in index["versions"]
                ):
                    continue
                if full or not (index or {}).get("versions"):
                    roots = list(run["params"]["from"])
                else:
                    previous_roots = (
                        index["versions"][-1].get("provenance", {}).get("source_roots", [])
                    )
                    roots = sorted(set(previous_roots) | set(run_meta["changed_roots"]))
                created = await self.append(
                    account_id,
                    record["id"],
                    uri,
                    head,
                    {
                        "source": "compile",
                        "actor": run["actor"],
                        "meta": {"task_id": run_meta["task_id"]},
                        "provenance": {
                            "trigger": run_meta["trigger"],
                            "skill": run["params"]["skill"],
                            "source_roots": roots,
                        },
                    },
                    operation_id=run["operation_id"],
                )
                outputs.append(
                    {
                        "uri": uri,
                        "version_id": created["version_id"],
                        "snapshot_id": created["snapshot_id"],
                    }
                )
        finally:
            error = sys.exc_info()[1]
            try:
                await run_to_completion(
                    lambda: self._store.merge_outputs(
                        account_id, record["id"], run["operation_id"], outputs
                    )
                )
            except BaseException:
                if error is None:
                    raise
                logger.exception("Failed to persist Compile outputs for %s", run["operation_id"])

    @staticmethod
    def _ordinary_version(index: Optional[Dict[str, Any]], task_id: str) -> Optional[dict]:
        """Return the run's latest ordinary version, or None; Compile entries are ignored."""
        for version in reversed((index or {}).get("versions", [])):
            if version["meta"]["task_id"] == task_id and version["source"] in {
                "manual",
                "agent",
                "restore",
            }:
                return version
        return None

    async def record_external(
        self,
        account_id: str,
        record: Dict[str, Any],
        target_base: Optional[str],
        ctx: RequestContext,
    ) -> None:
        """Version external changes since ``versions_head`` under the task owner's identity.

        Existing files start at ``v1``; files matching their latest version are skipped.
        """
        if not target_base:
            return
        changes = await self._fs.diff_paths(
            dirs=[record["to"]],
            base=record.get("versions_head"),
            head=target_base,
            ctx=ctx,
        )
        changed = False
        for change in changes:
            uri = change["uri"]
            if not is_versioned(uri):
                continue
            if change["change_type"] == "deleted":
                changed = changed or await self.load(account_id, record["id"], uri) is not None
                await self._store.delete_version_index(account_id, record["id"], uri)
            elif await self._changed(ctx, record["id"], uri, target_base):
                changed = True
                await self.append(
                    account_id,
                    record["id"],
                    uri,
                    target_base,
                    {"source": "external", "actor": None, "provenance": {}},
                )
        if changed and record.get("versions_head"):
            await self._store.mark_content_updated(account_id, record["id"])

    async def record_write(
        self,
        uris: List[str],
        ctx: RequestContext,
        edit_source: str,
        *,
        lease_ref: Optional[Dict[str, Any]] = None,
        write: Optional[Callable[[], Awaitable[Any]]] = None,
        record_version: bool = True,
    ) -> Any:
        """Write under the content lease and register ordinary versions with provenance.

        Unversioned writes still record actual content changes.
        Ordinary batches save each target's change time during batch finalization.
        Active Compile writes defer the directory timestamp to task finalization.
        Operation outputs select rollback content; after_compile_snapshot protects newer
        ordinary versions from a retry of the frozen finish snapshot.
        """
        grouped: Dict[str, List[str]] = {}
        previous = {}
        for uri in uris:
            record = await self.owning_target(ctx.account_id, uri)
            if record is None or not is_versioned(uri):
                continue
            grouped.setdefault(record["id"], []).append(uri)
            # Working bytes can differ from the latest version during Compile.
            if write is not None:
                previous[uri] = (
                    await self._fs.read_file_bytes(uri, ctx=ctx)
                    if await self._fs.exists(uri, ctx=ctx)
                    else None
                )
        written = await write() if write is not None else None
        changed = {}
        context = _operation_outputs.get()
        for target_id, targets in grouped.items():
            targets = [
                uri
                for uri in targets
                if write is None or previous[uri] != await self._fs.read_file_bytes(uri, ctx=ctx)
            ]
            if not targets:
                continue
            changed[target_id] = targets
            run = None
            if edit_source == "compile":
                record = await self._store.get(ctx.account_id, target_id)
                run = (
                    await self._store.get_operation(
                        ctx.account_id, target_id, record["active_task_id"]
                    )
                    if record["active_task_id"]
                    else None
                )
                if run and run["started_at"] is not None and run["finished_at"] is None:
                    await self._store.mark_content_changed(
                        ctx.account_id, target_id, run["operation_id"]
                    )
                    if run["meta"]["target_base"] is None:
                        await self._store.merge_outputs(
                            ctx.account_id,
                            target_id,
                            run["operation_id"],
                            [
                                {"uri": uri, "version_id": None, "snapshot_id": None}
                                for uri in targets
                            ],
                        )
                else:
                    run = None
            is_batch = (
                context is not None
                and context["account_id"] == ctx.account_id
                and context.get("batch", False)
                and target_id in context["outputs"]
            )
            if run is None and (
                (write is not None and not is_batch) or (write is None and is_batch)
            ):
                await self._store.mark_content_updated(ctx.account_id, target_id)
            if context and context["account_id"] == ctx.account_id:
                context["changed"].update(targets)
                if target_id in context["outputs"]:
                    for uri in targets:
                        context["outputs"][target_id][uri] = {
                            "uri": uri,
                            "version_id": None,
                            "snapshot_id": None,
                        }
        if not record_version:
            return written
        if not changed:
            return written
        try:
            snapshot = await self._commit(
                [uri for targets in changed.values() for uri in targets],
                f"{edit_source} edit",
                ctx,
                lease_ref=lease_ref,
            )
            if context and context["account_id"] == ctx.account_id:
                for target_id, targets in changed.items():
                    if target_id in context["outputs"]:
                        for uri in targets:
                            context["outputs"][target_id][uri]["snapshot_id"] = snapshot
            for target_id, targets in changed.items():
                record = await self._store.get(ctx.account_id, target_id)
                run_entry = await self._write_meta(ctx.account_id, record)
                for uri in targets:
                    if snapshot:
                        await self.append(
                            ctx.account_id,
                            target_id,
                            uri,
                            snapshot,
                            {
                                "source": edit_source,
                                "actor": ctx.user.user_id,
                                "provenance": {"source_roots": [uri]},
                                "meta": run_entry,
                            },
                        )
        finally:
            if edit_source != "compile":
                await self.record_effects([], ctx)
        return written

    async def _write_meta(self, account_id: str, record: dict) -> dict:
        """Associate ordinary versions and operations with an unfinished Compile."""
        run = (
            await self._store.get_operation(account_id, record["id"], record["active_task_id"])
            if record["active_task_id"]
            else None
        )
        if (
            run
            and run["started_at"] is not None
            and run["finished_at"] is None
            and not run["meta"].get("rollback_head")
        ):
            return {
                "task_id": run["meta"]["task_id"],
                **(
                    {"after_compile_snapshot": True}
                    if run["meta"].get("snapshot_committed")
                    else {}
                ),
            }
        return {}

    async def finish_batch(self, *, ctx: RequestContext, edit_source: str, lease_ref: Any) -> None:
        """Version the batch's changed files once, before its content lease is released."""
        context = _operation_outputs.get()
        if context and context["account_id"] == ctx.account_id and context["changed"]:
            await self.record_write(
                sorted(context["changed"]), ctx, edit_source, lease_ref=lease_ref
            )

    async def record_restored(
        self,
        result: dict,
        *,
        ctx: RequestContext,
        lease_ref: Any,
        metadata_lease: Any = None,
    ) -> None:
        """Version successful snapshot-restore writes under the writeback lease.

        Partial failures expose only their confirmed written paths. Their new commit
        supplies immutable bytes; deleted paths and unchanged files are not outputs.
        """
        context = _operation_outputs.get()
        if context is None or context["account_id"] != ctx.account_id:
            return
        snapshot = result.get("new_commit_oid")
        effects = [
            {"uri": self._fs._tree_path_to_uri(path), "kind": "deleted"}
            for path in result.get("deleted_paths", [])
        ]
        files = []
        for path in result.get("written_paths", []):
            uri = self._fs._tree_path_to_uri(path)
            record = await self.owning_target(ctx.account_id, uri, metadata_lease=metadata_lease)
            if record is not None and is_versioned(uri):
                if record["id"] in context["outputs"]:
                    context["outputs"][record["id"]][uri] = {
                        "uri": uri,
                        "version_id": None,
                        "snapshot_id": snapshot,
                    }
                files.append((record, uri))
        try:
            for record, uri in files:
                if not snapshot:
                    continue
                await self.append(
                    ctx.account_id,
                    record["id"],
                    uri,
                    snapshot,
                    {
                        "source": "restore",
                        "actor": ctx.user.user_id,
                        "provenance": {"source_roots": [uri]},
                        "meta": await self._write_meta(ctx.account_id, record),
                    },
                    metadata_lease=metadata_lease,
                )
        finally:
            await self.record_effects(effects, ctx, metadata_lease=metadata_lease)

    async def _changed(self, ctx: RequestContext, target_id: str, uri: str, snapshot: str) -> bool:
        """Compare blob IDs at their recorded paths using the caller's identity.

        Missing history counts as changed; other read errors propagate.
        """
        index = await self.load(ctx.account_id, target_id, uri)
        if index is None or not index["versions"]:
            return True
        latest = index["versions"][-1]
        try:
            before = await self._fs.show_blob_raw(
                latest["snapshot_id"], path=latest["snapshot_path"], ctx=ctx
            )
        except (AGFSNotFoundError, NotFoundError):
            return True
        after = await self._fs.show_blob_raw(snapshot, path=uri, ctx=ctx)
        return before["oid"] != after["oid"]

    async def move(self, account_id: str, from_uri: str, to_uri: str) -> None:
        """Re-key indexes moved within one target; entries keep their old snapshot_path."""
        source = await self.owning_target(account_id, from_uri)
        if source is None:
            return
        target = await self.owning_target(account_id, to_uri)
        for index in await self._indexes(account_id, source["id"]):
            uri = index["uri"]
            if uri != from_uri and not uri.startswith(f"{from_uri}/"):
                continue
            await self._store.delete_version_index(account_id, source["id"], uri)
            if target is not None and target["id"] == source["id"]:
                index["uri"] = to_uri + uri[len(from_uri) :]
                await self._store.put_version_index(account_id, source["id"], index)

    async def drop_all(self, account_id: str, target_id: str) -> None:
        await self._store.clear_output_data(account_id, target_id)

    # ---------- fs/rm and fs/mv ----------

    async def _targets_under(self, account_id: str, uri: str) -> List[Dict[str, Any]]:
        """Active targets equal to or inside ``uri`` (a removed/moved directory)."""
        return [
            item
            for item in await self._store.list_summaries(account_id)
            if item.get("status") == "active"
            and (item["to"] == uri or item["to"].startswith(f"{uri}/"))
        ]

    async def _reject_running(self, account_id: str, uri: str) -> None:
        for item in await self._targets_under(account_id, uri):
            record = await self._store.get(account_id, item["id"])
            if record is not None and await self._running(account_id, record):
                raise ConflictError(
                    f"Compile is running on {item['to']}", resource="COMPILE_RUNNING"
                )

    @asynccontextmanager
    async def before_remove(self, uri: str, ctx: RequestContext) -> AsyncIterator[Any]:
        """Hold a managed descendant's content lease through deletion and metadata updates."""
        uri = uri.rstrip("/")
        await self._reject_running(ctx.account_id, uri)
        record = await self.owning_target(ctx.account_id, uri)
        if record is None or record["to"] == uri:
            yield None
            return
        lock_uri = uri
        while lock_uri != "viking://" and not await self._fs.exists(lock_uri, ctx=ctx):
            parts = lock_uri.removeprefix("viking://").split("/")
            lock_uri = "viking://" + "/".join(parts[:-1])
        try:
            lease = await self._fs._async_agfs.pathlock_acquire_tree(
                self._fs._uri_to_path(lock_uri, ctx=ctx)
            )
        except LockAcquisitionError as exc:
            raise ResourceBusyError("Resource is being processed", uri=uri) from exc
        try:
            yield lease
        finally:
            await self._fs._async_agfs.pathlock_release(lease)

    async def after_remove(self, uri: str, ctx: RequestContext) -> None:
        """Whole targets become ``deleted`` (runs kept); a single file loses its index."""
        uri = uri.rstrip("/")
        removed = await self._targets_under(ctx.account_id, uri)
        for item in removed:
            async with self._store.edit(ctx.account_id, item["id"]) as record:
                record["status"] = "deleted"
                record["deleted_at"] = time.time()
                record["auto_update"] = False
                record["active_task_id"] = None
                record["execution_context"]["credential"] = None
                await self.drop_all(ctx.account_id, item["id"])
            if self.reschedule is not None:
                await self.reschedule(ctx.account_id, record)
        if not removed:
            record = await self.owning_target(ctx.account_id, uri)
            if record is not None:
                for index in await self._indexes(ctx.account_id, record["id"]):
                    if index["uri"] == uri or index["uri"].startswith(f"{uri}/"):
                        await self._store.delete_version_index(
                            ctx.account_id, record["id"], index["uri"]
                        )
                if is_versioned(uri):
                    async with self._store.edit(ctx.account_id, record["id"]) as record:
                        record["content_updated_at"] = time.time()

    async def before_move(self, from_uri: str, to_uri: str, ctx: RequestContext) -> None:
        await self._reject_running(ctx.account_id, from_uri)
        for item in await self._targets_under(ctx.account_id, from_uri):
            record = await self._store.get(ctx.account_id, item["id"])
            moved_to = to_uri + item["to"][len(from_uri) :]
            for other in await self._store.list_summaries(ctx.account_id):
                if (
                    other.get("status") == "active"
                    and other["id"] != item["id"]
                    and overlaps(other["to"], moved_to)
                ):
                    raise InvalidArgumentError(f"Compile target would nest {other['to']}")
            if any(overlaps(source, moved_to) for source in record["from"]):
                raise InvalidArgumentError("Compile target would overlap its sources")

    async def after_move(self, from_uri: str, to_uri: str, ctx: RequestContext) -> None:
        """Rewrite moved targets' URIs (IDs stay); otherwise re-key moved file indexes."""
        if from_uri == to_uri:
            return
        moved = await self._targets_under(ctx.account_id, from_uri)
        for item in moved:
            async with self._store.edit(ctx.account_id, item["id"]) as record:
                record["to"] = to_uri + record["to"][len(from_uri) :]
                record["content_updated_at"] = time.time()
            for index in await self._indexes(ctx.account_id, item["id"]):
                await self._store.delete_version_index(ctx.account_id, item["id"], index["uri"])
                index["uri"] = record["to"] + index["uri"][len(item["to"]) :]
                await self._store.put_version_index(ctx.account_id, item["id"], index)
        if not moved:
            await self.move(ctx.account_id, from_uri, to_uri)
            for uri in {from_uri, to_uri}:
                record = await self.owning_target(ctx.account_id, uri)
                if record is not None and is_versioned(uri):
                    await self._store.mark_content_updated(ctx.account_id, record["id"])

    # ---------- queries and single-file restore ----------

    async def versions(
        self, uri: str, ctx: RequestContext, *, limit: int, before: Optional[str]
    ) -> Dict[str, Any]:
        """Return newest-first versions and the retained count before pagination."""
        await self._fs._ensure_access(uri, ctx)
        record = await self._managed(uri, ctx)
        index = await self.load(ctx.account_id, record["id"], uri)
        entries = list(reversed((index or {}).get("versions", [])))
        total = len(entries)
        current = entries[0]["version_id"] if entries else None
        if before:
            ids = [entry["version_id"] for entry in entries]
            entries = entries[ids.index(before) + 1 :] if before in ids else []
        page = entries[:limit]
        return {
            "uri": uri,
            "current_version": current,
            "total": total,
            "versions": page,
            "next_before_version": page[-1]["version_id"] if len(entries) > limit else None,
        }

    async def restore(
        self,
        uri: str,
        restore_version: str,
        current_version: str,
        ctx: RequestContext,
        write: Callable[[bytes, Callable[..., Awaitable[Any]]], Awaitable[Any]],
    ) -> Dict[str, Any]:
        """Restore ``uri`` from ``restore_version`` under the content lock.

        ``current_version`` must match; active Compile or mismatch raises ConflictError.
        ``write`` persists and refreshes as ``ctx``; return new version metadata or noop.
        """
        await self._fs._ensure_access(uri, ctx, action=AclAction.WRITE)
        record = await self._managed(uri, ctx)
        index = await self.load(ctx.account_id, record["id"], uri) or {"versions": []}
        source = next((v for v in index["versions"] if v["version_id"] == restore_version), None)
        if source is None:
            raise NotFoundError(restore_version, "file version")
        content = await self._fs.show(source["snapshot_id"], path=source["snapshot_path"], ctx=ctx)
        result: Dict[str, Any] = {}

        async def commit_restore(uris, *, lease_ref, write):
            """Check, write and version under the content lease before refresh."""
            async with self._store.lock_version_index(ctx.account_id, target_id, uri):
                record = await self._store.get(ctx.account_id, target_id)
                if await self._running(ctx.account_id, record):
                    raise ConflictError("Compile is running", resource="COMPILE_RUNNING")
                index = await self.load(ctx.account_id, target_id, uri) or {"versions": []}
                current = index["versions"][-1]["version_id"] if index["versions"] else None
                if current != current_version:
                    raise ConflictError(
                        f"Current version is {current}, not {current_version}",
                        resource="VERSION_CONFLICT",
                    )
                written = await self.record_write(
                    uris, ctx, "restore", write=write, record_version=False
                )
                snapshot = await self._commit(
                    uris, f"restore {uri} to {restore_version}", ctx, lease_ref=lease_ref
                )
            if not snapshot or not await self._changed(ctx, target_id, uri, snapshot):
                result.update(
                    uri=uri, version_id=current, meta=dict(_VERSION_META_DEFAULTS), result="noop"
                )
            else:
                version = await self.append(
                    ctx.account_id,
                    target_id,
                    uri,
                    snapshot,
                    {
                        "source": "restore",
                        "actor": ctx.user.user_id,
                        "meta": {"restored_from": restore_version},
                        "provenance": dict(source.get("provenance") or {}),
                    },
                )
                result.update(uri=uri, version_id=version["version_id"], meta=dict(version["meta"]))
            return written

        target_id = record["id"]

        async def restore_content():
            await write(content, commit_restore)
            return result

        return await self.record_operation(
            [uri],
            ctx,
            "restore",
            {"restore_version": restore_version, "current_version": current_version},
            restore_content,
        )

    async def _commit(
        self,
        uris: List[str],
        message: str,
        ctx: RequestContext,
        *,
        lease_ref: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        return await commit_snapshot(
            self._fs, uris, message, task_ctx(ctx.account_id, ctx.user.user_id), lease_ref=lease_ref
        )
