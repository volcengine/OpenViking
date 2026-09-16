# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Legacy session data migration to user-owned namespaces."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from openviking.pyagfs import AsyncAGFSClient
from openviking.server.identity import RequestContext, Role
from openviking.storage.vector_migration import (
    VectorMigrationResult,
    delete_vector_records,
)
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import FailedPreconditionError
from openviking_cli.session.user_id import UserIdentifier, validate_user_id


@dataclass(frozen=True)
class TreeCopy:
    source_path: str
    target_path: str
    category: str


@dataclass(frozen=True)
class LegacyCleanupTarget:
    account_id: str
    source_path: str
    source_uri: str
    category: str


@dataclass
class MigrationPlan:
    account_users: dict[str, set[str]] = field(default_factory=dict)
    created_users: set[tuple[str, str]] = field(default_factory=set)
    operations: list[TreeCopy] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_preflight_result(self) -> dict[str, Any]:
        return {
            "errors": list(self.errors),
            "created_users": [
                {"account_id": account_id, "user_id": user_id}
                for account_id, user_id in sorted(self.created_users)
            ],
            "operation_count": len(self.operations),
        }


@dataclass
class LegacyCleanupPlan:
    targets: list[LegacyCleanupTarget] = field(default_factory=list)


@dataclass
class MigrationResult:
    files: int = 0
    directories: int = 0
    operations: dict[str, int] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    created_users: list[dict[str, Any]] = field(default_factory=list)

    def mark_operation(self, category: str) -> None:
        self.operations[category] = self.operations.get(category, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "migrated": {
                "files": self.files,
                "directories": self.directories,
                "operations": dict(sorted(self.operations.items())),
            },
            "skipped": self.skipped,
            "created_users": self.created_users,
        }


@dataclass
class LegacyCleanupResult:
    vector_records: int = 0
    removed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cleanup": {
                "directories": len(self.removed),
                "vector_records": self.vector_records,
                "targets": self.removed,
            },
            "skipped": self.skipped,
            "warnings": self.warnings,
        }


def target_to_dict(target: LegacyCleanupTarget) -> dict[str, str]:
    return {
        "account_id": target.account_id,
        "type": target.category,
        "source": target.source_uri,
    }


class LegacyDataMigration:
    """Plan and execute legacy session migration."""

    def __init__(self, *, viking_fs: VikingFS, api_key_manager: Any, service: Any):
        if api_key_manager is None:
            raise FailedPreconditionError("Admin migration requires an API key user registry.")
        self._viking_fs = viking_fs
        self._agfs = AsyncAGFSClient(viking_fs.agfs)
        self._api_key_manager = api_key_manager
        self._service = service

    async def preflight(self) -> MigrationPlan:
        plan = MigrationPlan()
        registry_accounts = self._registry_account_ids()
        physical_accounts = await self._physical_account_ids()
        for account_id in sorted(physical_accounts - registry_accounts):
            if await self._exists(f"/local/{account_id}/session"):
                plan.errors.append(
                    {
                        "account_id": account_id,
                        "reason": (
                            "legacy data exists under an account missing from the user registry"
                        ),
                    }
                )

        for account_id in sorted(registry_accounts):
            plan.account_users[account_id] = set()
            for user_id in sorted(self._registry_user_ids(account_id)):
                self._ensure_plan_user(account_id, user_id, plan)
            if await self._exists(f"/local/{account_id}/session"):
                for user_id in sorted(await self._physical_user_ids(account_id)):
                    self._ensure_plan_user(account_id, user_id, plan)
            await self._plan_sessions(account_id, plan)
        return plan

    async def run(self) -> dict[str, Any]:
        plan = await self.preflight()
        if plan.errors:
            raise FailedPreconditionError(
                "Legacy migration preflight failed.",
                details=plan.to_preflight_result(),
            )

        result = MigrationResult()
        for account_id, user_id in sorted(plan.created_users):
            if self._has_user(account_id, user_id):
                continue
            await self._api_key_manager.register_user(account_id, user_id, "user")
            user_ctx = RequestContext(user=UserIdentifier(account_id, user_id), role=Role.USER)
            await self._service.initialize_user_directories(user_ctx)
            result.created_users.append({"account_id": account_id, "user_id": user_id})

        for operation in plan.operations:
            if await self._copy_path(operation.source_path, operation.target_path, result):
                result.mark_operation(operation.category)
        return result.to_dict()

    async def cleanup_preflight(self) -> LegacyCleanupPlan:
        plan = LegacyCleanupPlan()
        for account_id in sorted(await self._physical_account_ids()):
            session_path = f"/local/{account_id}/session"
            if await self._exists(session_path):
                plan.targets.append(self._cleanup_target(account_id, session_path, "session"))

        return plan

    async def cleanup(self) -> dict[str, Any]:
        plan = await self.cleanup_preflight()
        result = LegacyCleanupResult()
        for target in plan.targets:
            if not await self._exists(target.source_path):
                skipped = target_to_dict(target)
                skipped["reason"] = "missing"
                result.skipped.append(skipped)
                continue
            vector_result = await self._delete_vectors(target)
            result.vector_records += vector_result.deleted
            result.warnings.extend(vector_result.warnings)
            if vector_result.failed:
                skipped = target_to_dict(target)
                skipped["reason"] = "vector cleanup failed"
                result.skipped.append(skipped)
                continue
            await self._agfs.rm(target.source_path, recursive=True)
            result.removed.append(target_to_dict(target))
        return result.to_dict()

    def _registry_account_ids(self) -> set[str]:
        return {
            str(item.get("account_id", ""))
            for item in self._api_key_manager.get_accounts()
            if item.get("account_id")
        }

    def _registry_user_ids(self, account_id: str) -> set[str]:
        users = self._api_key_manager.get_users(
            account_id,
            limit=1_000_000,
            expose_key=False,
        )
        return {str(item.get("user_id", "")) for item in users if item.get("user_id")}

    def _has_user(self, account_id: str, user_id: str) -> bool:
        has_user = getattr(self._api_key_manager, "has_user", None)
        if callable(has_user):
            return bool(has_user(account_id, user_id))
        return user_id in self._registry_user_ids(account_id)

    async def _physical_account_ids(self) -> set[str]:
        entries = await self._ls("/local")
        return {
            entry["name"] for entry in entries if entry["is_dir"] and entry["name"] != "_system"
        }

    async def _physical_user_ids(self, account_id: str) -> set[str]:
        entries = await self._ls(f"/local/{account_id}/user")
        return {entry["name"] for entry in entries if entry["is_dir"]}

    async def _plan_sessions(self, account_id: str, plan: MigrationPlan) -> None:
        session_root = f"/local/{account_id}/session"
        for entry in await self._ls(session_root):
            if not entry["is_dir"]:
                continue
            source_path = f"{session_root}/{entry['name']}"
            if await self._looks_like_session_dir(source_path):
                await self._plan_one_session(
                    account_id,
                    session_id=entry["name"],
                    source_path=source_path,
                    owner_hint=None,
                    plan=plan,
                )
                continue
            for nested in await self._ls(source_path):
                if not nested["is_dir"]:
                    continue
                nested_path = f"{source_path}/{nested['name']}"
                if await self._looks_like_session_dir(nested_path):
                    await self._plan_one_session(
                        account_id,
                        session_id=nested["name"],
                        source_path=nested_path,
                        owner_hint=entry["name"],
                        plan=plan,
                    )

    async def _plan_one_session(
        self,
        account_id: str,
        *,
        session_id: str,
        source_path: str,
        owner_hint: str | None,
        plan: MigrationPlan,
    ) -> None:
        owner = await self._session_owner(source_path)
        if not owner and owner_hint:
            owner = owner_hint
        users = plan.account_users.setdefault(account_id, set())
        if not owner and len(users) == 1:
            owner = next(iter(users))
        if not owner:
            plan.errors.append(
                {
                    "account_id": account_id,
                    "session_id": session_id,
                    "source": self._path_to_uri(account_id, source_path),
                    "reason": "session owner cannot be inferred in a multi-user account",
                }
            )
            return
        if validate_user_id(owner):
            plan.errors.append(
                {
                    "account_id": account_id,
                    "session_id": session_id,
                    "user_id": owner,
                    "reason": "session owner user_id is invalid",
                }
            )
            return
        self._ensure_plan_user(account_id, owner, plan)
        target_path = f"/local/{account_id}/user/{owner}/sessions/{session_id}"
        plan.operations.append(
            TreeCopy(
                source_path=source_path,
                target_path=target_path,
                category="sessions",
            )
        )

    async def _session_owner(self, source_path: str) -> str:
        data = await self._read_json_file(f"{source_path}/.meta.json")
        if not isinstance(data, dict):
            return ""
        for key in (
            "created_by_user_id",
            "user_id",
            "owner_user_id",
            "created_by",
        ):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    async def _looks_like_session_dir(self, path: str) -> bool:
        for leaf in (".meta.json", "messages.jsonl", "history", "tool-results", "tools"):
            if await self._exists(f"{path}/{leaf}"):
                return True
        return False

    def _ensure_plan_user(self, account_id: str, user_id: str, plan: MigrationPlan) -> bool:
        if error := validate_user_id(user_id):
            detail = {
                "account_id": account_id,
                "user_id": user_id,
                "reason": f"user_id is invalid: {error}",
            }
            if detail not in plan.errors:
                plan.errors.append(detail)
            return False
        users = plan.account_users.setdefault(account_id, set())
        if user_id in users:
            return True
        users.add(user_id)
        if not self._has_user(account_id, user_id):
            plan.created_users.add((account_id, user_id))
        return True

    def _cleanup_target(
        self,
        account_id: str,
        source_path: str,
        category: str,
    ) -> LegacyCleanupTarget:
        return LegacyCleanupTarget(
            account_id=account_id,
            source_path=source_path,
            source_uri=self._path_to_uri(account_id, source_path),
            category=category,
        )

    async def _copy_path(
        self,
        source_path: str,
        target_path: str,
        result: MigrationResult,
    ) -> bool:
        stat = await self._stat(source_path)
        if not stat:
            result.skipped.append(
                {"type": "path", "source": source_path, "target": target_path, "reason": "missing"}
            )
            return False
        if stat["is_dir"]:
            copied = await self._mkdir_if_missing(target_path)
            if copied:
                result.directories += 1
            for entry in await self._ls(source_path):
                child_copied = await self._copy_path(
                    f"{source_path}/{entry['name']}",
                    f"{target_path}/{entry['name']}",
                    result,
                )
                copied = copied or child_copied
            return copied
        if await self._exists(target_path):
            result.skipped.append(
                {
                    "type": "file",
                    "source": source_path,
                    "target": target_path,
                    "reason": "target already exists; kept existing target",
                }
            )
            return False
        await self._ensure_parent_dirs(target_path)
        await self._agfs.write(target_path, await self._agfs.read(source_path))
        result.files += 1
        return True

    async def _read_json_file(self, path: str) -> Any:
        try:
            raw = await self._agfs.read(path)
        except Exception:
            return None
        if isinstance(raw, bytes):
            text = raw.decode("utf-8")
        elif hasattr(raw, "content"):
            text = raw.content.decode("utf-8")
        else:
            text = str(raw)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    async def _delete_vectors(self, target: LegacyCleanupTarget) -> VectorMigrationResult:
        vector_store = getattr(self._service, "vikingdb_manager", None)
        return await delete_vector_records(
            vector_store,
            account_id=target.account_id,
            uri=target.source_uri,
            recursive=True,
        )

    async def _ls(self, path: str) -> list[dict[str, Any]]:
        try:
            entries = await self._agfs.ls(path)
        except Exception:
            return []
        normalized = []
        for entry in entries:
            name = entry.get("name", "")
            if not name or name in {".", ".."}:
                continue
            normalized.append(
                {
                    "name": name,
                    "is_dir": bool(entry.get("isDir", entry.get("is_dir", False))),
                }
            )
        return normalized

    async def _stat(self, path: str) -> dict[str, Any] | None:
        try:
            stat = await self._agfs.stat(path)
        except Exception:
            return None
        return {"is_dir": bool(stat.get("isDir", stat.get("is_dir", False)))}

    async def _exists(self, path: str) -> bool:
        return await self._stat(path) is not None

    async def _mkdir_if_missing(self, path: str) -> bool:
        if await self._exists(path):
            return False
        await self._ensure_parent_dirs(path)
        try:
            await self._agfs.mkdir(path)
            return True
        except Exception as exc:
            if "exist" in str(exc).lower() or "already" in str(exc).lower():
                return False
            raise

    async def _ensure_parent_dirs(self, path: str) -> None:
        parent = path.rsplit("/", 1)[0]
        if not parent or parent == path:
            return
        parts = [part for part in parent.strip("/").split("/") if part]
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else f"/{part}"
            await self._mkdir_if_missing(current)

    def _path_to_uri(self, account_id: str, path: str) -> str:
        prefix = f"/local/{account_id}/"
        if path.startswith(prefix):
            return "viking://" + path[len(prefix) :].strip("/")
        return path
