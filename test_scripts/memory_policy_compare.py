#!/usr/bin/env python3
"""
Compare unset and explicit session memory_policy.memory_types behavior with peer enabled.

Run against an existing local server:
    .venv/bin/python test_scripts/memory_policy_compare.py

Start a local server from ov.conf when needed:
    .venv/bin/python test_scripts/memory_policy_compare.py --start-server
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional
from uuid import uuid4

import httpx

import openviking as ov

DEFAULT_CONFIG_PATH = "ov.conf"
DEFAULT_URL = "http://127.0.0.1:1934"
DEFAULT_ROOT_KEY = "test"
DEFAULT_ACCOUNT_PREFIX = "memory-policy-smoke"
DEFAULT_POLICY_USER = "policy_default"
FILTERED_POLICY_USER = "policy_filtered"
PEER_ID = "policy_peer"
DEFAULT_POLICY = {"peer": {"enabled": True}}
FILTERED_POLICY = {
    "peer": {"enabled": True},
    "memory_types": ["profile", "preferences", "trajectories"],
}
FILTERED_ALLOWED_TYPES = {"profile", "preferences", "trajectories"}
TERMINAL_TASK_STATUSES = {"completed", "failed"}


@dataclass(frozen=True)
class CaseSpec:
    name: str
    user_id: str
    session_id: str
    api_key: str
    memory_policy: Optional[dict[str, Any]]
    allowed_types: Optional[set[str]]
    required_any_types: set[str]


@dataclass(frozen=True)
class CaseResult:
    case: CaseSpec
    task: dict[str, Any]
    memory_entries: list[dict[str, Any]]
    memory_types: set[str]
    archive_diff: Optional[dict[str, Any]]
    archive_diff_types: set[str]
    errors: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def load_json_file(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def default_url_from_config(config: dict[str, Any]) -> str:
    server = config.get("server") or {}
    host = server.get("host") or "127.0.0.1"
    port = server.get("port") or 1934
    return f"http://{host}:{port}"


def root_key_from_config(config: dict[str, Any]) -> str:
    server = config.get("server") or {}
    return server.get("root_api_key") or DEFAULT_ROOT_KEY


def print_json(title: str, payload: Any) -> None:
    print(f"\n== {title} ==")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def ensure_server_ready(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=5.0)
        response.raise_for_status()
        data = response.json()
    except Exception:
        return False
    return data.get("status") == "ok"


def wait_server_ready(base_url: str, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ensure_server_ready(base_url):
            return
        time.sleep(1.0)
    raise TimeoutError(f"OpenViking server is not ready: {base_url}")


def start_server(config_path: Path, base_url: str, startup_timeout: float) -> subprocess.Popen:
    server_bin = Path(".venv/bin/openviking-server")
    command = [str(server_bin if server_bin.exists() else "openviking-server")]
    command.extend(["--config", str(config_path)])

    log_path = Path(tempfile.gettempdir()) / f"openviking-memory-policy-{uuid4().hex}.log"
    log_file = log_path.open("w", encoding="utf-8")
    process = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        wait_server_ready(base_url, startup_timeout)
    except Exception:
        process.terminate()
        raise RuntimeError(f"Failed to start server. Log: {log_path}") from None

    print(f"Started OpenViking server pid={process.pid}, log={log_path}")
    return process


def close_client(client: Any) -> None:
    try:
        client.close()
    except Exception:
        pass


def make_client(base_url: str, api_key: str, timeout: float):
    client = ov.SyncHTTPClient(
        url=base_url,
        api_key=api_key,
        timeout=timeout,
        profile_enabled=False,
    )
    client.initialize()
    return client


def bootstrap_users(
    base_url: str,
    root_key: str,
    account_id: str,
    timeout: float,
) -> dict[str, str]:
    admin_client = make_client(base_url, root_key, timeout)
    try:
        account = admin_client.admin_create_account(account_id, DEFAULT_POLICY_USER)
        filtered_user = admin_client.admin_register_user(
            account_id,
            FILTERED_POLICY_USER,
            role="user",
        )
    finally:
        close_client(admin_client)

    user_keys = {
        DEFAULT_POLICY_USER: account["user_key"],
        FILTERED_POLICY_USER: filtered_user["user_key"],
    }
    print_json(
        "users",
        {
            "account_id": account_id,
            "users": sorted(user_keys),
        },
    )
    return user_keys


def build_messages() -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": (
                "我是林澈，平台工程师，长期维护 OpenViking。"
                "我的稳定偏好是中文沟通、结论先行、代码改动要小而结构清楚，"
                "不要新增没有业务含义的 wrapper。2026-06-09 上午，我们决定把 "
                "PR #2236 的 session memory policy 调整成顶层 memory_types："
                "默认不限制类型，显式策略只允许白名单里的多个类型。"
            ),
            "created_at": "2026-06-09T10:00:00+08:00",
        },
        {
            "role": "user",
            "peer_id": PEER_ID,
            "content": (
                "我是许知行，PR #2236 的 reviewer，长期负责 session memory policy "
                "和 peer memory 路由审查。我的稳定偏好是测试必须覆盖 self 和 peer，"
                "对比默认 memory_types 不设与显式多个白名单类型。2026-06-09 上午，"
                "我要求 smoke 脚本确认 peer 目录也不会生成白名单之外的 identity 或 soul。"
            ),
            "created_at": "2026-06-09T10:00:30+08:00",
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "text",
                    "text": (
                        "我会先读取 memory_policy 和 session commit 路径，确认策略解析、"
                        "类型校验和提取 pipeline 的分发点。"
                    ),
                },
                {
                    "type": "tool",
                    "tool_id": "inspect_memory_policy",
                    "tool_name": "rg",
                    "skill_uri": "viking://user/skills/codebase_inspection",
                    "tool_input": {
                        "pattern": "memory_types|extract_execution_memories",
                        "path": "openviking/session",
                    },
                    "tool_output": (
                        "Found MemoryPolicy.memory_types as a top-level field. "
                        "Session commit validates it against MemoryTypeRegistry, then passes "
                        "the same whitelist into each memory extraction entry point. "
                        "The default policy enables peer memory and leaves allowed_memory_types "
                        "unset, while an explicit policy passes a whitelist into each extraction "
                        "pipeline."
                    ),
                    "tool_status": "completed",
                    "duration_ms": 22,
                    "prompt_tokens": 128,
                    "completion_tokens": 92,
                },
            ],
            "created_at": "2026-06-09T10:01:00+08:00",
        },
        {
            "role": "assistant",
            "peer_id": PEER_ID,
            "content": (
                "我会在同一份对话里保留你的 peer_id，并在提交后同时扫描 self memory "
                "和 peer memory，确认过滤策略对两个目标都生效。"
            ),
            "created_at": "2026-06-09T10:01:30+08:00",
        },
        {
            "role": "user",
            "content": (
                "请写一个 test_scripts 里的脚本，创建两个 user，给他们同一段对话，"
                "但设置不同 memory_policy。一个只开启 peer 但不设置 memory_types，"
                "另一个开启 peer 并设置 profile、preferences、trajectories 这几个类型，"
                "最后对比默认全量和显式白名单在 self 与 peer 两边的产物差异。"
            ),
            "created_at": "2026-06-09T10:02:00+08:00",
        },
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "text",
                    "text": ("我会用真实 HTTP 服务执行 smoke test，并轮询 commit task 到完成。"),
                },
                {
                    "type": "tool",
                    "tool_id": "write_compare_script",
                    "tool_name": "apply_patch",
                    "skill_uri": "viking://user/skills/code_editing",
                    "tool_input": {
                        "file": "test_scripts/memory_policy_compare.py",
                        "operation": "add",
                    },
                    "tool_output": (
                        "Created a script that bootstraps users, creates sessions with "
                        "peer-enabled policies, adds identical messages, commits, polls tasks, "
                        "and compares self and peer memory files by type."
                    ),
                    "tool_status": "completed",
                    "duration_ms": 31,
                    "prompt_tokens": 180,
                    "completion_tokens": 120,
                },
            ],
            "created_at": "2026-06-09T10:03:00+08:00",
        },
        {
            "role": "assistant",
            "content": (
                "脚本已完成：未设置 memory_types 的策略应尽量按当前 registry 全量抽取；"
                "显式策略只能在 self 和 peer 两边产生 profile、preferences、"
                "trajectories。"
            ),
            "created_at": "2026-06-09T10:04:00+08:00",
        },
    ]


def wait_task(client: Any, task_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    latest: dict[str, Any] = {}
    while time.time() < deadline:
        task = client.get_task(task_id) or {}
        latest = task
        status = task.get("status")
        if status in TERMINAL_TASK_STATUSES:
            return task
        time.sleep(2.0)
    raise TimeoutError(f"Task timed out: {task_id}, latest={latest}")


def entry_uri(entry: dict[str, Any]) -> str:
    return str(entry.get("uri") or "")


def collect_memory_entries(client: Any, user_id: str) -> list[dict[str, Any]]:
    root_uris = [
        f"viking://user/{user_id}/memories",
        f"viking://user/{user_id}/peers/{PEER_ID}/memories",
    ]
    memory_entries: list[dict[str, Any]] = []
    for root_uri in root_uris:
        try:
            entries = client.ls(root_uri, recursive=True, show_all_hidden=True) or []
        except Exception:
            continue
        memory_entries.extend(entry for entry in entries if isinstance(entry, dict))
    return memory_entries


def is_peer_memory_uri(uri: str) -> bool:
    return f"/peers/{PEER_ID}/memories/" in uri


def memory_type_from_uri(uri: str) -> str:
    if "/memories/trajectories/" in uri:
        return "trajectories"
    if "/memories/experiences/" in uri:
        return "experiences"
    if "/memories/preferences/" in uri:
        return "preferences"
    if "/memories/entities/" in uri:
        return "entities"
    if uri.endswith("/profile.md"):
        return "profile"
    if uri.endswith("/identity.md"):
        return "identity"
    if uri.endswith("/soul.md"):
        return "soul"
    if uri.endswith(".md"):
        return Path(uri).stem
    return ""


def collect_memory_types(entries: Iterable[dict[str, Any]]) -> set[str]:
    memory_types = set()
    for entry in entries:
        uri = entry_uri(entry)
        if not uri.endswith(".md"):
            continue
        if Path(uri).name.startswith("."):
            continue
        memory_type = memory_type_from_uri(uri)
        if memory_type:
            memory_types.add(memory_type)
    return memory_types


def extracted_memory_types(memory_types: set[str]) -> set[str]:
    return set(memory_types)


def read_archive_diff(client: Any, archive_uri: str) -> Optional[dict[str, Any]]:
    try:
        content = client.read(f"{archive_uri.rstrip('/')}/memory_diff.json")
    except Exception:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


def collect_archive_diff_types(diff: Optional[dict[str, Any]]) -> set[str]:
    if not diff:
        return set()
    operations = diff.get("operations") or {}
    memory_types = set()
    for key in ("adds", "updates", "deletes"):
        for item in operations.get(key) or []:
            memory_type = item.get("memory_type")
            if memory_type:
                memory_types.add(str(memory_type))
    return memory_types


def run_case(
    base_url: str,
    case: CaseSpec,
    timeout: float,
) -> CaseResult:
    client = make_client(base_url, case.api_key, timeout)
    errors: list[str] = []
    try:
        client.create_session(
            session_id=case.session_id,
            memory_policy=case.memory_policy,
        )
        client.batch_add_messages(case.session_id, build_messages())
        commit = client.commit_session(case.session_id)
        task_id = commit.get("task_id")
        archive_uri = commit.get("archive_uri") or (
            f"viking://session/{case.session_id}/history/archive_001"
        )
        if not task_id:
            raise RuntimeError(f"Commit did not return task_id: {commit}")

        task = wait_task(client, task_id, timeout)
        if task.get("status") != "completed":
            errors.append(f"commit task failed: {task.get('error') or task}")

        entries = collect_memory_entries(client, case.user_id)
        memory_types = collect_memory_types(entries)
        extracted_types = extracted_memory_types(memory_types)
        archive_diff = read_archive_diff(client, archive_uri)
        archive_diff_types = collect_archive_diff_types(archive_diff)

        if case.required_any_types and not extracted_types & case.required_any_types:
            errors.append(
                "missing any expected extracted memory type from: "
                f"{sorted(case.required_any_types)}"
            )
        if not any(is_peer_memory_uri(entry_uri(entry)) for entry in entries):
            errors.append(f"peer memory did not produce files for peer_id={PEER_ID}")

        if case.allowed_types is not None:
            outside_allowed = extracted_types - case.allowed_types
            if outside_allowed:
                errors.append(
                    f"extracted memory types outside explicit whitelist: {sorted(outside_allowed)}"
                )

        return CaseResult(
            case=case,
            task=task,
            memory_entries=entries,
            memory_types=memory_types,
            archive_diff=archive_diff,
            archive_diff_types=archive_diff_types,
            errors=errors,
        )
    finally:
        close_client(client)


def result_summary(result: CaseResult) -> dict[str, Any]:
    memory_files = sorted(
        entry_uri(entry)
        for entry in result.memory_entries
        if entry_uri(entry).endswith(".md") and not Path(entry_uri(entry)).name.startswith(".")
    )
    peer_memory_files = [uri for uri in memory_files if is_peer_memory_uri(uri)]
    return {
        "case": result.case.name,
        "user_id": result.case.user_id,
        "peer_id": PEER_ID,
        "session_id": result.case.session_id,
        "memory_policy": result.case.memory_policy,
        "task_status": result.task.get("status"),
        "memory_types": sorted(result.memory_types),
        "extracted_memory_types": sorted(extracted_memory_types(result.memory_types)),
        "archive_diff_types": sorted(result.archive_diff_types),
        "memory_files": memory_files,
        "peer_memory_files": peer_memory_files,
        "errors": result.errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare session memory_policy.memory_types filtering under two users."
    )
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="Path to ov.conf.")
    parser.add_argument("--url", default="", help="OpenViking server URL.")
    parser.add_argument("--root-key", default="", help="Root API key.")
    parser.add_argument("--account", default="", help="Account ID to create.")
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Seconds to wait for server startup and commit tasks.",
    )
    parser.add_argument(
        "--start-server",
        action="store_true",
        help="Start .venv/bin/openviking-server --config ov.conf if health check fails.",
    )
    parser.add_argument(
        "--leave-server-running",
        action="store_true",
        help="Do not terminate the server process started by this script.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config_path = Path(args.config)
    config = load_json_file(config_path)
    base_url = (args.url or default_url_from_config(config) or DEFAULT_URL).rstrip("/")
    root_key = args.root_key or root_key_from_config(config)
    run_id = time.strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8]
    account_id = args.account or f"{DEFAULT_ACCOUNT_PREFIX}-{run_id}"

    server_process: Optional[subprocess.Popen] = None
    if not ensure_server_ready(base_url):
        if not args.start_server:
            print(
                f"OpenViking server is not ready at {base_url}. "
                "Start it first or pass --start-server.",
                file=sys.stderr,
            )
            return 2
        server_process = start_server(config_path, base_url, args.timeout)

    try:
        user_keys = bootstrap_users(base_url, root_key, account_id, args.timeout)
        cases = [
            CaseSpec(
                name="policy_memory_types_unset",
                user_id=DEFAULT_POLICY_USER,
                session_id=f"mp_unset_{run_id}",
                api_key=user_keys[DEFAULT_POLICY_USER],
                memory_policy=DEFAULT_POLICY,
                allowed_types=None,
                required_any_types=FILTERED_ALLOWED_TYPES | {"events", "tools", "experiences"},
            ),
            CaseSpec(
                name="policy_filtered",
                user_id=FILTERED_POLICY_USER,
                session_id=f"mp_filtered_{run_id}",
                api_key=user_keys[FILTERED_POLICY_USER],
                memory_policy=FILTERED_POLICY,
                allowed_types=FILTERED_ALLOWED_TYPES,
                required_any_types=FILTERED_ALLOWED_TYPES,
            ),
        ]

        results = [run_case(base_url, case, args.timeout) for case in cases]
        default_types = extracted_memory_types(results[0].memory_types)
        filtered_types = extracted_memory_types(results[1].memory_types)
        default_only_types = default_types - filtered_types
        if not default_only_types:
            results[1].errors.append(
                "no observable type difference between unset memory_types and filtered policy"
            )

        print_json(
            "comparison",
            {
                "default_only_types": sorted(default_only_types),
                "filtered_allowed_types": sorted(FILTERED_ALLOWED_TYPES),
                "cases": [result_summary(result) for result in results],
            },
        )

        if all(result.ok for result in results):
            print(
                "\nPASS: unset memory_types and explicit memory_types whitelist produced "
                "different self/peer memory type sets."
            )
            return 0

        print("\nFAIL: memory_policy comparison did not match expectations.", file=sys.stderr)
        return 1
    finally:
        if server_process is not None and not args.leave_server_running:
            server_process.terminate()
            try:
                server_process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                server_process.kill()


if __name__ == "__main__":
    raise SystemExit(main())
