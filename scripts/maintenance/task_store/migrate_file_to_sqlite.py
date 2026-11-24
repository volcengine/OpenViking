#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""将 File TaskStore 离线迁移到 SQLite。

运行位置与环境：
    在 OpenViking 仓库根目录中，使用已安装项目依赖的 Python 环境运行。
    加密任务文件需要提供原来的 ov.conf 和解密密钥。

使用步骤：
    1. 停止所有使用源工作目录的服务和工作进程。
    2. 预检查：读取、解析源数据，不创建目标数据库，也不修改源队列。
       python -m scripts.maintenance.task_store.migrate_file_to_sqlite --config ov.conf
    3. 正式迁移：写入 Task、事件和 Work，并释放成功消息的旧租约。
       python -m scripts.maintenance.task_store.migrate_file_to_sqlite --config ov.conf --execute
    4. 确认退出码为 0 后，修改 ov.conf，再启动服务：
       storage.task_store.provider = "sqlite"
       如果覆盖了目标路径、表名前缀或 service_name，服务配置也要与迁移参数一致。

常用参数：
    --workspace /path/to/data       覆盖配置中的源工作目录。
    --sqlite-path /path/to/tasks.db 覆盖目标路径；默认取 task_store.params.path，
                                   未配置时使用 <workspace>/task_store/tasks.db。
    --queue-db /path/to/queue.db    覆盖源 QueueFS 数据库路径。
    --skip-queue-works              只迁移 Task 和事件，不检查或修改源队列。
    --on-conflict update|skip|fail  已有记录的处理策略，默认为 update。
    --account-id / --user-id        按账户或用户筛选任务文件，不筛选队列消息。
    --queue-name Semantic          按队列名筛选消息，可重复提供。
    --table / --service-name        覆盖目标表名前缀或服务标识。
    --help                         查看完整命令说明。

结果与重复执行：
    退出码 0 表示本次检查或迁移没有失败；退出码 1 表示有失败。
    最终失败列表输出到 stdout，前缀为 OPENVIKING_MIGRATION_RESULT；
    进度、计数和脱敏错误输出到 stderr。
    默认 update 会用源数据覆盖已有 Task 和事件；只希望补充缺失记录时使用 skip。
    单项失败不会撤销此前成功提交的记录，可修复原因后再次执行。
    预检查不验证目标数据库的实际写入，不能代替正式迁移结果。

迁移范围：
    源任务 JSON 文件保留不变；正式迁移会将成功消息的 processing 状态改为 pending。
    必须显式切换 provider，否则保留的源任务目录仍会使默认探测选择 File 后端。
    File 后端未持久化已完成 Work 的历史，因此无法重建这些记录。

Task 字段映射、QueueFS 身份解析和迁移顺序沿用 migrate_file_to_mysql.py。
数据库写入使用 OpenViking 的 SQLite TaskStore。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import delete
from sqlalchemy.exc import OperationalError

from openviking.crypto.config import bootstrap_encryption
from openviking.service.task_store_sqlite import SQLiteTaskWorkStore
from openviking.utils.process_lock import acquire_data_dir_lock, release_data_dir_lock
from openviking.utils.time_utils import parse_iso_datetime
from openviking_cli.utils.config.config_utils import DEFAULT_SERVICE_NAME, normalize_service_name


@dataclass(frozen=True)
class WorkspaceLayout:
    workspace: Path
    store_root: Path

    def tasks_root(self, account_id: str) -> Path:
        if account_id == "_system":
            return self.store_root / "_system/tasks"
        return self.store_root / account_id / "_system/tasks"

    def queue_db(self) -> Path:
        # 当前 RAGFS 将 QueueFS 存放在 viking/local 文件存储根目录之外。
        current = self.workspace / "_system/queue/queue.db"
        return current if current.is_file() else self.store_root / "_system/queue/queue.db"


@dataclass(frozen=True)
class TaskFile:
    path: Path
    account_id: str
    user_id: str


@dataclass(frozen=True)
class WorkRow:
    task_id: str
    work_id: str
    account_id: str
    user_id: str
    queue_name: str
    state: str
    enqueued_at: float
    started_at: float | None
    updated_at: float
    queue_message_id: str


@dataclass
class TaskSummary:
    task_files_seen: int = 0
    tasks_inserted: int = 0
    tasks_existing: int = 0
    tasks_updated: int = 0
    task_events_inserted: int = 0
    queue_messages_seen: int = 0
    works_reconstructed: int = 0
    works_inserted: int = 0
    works_existing: int = 0
    works_updated: int = 0
    works_missing_task: int = 0
    invalid_task_files: int = 0
    invalid_queue_messages: int = 0
    failed: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


class MigrationAborted(RuntimeError):
    """数据库操作失败，且失败信息已记录，终止当前迁移。"""


def _write_log(entry: dict[str, Any]) -> None:
    print(json.dumps(entry, ensure_ascii=False, sort_keys=True), file=sys.stderr)


IDENTITY_FIELDS = ("task_id", "account_id", "user_id", "work_id", "queue_name", "queue_message_id")
WORK_ID_FIELD = "_task_work_id"
ACCOUNT_ID_FIELD = "_task_account_id"
USER_ID_FIELD = "_task_user_id"


def _record_failure(
    summary: TaskSummary,
    exc: Exception,
    *,
    stage: str,
    source_file: Path | str,
    reason: str = "record operation failed; error details redacted",
    **identity: str,
) -> None:
    # 不输出 SQL 参数、源 JSON、加密密钥或认证数据，避免泄露敏感信息。
    safe_messages = {
        "expected a JSON object",
        "stored queue message must contain a JSON object",
        "queue work is missing identity fields",
        "task already exists",
        "work already exists",
        "target task ownership differs from source",
        "target work ownership differs from source",
        "queue work has no owned target task",
        "queue work task failed migration",
        "QueueFS database does not exist",
        "Unsupported QueueFS queue_messages schema",
        "another instance is already using the data directory",
    }
    if str(exc) in safe_messages:
        reason = str(exc)
    elif isinstance(exc, json.JSONDecodeError):
        reason = "invalid source JSON"
    elif isinstance(exc, UnicodeError):
        reason = "invalid source text encoding"
    elif isinstance(exc, FileNotFoundError):
        reason = "source or configuration file is missing"
    elif isinstance(exc, PermissionError):
        reason = "source access denied"
    entry = {
        "event": "migration_failed",
        "stage": stage,
        "source_file": str(source_file),
        "error_type": type(exc).__name__,
        "reason": reason,
        "partial_writes_remain": True,
        **{key: str(identity.get(key) or "") for key in IDENTITY_FIELDS},
    }
    _write_log(entry)
    kind = "work" if stage in {"work", "work_read"} else "task"
    summary.failed += 1
    summary.failures.append(
        {
            "type": kind,
            "account_id": entry["account_id"],
            "user_id": entry["user_id"],
            "task_id": entry["task_id"],
            **({"work_id": entry["work_id"]} if kind == "work" else {}),
            "reason": f"{type(exc).__name__}: {reason}",
        }
    )


def _stage_finished(summary: TaskSummary, stage: str, started: float) -> None:
    _write_log(
        {
            "event": "migration_stage_finished",
            "stage": stage,
            "elapsed_seconds": time.monotonic() - started,
            "counts": _summary_counts(summary),
        }
    )


def _summary_counts(summary: TaskSummary) -> dict[str, int]:
    return {key: value for key, value in vars(summary).items() if key != "failures"}


def _progress(summary: TaskSummary, stage: str, last: float) -> float:
    now = time.monotonic()
    if now - last >= 5:
        _write_log(
            {
                "event": "migration_progress",
                "stage": stage,
                "counts": _summary_counts(summary),
            }
        )
        return now
    return last


def print_summary(summary: TaskSummary) -> None:
    print(
        "OPENVIKING_MIGRATION_RESULT "
        + json.dumps({"failures": {"accounts": [], "tasks": summary.failures}}, ensure_ascii=False)
    )


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    config = json.loads(os.path.expandvars(path.expanduser().read_text(encoding="utf-8-sig")))
    if not isinstance(config, dict):
        raise ValueError("expected a JSON object")
    return config


def workspace_from_config(config: dict[str, Any]) -> Path:
    storage = config.get("storage", {})
    return Path(storage.get("workspace") or storage.get("agfs", {}).get("path") or "./data")


def resolve_workspace_layout(workspace: Path) -> WorkspaceLayout:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(workspace)
    candidates = [workspace, workspace / "viking", workspace / "local"]
    for root in candidates:
        if (root / "_system/accounts.json").is_file():
            return WorkspaceLayout(workspace, root)
    for root in candidates[1:]:
        if root.is_dir():
            return WorkspaceLayout(workspace, root)
    # 开源版 FileStore 可能没有 accounts.json。
    return WorkspaceLayout(workspace, workspace)


def iter_task_files(
    layout: WorkspaceLayout,
    account_id: str | None = None,
    user_id: str | None = None,
) -> Iterator[TaskFile]:
    for account in sorted(path for path in layout.store_root.iterdir() if path.is_dir()):
        if account_id and account.name != account_id:
            continue
        task_root = layout.tasks_root(account.name)
        if not task_root.is_dir():
            continue
        for user in sorted(path for path in task_root.iterdir() if path.is_dir()):
            if user_id and user.name != user_id:
                continue
            for path in sorted(user.glob("*.json")):
                if path.is_file() and not path.name.startswith("."):
                    yield TaskFile(path, account.name, user.name)


class SourceReader:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.encryptor: Any = None

    async def read_json(self, path: Path, account_id: str) -> dict[str, Any]:
        raw = path.read_bytes()
        if raw.startswith(b"OVE1"):
            if self.encryptor is None:
                encryption = self.config.get("encryption", {})
                local = encryption.get("local", {})
                if encryption.get("provider", "local") == "local" and not local.get("root_key"):
                    key = Path(local.get("key_file", "~/.openviking/master.key")).expanduser()
                    if not key.is_file():
                        raise ValueError("Existing decryption key is required")
                self.encryptor = await bootstrap_encryption(self.config)
            if self.encryptor is None:
                raise ValueError("Encrypted source requires the original encryption configuration")
            raw = await self.encryptor.decrypt(account_id, raw)
        payload = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        return payload


def task_values(payload: dict[str, Any], service_name: str) -> dict[str, Any]:
    resource_id = payload.get("resource_id")
    meta = payload.get("meta") or {}
    values = {
        "service_name": service_name,
        "task_id": str(payload["task_id"]),
        "task_type": str(payload.get("task_type") or ""),
        "status": str(payload.get("status") or "pending"),
        "created_at": float(payload.get("created_at") or 0),
        "updated_at": float(payload.get("updated_at") or payload.get("created_at") or 0),
        "resource_id": resource_id,
        "resource_id_hash": hashlib.sha256(str(resource_id).encode()).hexdigest()
        if resource_id is not None
        else None,
        "account_id": str(payload["account_id"]),
        "user_id": str(payload["user_id"]),
        "meta": meta,
        "stage": payload.get("stage"),
        "result": payload.get("result"),
        "error": payload.get("error"),
        "auth": payload.get("auth") or {},
        "processing_seconds": payload.get("processing_seconds"),
        "reject_if_active": False,
        "is_internal": bool(isinstance(meta, dict) and meta.get("internal")),
        "active_business_key": None,
    }
    if any(not math.isfinite(values[key]) for key in ("created_at", "updated_at")):
        raise ValueError("Task timestamp must be finite")
    return values


def _event_values(history: Any) -> list[dict[str, Any]]:
    if not isinstance(history, dict):
        return []
    items = history.get("items") or []
    if not isinstance(items, list):
        return []
    dropped_count = int(history.get("dropped_count") or 0)
    started_mid_task = bool(history.get("started_mid_task"))
    return [
        {
            "seq": int(item["seq"]),
            "recorded_at": str(item.get("recorded_at") or ""),
            "kind": str(item.get("kind") or ""),
            "status": str(item.get("status") or ""),
            "stage": item.get("stage"),
            "operation": item.get("operation"),
            "error": item.get("error"),
            "dropped_count": dropped_count,
            "started_mid_task": started_mid_task,
        }
        for item in items
        if isinstance(item, dict)
    ]


def _ensure_owner(row: Any, account_id: str, user_id: str, kind: str) -> None:
    if (row.account_id, row.user_id) != (account_id, user_id):
        raise ValueError(f"target {kind} ownership differs from source")


def _upsert_row(session, model, row, values, on_conflict, kind, **defaults) -> str:
    if row is None:
        session.add(model(**defaults, **values))
        return "inserted"
    _ensure_owner(row, values["account_id"], values["user_id"], kind)
    if on_conflict == "fail":
        raise ValueError(f"{kind} already exists")
    if on_conflict == "skip":
        return "existing"
    for key, value in values.items():
        setattr(row, key, value)
    return "updated"


async def _replace_events(session, store, task_id: str, history: Any) -> int:
    Event = store._models.Event
    await session.execute(
        delete(Event).where(Event.service_name == store._service_name, Event.task_id == task_id)
    )
    events = _event_values(history)
    session.add_all(
        [Event(service_name=store._service_name, task_id=task_id, **event) for event in events]
    )
    return len(events)


async def _insert_task(
    store: SQLiteTaskWorkStore,
    payload: dict[str, Any],
    on_conflict: str,
) -> tuple[str, int]:
    """在同一个 SQLite 事务中写入单个 Task 并替换其事件历史。"""

    async def insert() -> tuple[str, int]:
        values = task_values(payload, store._service_name)
        async with store._transaction() as session:
            row = await store._load_task(
                session, values["task_id"], account_id=None, user_id=None, lock=True
            )
            # 插入task
            outcome = _upsert_row(session, store._models.Task, row, values, on_conflict, "task")
            if outcome == "existing":
                return outcome, 0
            # 插入事件
            event_count = await _replace_events(
                session, store, values["task_id"], payload.get("execution_events")
            )
        return outcome, event_count

    return await store._sql_runtime.run(insert)


def _non_empty_str(value: Any) -> str:
    return str(value) if value not in (None, "") else ""


def _first_string(*values: Any) -> str:
    for value in values:
        text = _non_empty_str(value)
        if text:
            return text
    return ""


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _payload_candidate_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [payload]
    for key in ("payload", "data"):
        nested_payload = payload.get(key)
        if isinstance(nested_payload, dict):
            candidates.append(nested_payload)
    return candidates


def _extract_task_id(payload: dict[str, Any]) -> str:
    return _first_string(
        *(
            value
            for candidate in _payload_candidate_dicts(payload)
            for value in (
                candidate.get("task_id"),
                candidate.get("_task_id"),
                _dict_value(candidate.get("task")).get("task_id"),
                _dict_value(candidate.get("task")).get("id"),
            )
        )
    )


def _extract_work_id(payload: dict[str, Any], envelope: dict[str, Any]) -> str:
    work_id = _first_string(
        *(
            value
            for candidate in _payload_candidate_dicts(payload)
            for value in (
                candidate.get(WORK_ID_FIELD),
                candidate.get("work_id"),
                candidate.get("task_work_id"),
                _dict_value(candidate.get("work")).get("work_id"),
                _dict_value(candidate.get("work")).get("id"),
            )
        )
    )
    if not work_id and envelope.get("id"):
        work_id = f"queuefs:{envelope['id']}"
    return work_id


def _owner_from_context(context_data: dict[str, Any]) -> tuple[str, str]:
    return (
        _first_string(
            context_data.get(ACCOUNT_ID_FIELD),
            context_data.get("task_account_id"),
            context_data.get("account_id"),
            context_data.get("owner_account_id"),
        ),
        _first_string(
            context_data.get(USER_ID_FIELD),
            context_data.get("task_user_id"),
            context_data.get("user_id"),
            context_data.get("owner_user_id"),
        ),
    )


def _owner_from_payload(payload: dict[str, Any]) -> tuple[str, str]:
    for candidate in _payload_candidate_dicts(payload):
        account_id = _first_string(
            candidate.get(ACCOUNT_ID_FIELD),
            candidate.get("task_account_id"),
        )
        user_id = _first_string(
            candidate.get(USER_ID_FIELD),
            candidate.get("task_user_id"),
        )
        if account_id and user_id:
            return account_id, user_id

    for candidate in _payload_candidate_dicts(payload):
        account_id = _first_string(candidate.get("account_id"), candidate.get("owner_account_id"))
        user_id = _first_string(candidate.get("user_id"), candidate.get("owner_user_id"))
        if account_id and user_id:
            return account_id, user_id

        user = _dict_value(candidate.get("user"))
        account_id = account_id or _first_string(
            user.get("account_id"), user.get("owner_account_id")
        )
        user_id = user_id or _first_string(user.get("user_id"), user.get("owner_user_id"))
        if account_id and user_id:
            return account_id, user_id

        auth = _dict_value(candidate.get("auth"))
        account_id = account_id or _first_string(
            auth.get("account_id"), auth.get("owner_account_id")
        )
        user_id = user_id or _first_string(auth.get("user_id"), auth.get("owner_user_id"))
        if account_id and user_id:
            return account_id, user_id

        for context_key in ("context_data", "context"):
            context_account_id, context_user_id = _owner_from_context(
                _dict_value(candidate.get(context_key))
            )
            account_id = account_id or context_account_id
            user_id = user_id or context_user_id
            if account_id and user_id:
                return account_id, user_id

    return "", ""


def _parse_timestamp(value: Any, fallback: float) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            parsed = parse_iso_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            pass
    return fallback


def _decode_stored_message(row: dict[str, Any]) -> dict[str, Any]:
    stored = json.loads(row["data"])
    if not isinstance(stored, dict):
        raise ValueError("stored queue message must contain a JSON object")
    is_queuefs_envelope = any(key in stored for key in ("id", "data", "timestamp"))
    raw_data = stored.get("data") if is_queuefs_envelope else stored
    if isinstance(raw_data, list) and all(isinstance(item, int) for item in raw_data):
        raw_data = bytes(int(item) for item in raw_data).decode("utf-8", errors="replace")
    elif isinstance(raw_data, (dict, list)):
        raw_data = json.dumps(raw_data, ensure_ascii=False, separators=(",", ":"))
    elif raw_data is None:
        raw_data = ""
    else:
        raw_data = str(raw_data)
    enqueued_at = _parse_timestamp(stored.get("timestamp"), float(row.get("timestamp") or 0))
    return {
        "id": str(stored.get("id") or row.get("message_id") or ""),
        "data": raw_data,
        "timestamp": enqueued_at,
    }


def _queue_payload_dict(envelope: dict[str, Any]) -> dict[str, Any] | None:
    payload = envelope.get("data", envelope)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            # NamedQueue 也接受不参与任务跟踪的普通字符串消息。
            if payload.lstrip().startswith("{"):
                raise
            return None
    return payload if isinstance(payload, dict) else None


def _validate_queue_source(queue_db: Path) -> None:
    if not queue_db.is_file():
        raise ValueError("QueueFS database does not exist")
    with closing(sqlite3.connect(f"{queue_db.as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute(
            "SELECT id, queue_name, message_id, data, timestamp FROM queue_messages LIMIT 0"
        )


def iter_queue_works(
    queue_db: Path,
    queue_filter: set[str] | None,
    summary: TaskSummary,
) -> Iterator[WorkRow]:
    if not queue_db.is_file():
        return
    with closing(sqlite3.connect(f"{queue_db.as_uri()}?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(queue_messages)")}
        predicate = " WHERE status IN ('pending', 'processing')" if "status" in columns else ""
        for raw in connection.execute(f"SELECT * FROM queue_messages{predicate} ORDER BY id"):
            row = dict(raw)
            if queue_filter is not None and row["queue_name"] not in queue_filter:
                continue
            task_id = work_id = account_id = user_id = ""
            try:
                envelope = _decode_stored_message(row)
                payload = _queue_payload_dict(envelope)
                if payload is None:
                    continue
                task_id = _extract_task_id(payload)
                if not task_id:
                    continue
                work_id = _extract_work_id(payload, envelope)
                account_id, user_id = _owner_from_payload(payload)
                if not work_id or not account_id or not user_id:
                    raise ValueError("queue work is missing identity fields")
                timestamp = float(envelope["timestamp"] or row.get("timestamp") or 0)
                if not math.isfinite(timestamp):
                    raise ValueError("Queue timestamp must be finite")
                yield WorkRow(
                    task_id,
                    work_id,
                    account_id,
                    user_id,
                    str(row["queue_name"]),
                    "pending",
                    timestamp,
                    None,
                    timestamp,
                    str(row.get("message_id") or ""),
                )
            except Exception as exc:
                summary.invalid_queue_messages += 1
                _record_failure(
                    summary,
                    exc,
                    stage="work_read",
                    source_file=queue_db,
                    task_id=task_id,
                    work_id=work_id,
                    account_id=account_id,
                    user_id=user_id,
                    queue_name=str(row["queue_name"]),
                    queue_message_id=str(row.get("message_id") or ""),
                    reason="queue work source could not be read",
                )


async def _insert_work(
    store: SQLiteTaskWorkStore,
    work: WorkRow,
    on_conflict: str,
) -> str:
    async def insert() -> str:
        async with store._transaction() as session:
            task = await store._load_task(
                session, work.task_id, account_id=None, user_id=None, lock=True
            )
            if task is None:
                return "missing_task"
            _ensure_owner(task, work.account_id, work.user_id, "task")
            Work = store._models.Work
            row = await session.get(Work, (store._service_name, work.task_id, work.work_id))
            values = asdict(work)
            del values["queue_message_id"]
            values["service_name"] = store._service_name
            return _upsert_row(
                session,
                Work,
                row,
                values,
                on_conflict,
                "work",
                requeue_count=0,
                error=None,
                errors=[],
                context_count=0,
                finished_at=None,
            )

    return await store._sql_runtime.run(insert)


def reset_processing_queue_messages(queue_db: Path, message_ids: set[str]) -> int:
    if not message_ids:
        return 0
    with closing(sqlite3.connect(queue_db)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(queue_messages)")}
        if "status" not in columns:
            return 0
        started = ", processing_started_at=NULL" if "processing_started_at" in columns else ""
        count = 0
        ordered = sorted(message_ids)
        with connection:
            # 分批更新，避免超过旧版 SQLite 的 999 个 SQL 参数限制。
            for offset in range(0, len(ordered), 900):
                batch = ordered[offset : offset + 900]
                placeholders = ",".join("?" for _ in batch)
                count += connection.execute(
                    f"UPDATE queue_messages SET status='pending'{started} "
                    f"WHERE status='processing' AND message_id IN ({placeholders})",
                    batch,
                ).rowcount
        return count


async def migrate(
    *,
    workspace: Path,
    config: dict[str, Any],
    sqlite_path: Path,
    queue_db: Path | None = None,
    service_name: str | None = None,
    execute: bool = False,
    on_conflict: str = "update",
    account_id: str | None = None,
    user_id: str | None = None,
    queue_names: set[str] | None = None,
    skip_queue_works: bool = False,
    table: str | None = None,
    summary: TaskSummary | None = None,
) -> TaskSummary:
    summary = summary if summary is not None else TaskSummary()
    if on_conflict not in {"skip", "update", "fail"}:
        raise ValueError("Unsupported conflict mode")
    layout = resolve_workspace_layout(workspace)
    workspace = layout.workspace
    storage = config.get("storage", {})
    agfs = storage.get("agfs", {})
    if agfs.get("backend", "local") != "local":
        raise ValueError("This offline script requires a local FileStore source")
    if queue_db is None:
        configured = agfs.get("queuefs", {}).get("db_path") or agfs.get("queue_db_path")
        queue_db = Path(configured) if configured else layout.queue_db()
        if not skip_queue_works and agfs.get("queuefs", {}).get("backend", "sqlite") not in {
            "sqlite",
            "sqlite3",
        }:
            raise ValueError("QueueFS migration requires a persisted SQLite queue database")
    queue_db = queue_db.expanduser().resolve()
    sqlite_path = sqlite_path.expanduser().resolve()
    if sqlite_path == queue_db or (
        sqlite_path.exists() and queue_db.exists() and sqlite_path.samefile(queue_db)
    ):
        raise ValueError("Destination must not be the QueueFS database")
    if execute and not skip_queue_works:
        # 在创建目标数据库或写入任何 Task 之前，先检查源队列数据库。
        _validate_queue_source(queue_db)
    configured_params = storage.get("task_store", {}).get("params", {})
    service_name = normalize_service_name(
        service_name
        or configured_params.get("service_name")
        or config.get("service_name")
        or DEFAULT_SERVICE_NAME
    )
    params = {
        key: value
        for key, value in configured_params.items()
        if key in {"table", "busy_timeout_ms", "maxsize"}
    }
    params["path"] = str(sqlite_path)
    if table is not None:
        params["table"] = table
    reader = SourceReader(config)
    tasks: dict[str, tuple[str, str]] = {}
    failed_tasks: set[str] = set()
    queue_message_ids_to_reset: set[str] = set()
    store = SQLiteTaskWorkStore(params=params, service_name=service_name) if execute else None
    lock = acquire_data_dir_lock(str(workspace)) if execute else None
    started = time.monotonic()
    last_progress = started
    try:
        if store is not None:
            await store.load()
        # 2. 遍历源任务文件，读取 JSON 并写入目标 SQLite
        for source in iter_task_files(layout, account_id, user_id):
            summary.task_files_seen += 1
            task_id = source.path.stem
            stage = "read"
            try:
                payload = await reader.read_json(source.path, source.account_id)
                task_id = str(payload.get("task_id") or task_id)
                payload.update(
                    task_id=task_id, account_id=source.account_id, user_id=source.user_id
                )
                # 沿用 MySQL 迁移规则：身份以目录为准，缺失任务 ID 时使用文件名。
                owner = (source.account_id, source.user_id)
                if task_id in tasks and tasks[task_id] != owner:
                    raise ValueError("target task ownership differs from source")
                stage = "task"
                if store is not None:
                    # 3. 在 SQLite 中写入 Task 及其事件历史
                    outcome, events = await _insert_task(store, payload, on_conflict)
                    setattr(summary, f"tasks_{outcome}", getattr(summary, f"tasks_{outcome}") + 1)
                    summary.task_events_inserted += events
                else:
                    task_values(payload, service_name)
                    _event_values(payload.get("execution_events"))
                tasks[task_id] = owner
            except Exception as exc:
                failed_tasks.add(task_id)
                if stage == "read":
                    summary.invalid_task_files += 1
                _record_failure(
                    summary,
                    exc,
                    stage=stage,
                    source_file=source.path,
                    task_id=task_id,
                    account_id=source.account_id,
                    user_id=source.user_id,
                    reason="task source could not be read"
                    if stage == "read"
                    else "task write failed",
                )
                if isinstance(exc, OperationalError):
                    # 数据库不可用时立即终止，避免对后续每条记录重复报错。
                    raise MigrationAborted from exc
            last_progress = _progress(summary, "tasks", last_progress)
        _stage_finished(summary, "tasks", started)
        started = time.monotonic()
        if not skip_queue_works:
            # 4. 从队列消息中重建work，processing → pending，`processing_started_at → NULL`
            for work in iter_queue_works(queue_db, queue_names, summary):
                summary.queue_messages_seen += 1
                try:
                    if work.task_id in failed_tasks:
                        raise ValueError("queue work task failed migration")
                    if store is not None:
                        # 5. 在 SQLite 中写入 Work
                        outcome = await _insert_work(store, work, on_conflict)
                        setattr(
                            summary, f"works_{outcome}", getattr(summary, f"works_{outcome}") + 1
                        )
                        if outcome == "missing_task":
                            raise ValueError("queue work has no owned target task")
                        if work.queue_message_id:
                            queue_message_ids_to_reset.add(work.queue_message_id)
                    summary.works_reconstructed += 1
                except Exception as exc:
                    _record_failure(
                        summary,
                        exc,
                        stage="work",
                        source_file=queue_db,
                        **{key: str(getattr(work, key)) for key in IDENTITY_FIELDS},
                    )
                    if isinstance(exc, OperationalError):
                        raise MigrationAborted from exc
                last_progress = _progress(summary, "works", last_progress)
            if execute:
                # 6. 将已成功迁移的 QueueFS 消息的 processing 状态重置为 pending
                reset_count = reset_processing_queue_messages(queue_db, queue_message_ids_to_reset)
                _write_log(
                    {
                        "event": "migration_stage_finished",
                        "stage": "lease_reset",
                        "completed": reset_count,
                    }
                )
        _stage_finished(summary, "works", started)
    finally:
        try:
            if store is not None:
                await store.close()
        finally:
            if lock is not None:
                release_data_dir_lock(lock)
    return summary


# 1. 确定源目录和目标数据库
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", nargs="?", choices=("tasks",), default="tasks")
    parser.add_argument("--config", type=Path, help="Original JSON ov.conf")
    parser.add_argument("--workspace", type=Path, help="Override storage.workspace")
    parser.add_argument("--sqlite-path", type=Path, help="Default: <workspace>/task_store/tasks.db")
    parser.add_argument("--queue-db", type=Path, help="Override source QueueFS SQLite database")
    parser.add_argument("--service-name")
    parser.add_argument("--table", help="Override Task table prefix")
    parser.add_argument("--account-id", help="Filter Task files by account")
    parser.add_argument("--user-id", help="Filter Task files by user")
    parser.add_argument("--queue-name", action="append", help="Filter queue names; repeatable")
    parser.add_argument("--skip-queue-works", action="store_true")
    parser.add_argument("--on-conflict", choices=("skip", "update", "fail"), default="update")
    parser.add_argument(
        "--execute", action="store_true", help="Write after stopping source workers"
    )
    args = parser.parse_args()
    summary = TaskSummary()
    started = time.monotonic()
    try:
        config = load_config(args.config)
        workspace = args.workspace or workspace_from_config(config)
        configured = config.get("storage", {}).get("task_store", {}).get("params", {}).get("path")
        sqlite_path = args.sqlite_path or (
            Path(configured) if configured else workspace / "task_store/tasks.db"
        )
        asyncio.run(
            migrate(
                workspace=workspace,
                config=config,
                sqlite_path=sqlite_path,
                queue_db=args.queue_db,
                service_name=args.service_name,
                execute=args.execute,
                on_conflict=args.on_conflict,
                account_id=args.account_id,
                user_id=args.user_id,
                table=args.table,
                queue_names=set(args.queue_name) if args.queue_name else None,
                skip_queue_works=args.skip_queue_works,
                summary=summary,
            )
        )
    except MigrationAborted:
        pass
    except Exception as exc:
        _record_failure(
            summary,
            exc,
            stage="tasks",
            source_file=args.config or args.workspace or "",
            reason="migration setup or execution failed; error details redacted",
        )
    _stage_finished(summary, "migration", started)
    print_summary(summary)
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
