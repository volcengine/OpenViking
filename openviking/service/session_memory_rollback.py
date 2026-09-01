# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Conflict-safe reversal of memory changes recorded by Session commits."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from openviking.core.namespace import canonical_session_uri, classify_uri, is_accessible
from openviking.server.identity import RequestContext
from openviking.service.task_tracker import get_task_tracker
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import (
    ConflictError,
    FailedPreconditionError,
    InvalidArgumentError,
    NotFoundError,
)
from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_LOCK_TIMEOUT_SECONDS = 10.0


class SessionRollbackConflictError(ConflictError):
    """A rollback cannot safely proceed without skipping conflicting changes."""

    def __init__(self, session_id: str, result: dict[str, Any]):
        super().__init__(
            f"Session '{session_id}' memory rollback has conflicts; rerun with force=true "
            "to skip conflicting URIs",
            resource=session_id,
        )
        self.details["rollback"] = result


@dataclass(frozen=True)
class _State:
    exists: bool
    raw: str = ""


def _missing() -> _State:
    return _State(False, "")


def _present(raw: str) -> _State:
    return _State(True, raw)


def _state_matches(actual: _State, expected: _State) -> bool:
    if actual.exists != expected.exists:
        return False
    if not actual.exists:
        return True
    return _canonical_memory_raw(actual.raw) == _canonical_memory_raw(expected.raw)


def _canonical_memory_raw(raw: str) -> str:
    """Ignore harmless serializer differences while retaining all persisted fields."""
    try:
        return MemoryFileUtils.write(MemoryFileUtils.read(raw), render_links=False)
    except Exception:
        return raw


def _ordered_operations(diff: dict[str, Any]) -> list[dict[str, Any]]:
    ordered = diff.get("operation_order")
    if isinstance(ordered, list):
        return [item for item in ordered if isinstance(item, dict)]

    operations = diff.get("operations")
    if not isinstance(operations, dict):
        return []
    return [
        *[{"kind": "add", **item} for item in operations.get("adds", []) if isinstance(item, dict)],
        *[
            {"kind": "update", **item}
            for item in operations.get("updates", [])
            if isinstance(item, dict)
        ],
        *[
            {"kind": "delete", **item}
            for item in operations.get("deletes", [])
            if isinstance(item, dict)
        ],
    ]


def _validate_diff_structure(diff: dict[str, Any]) -> None:
    operations = diff.get("operations")
    if not isinstance(operations, dict):
        raise ValueError("operations must be an object")

    grouped: list[tuple[str, dict[str, Any]]] = []
    for group, kind in (("adds", "add"), ("updates", "update"), ("deletes", "delete")):
        values = operations.get(group)
        if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
            raise ValueError(f"operations.{group} must be an array of objects")
        grouped.extend((kind, item) for item in values)

    ordered = diff.get("operation_order")
    if ordered is None:
        if int(diff.get("schema_version") or 1) >= 2 and grouped:
            raise ValueError("schema version 2 requires operation_order")
        return
    if not isinstance(ordered, list) or any(not isinstance(item, dict) for item in ordered):
        raise ValueError("operation_order must be an array of objects")

    grouped_keys = Counter((kind, str(item.get("uri") or "")) for kind, item in grouped)
    ordered_keys = Counter(
        (str(item.get("kind") or ""), str(item.get("uri") or "")) for item in ordered
    )
    if grouped_keys != ordered_keys:
        raise ValueError("operation_order does not match grouped operations")


def _inverse_states(operation: dict[str, Any]) -> tuple[_State, _State, str] | None:
    kind = operation.get("kind")
    if kind == "add" and isinstance(operation.get("after_raw"), str):
        return _present(operation["after_raw"]), _missing(), "delete"
    if (
        kind == "update"
        and isinstance(operation.get("after_raw"), str)
        and isinstance(operation.get("before_raw"), str)
    ):
        return _present(operation["after_raw"]), _present(operation["before_raw"]), "write"
    if kind == "delete" and isinstance(operation.get("deleted_raw"), str):
        return _missing(), _present(operation["deleted_raw"]), "write"
    return None


def _is_memory_file_uri(uri: str, ctx: RequestContext) -> bool:
    try:
        classification = classify_uri(uri)
    except (TypeError, ValueError):
        return False
    return (
        is_accessible(uri, ctx)
        and classification.is_memory
        and classification.content_index is not None
        and len(classification.parts) > classification.content_index + 1
        and not uri.endswith("/.overview.md")
        and not uri.endswith("/.abstract.md")
    )


def _operation_conflict(
    *,
    archive_id: str,
    operation: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "archive_id": archive_id,
        "kind": str(operation.get("kind") or "unknown"),
        "uri": str(operation.get("uri") or ""),
        "action": "none",
        "status": "conflict",
        "reason": reason,
    }


def build_rollback_plan(
    archive_diffs: Iterable[tuple[str, dict[str, Any]]],
    current_states: dict[str, _State],
    *,
    force: bool,
) -> dict[str, Any]:
    """Build an inverse plan, simulating changes newest-to-oldest without mutating storage."""
    virtual = dict(current_states)
    items: list[dict[str, Any]] = []
    blocked_uris: set[str] = set()

    for archive_id, diff in archive_diffs:
        for operation in reversed(_ordered_operations(diff)):
            uri = str(operation.get("uri") or "")
            if not uri:
                items.append(
                    _operation_conflict(
                        archive_id=archive_id,
                        operation=operation,
                        reason="invalid_uri",
                    )
                )
                continue
            if uri in blocked_uris:
                item = _operation_conflict(
                    archive_id=archive_id,
                    operation=operation,
                    reason="blocked_by_newer_conflict",
                )
                item["status"] = "skipped" if force else "conflict"
                items.append(item)
                continue

            inverse = _inverse_states(operation)
            if inverse is None:
                blocked_uris.add(uri)
                item = _operation_conflict(
                    archive_id=archive_id,
                    operation=operation,
                    reason="legacy_snapshot_incomplete",
                )
                item["status"] = "skipped" if force else "conflict"
                items.append(item)
                continue

            expected, resulting, action = inverse
            actual = virtual.get(uri, _missing())
            if not _state_matches(actual, expected):
                blocked_uris.add(uri)
                item = _operation_conflict(
                    archive_id=archive_id,
                    operation=operation,
                    reason="current_content_changed",
                )
                item["status"] = "skipped" if force else "conflict"
                items.append(item)
                continue

            items.append(
                {
                    "archive_id": archive_id,
                    "kind": operation["kind"],
                    "memory_type": str(operation.get("memory_type") or "unknown"),
                    "uri": uri,
                    "action": action,
                    "status": "planned",
                    "content": resulting.raw if resulting.exists else None,
                }
            )
            virtual[uri] = resulting

    conflicts = sum(item["status"] == "conflict" for item in items)
    skipped = sum(item["status"] == "skipped" for item in items)
    planned = sum(item["status"] == "planned" for item in items)
    return {
        "operations": items,
        "summary": {"planned": planned, "conflicts": conflicts, "skipped": skipped},
    }


class SessionMemoryRollback:
    """Plan and execute one Session's memory rollback under storage path locks."""

    def __init__(self, *, viking_fs: VikingFS, vikingdb: Any = None):
        self._viking_fs = viking_fs
        self._memory_updater = MemoryUpdater(
            registry=MemoryTypeRegistry(),
            vikingdb=vikingdb,
        )

    async def run(
        self,
        session_id: str,
        ctx: RequestContext,
        *,
        dry_run: bool,
        force: bool,
        delete_session: bool,
    ) -> dict[str, Any]:
        session_uri = canonical_session_uri(ctx, session_id)
        session_path = self._viking_fs._uri_to_path(session_uri, ctx=ctx)
        session_lease = await self._viking_fs._async_agfs.pathlock_acquire_tree(
            session_path,
            timeout_secs=_LOCK_TIMEOUT_SECONDS,
        )
        try:
            # Close the service-layer check/acquire race. A commit that starts
            # after this check cannot finish Phase 1 while the tree lock is held.
            if await get_task_tracker().has_running(
                "session_commit",
                session_id,
                account_id=ctx.account_id,
                user_id=ctx.user.user_id,
            ):
                raise FailedPreconditionError(
                    f"Session '{session_id}' has a commit in progress",
                    details={"session_id": session_id, "reason": "commit_in_progress"},
                )
            archive_diffs = await self._load_archive_diffs(session_uri, ctx)
            uris = self._validate_and_collect_uris(archive_diffs, ctx)
            memory_lease = None
            if uris:
                paths = sorted(self._viking_fs._uri_to_path(uri, ctx=ctx) for uri in uris)
                memory_lease = await self._viking_fs._async_agfs.pathlock_acquire_exact_batch(
                    paths,
                    timeout_secs=_LOCK_TIMEOUT_SECONDS,
                    owner_lease_ref=session_lease,
                )
            try:
                current_states = await self._read_current_states(uris, ctx)
                plan = build_rollback_plan(archive_diffs, current_states, force=force)
                result = self._result(
                    session_id=session_id,
                    archive_count=len(archive_diffs),
                    plan=plan,
                    dry_run=dry_run,
                    force=force,
                    delete_session=delete_session,
                )
                if plan["summary"]["conflicts"]:
                    raise SessionRollbackConflictError(session_id, result)
                if dry_run:
                    return result

                original_states = dict(current_states)
                try:
                    await self._apply(plan["operations"], ctx, memory_lease)
                    if delete_session:
                        await self._viking_fs.rm(
                            session_uri,
                            recursive=True,
                            ctx=ctx,
                            lease_ref=session_lease,
                        )
                except BaseException:
                    await self._restore_original_states(original_states, ctx, memory_lease)
                    raise

                for item in plan["operations"]:
                    if item["status"] == "planned":
                        item["status"] = "applied"
                for public_item, planned_item in zip(
                    result["operations"], plan["operations"], strict=True
                ):
                    public_item["status"] = planned_item["status"]
                result["status"] = "partial" if plan["summary"]["skipped"] else "completed"
                result["summary"]["applied"] = plan["summary"]["planned"]
                result["session_deleted"] = delete_session
            finally:
                if memory_lease is not None:
                    await self._viking_fs._async_agfs.pathlock_release(memory_lease)
        finally:
            await self._viking_fs._async_agfs.pathlock_release(session_lease)

        result["refresh"] = await self._refresh(result["operations"], ctx)
        logger.info(
            "Rolled back session memories: session_id=%s status=%s summary=%s",
            session_id,
            result["status"],
            result["summary"],
        )
        return result

    async def _load_archive_diffs(
        self, session_uri: str, ctx: RequestContext
    ) -> list[tuple[str, dict[str, Any]]]:
        history_uri = f"{session_uri}/history"
        try:
            entries = await self._viking_fs.ls(history_uri, ctx=ctx)
        except NotFoundError:
            return []

        archives: list[tuple[int, str, dict[str, Any]]] = []
        for entry in entries:
            name = entry.get("name") if isinstance(entry, dict) else str(entry)
            if not name or not name.startswith("archive_"):
                continue
            try:
                index = int(name.removeprefix("archive_"))
            except ValueError:
                continue
            diff_uri = f"{history_uri}/{name}/memory_diff.json"
            try:
                raw = await self._viking_fs.read_file(diff_uri, ctx=ctx)
                diff = json.loads(raw)
            except NotFoundError as exc:
                raise FailedPreconditionError(
                    f"Archive '{name}' has no memory_diff.json; rollback safety cannot be proven",
                    details={"archive_id": name, "reason": "memory_diff_missing"},
                ) from exc
            except (TypeError, ValueError) as exc:
                raise FailedPreconditionError(
                    f"Archive '{name}' has an invalid memory_diff.json",
                    details={"archive_id": name, "reason": "memory_diff_invalid"},
                ) from exc
            if not isinstance(diff, dict):
                raise FailedPreconditionError(
                    f"Archive '{name}' has an invalid memory_diff.json",
                    details={"archive_id": name, "reason": "memory_diff_invalid"},
                )
            try:
                _validate_diff_structure(diff)
            except (TypeError, ValueError) as exc:
                raise FailedPreconditionError(
                    f"Archive '{name}' has an inconsistent memory_diff.json",
                    details={"archive_id": name, "reason": "memory_diff_inconsistent"},
                ) from exc
            archives.append((index, name, diff))
        archives.sort(key=lambda item: item[0], reverse=True)
        return [(name, diff) for _, name, diff in archives]

    @staticmethod
    def _validate_and_collect_uris(
        archive_diffs: Iterable[tuple[str, dict[str, Any]]], ctx: RequestContext
    ) -> set[str]:
        uris: set[str] = set()
        for archive_id, diff in archive_diffs:
            for operation in _ordered_operations(diff):
                uri = operation.get("uri")
                if not isinstance(uri, str) or not _is_memory_file_uri(uri, ctx):
                    raise InvalidArgumentError(
                        "memory_diff.json contains a URI outside the caller's memory namespace",
                        details={"archive_id": archive_id, "uri": uri},
                    )
                uris.add(uri)
        return uris

    async def _read_current_states(
        self, uris: Iterable[str], ctx: RequestContext
    ) -> dict[str, _State]:
        states: dict[str, _State] = {}
        for uri in uris:
            try:
                states[uri] = _present(await self._viking_fs.read_file(uri, ctx=ctx))
            except NotFoundError:
                states[uri] = _missing()
        return states

    async def _apply(
        self, operations: list[dict[str, Any]], ctx: RequestContext, lease_ref: Any
    ) -> None:
        for item in operations:
            if item["status"] != "planned":
                continue
            if item["action"] == "delete":
                await self._viking_fs.rm(item["uri"], recursive=False, ctx=ctx, lease_ref=lease_ref)
            else:
                await self._viking_fs.write_file(
                    item["uri"], item["content"], ctx=ctx, lease_ref=lease_ref
                )

    async def _restore_original_states(
        self, states: dict[str, _State], ctx: RequestContext, lease_ref: Any
    ) -> None:
        for uri, state in states.items():
            try:
                if state.exists:
                    await self._viking_fs.write_file(uri, state.raw, ctx=ctx, lease_ref=lease_ref)
                else:
                    await self._viking_fs.rm(uri, recursive=False, ctx=ctx, lease_ref=lease_ref)
            except Exception:
                logger.exception("Failed to compensate Session memory rollback for %s", uri)

    async def _refresh(
        self, operations: list[dict[str, Any]], ctx: RequestContext
    ) -> dict[str, Any]:
        applied = [item for item in operations if item["status"] == "applied"]
        restored = [item for item in applied if item["action"] == "write"]
        deleted = [item for item in applied if item["action"] == "delete"]
        return await self._memory_updater.refresh_after_rollback(
            restored_uris=[item["uri"] for item in restored],
            deleted_uris=[item["uri"] for item in deleted],
            uri_memory_type_map={item["uri"]: item["memory_type"] for item in applied},
            ctx=ctx,
        )

    @staticmethod
    def _result(
        *,
        session_id: str,
        archive_count: int,
        plan: dict[str, Any],
        dry_run: bool,
        force: bool,
        delete_session: bool,
    ) -> dict[str, Any]:
        public_operations = []
        for item in plan["operations"]:
            public_operations.append(
                {key: value for key, value in item.items() if key != "content"}
            )
        return {
            "session_id": session_id,
            "status": "ready" if dry_run else "planned",
            "dry_run": dry_run,
            "force": force,
            "delete_session": delete_session,
            "session_deleted": False,
            "archives_scanned": archive_count,
            "operations": public_operations,
            "summary": {**plan["summary"], "applied": 0},
            "planned_at": datetime.now(timezone.utc).isoformat(),
        }
