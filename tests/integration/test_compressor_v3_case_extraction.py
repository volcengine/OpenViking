#!/usr/bin/env python3
"""
OpenViking V3 cases memory live integration test.

This follows the style of ``test_compressor_v2_xiaomei.py``: it connects to a
running local OpenViking HTTP service, writes dialogue via add_message, commits
the session, and verifies that V3 extracts a trainable ``cases`` memory.

Prerequisites for a full pass:
- OpenViking server is running locally (default: http://localhost:1933)
- Server config uses memory.version = "v3"
- Server has a usable VLM/embedding configuration for memory extraction
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Any

import httpx
import pytest
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import openviking as ov

try:
    from openviking_live_auth import API_KEY_HELP, resolve_api_key
except ModuleNotFoundError:  # pytest/package import path
    from tests.integration.openviking_live_auth import API_KEY_HELP, resolve_api_key

# ── Constants ────────────────────────────────────────────────────────────────

DISPLAY_NAME = "V3 Case Extraction"
DEFAULT_URL = "http://localhost:1933"
DEFAULT_API_KEY = None
DEFAULT_SESSION_ID = "v3-case-extraction-demo"
PANEL_WIDTH = 90
TASK_POLL_INTERVAL_SECONDS = 1.0
TASK_TIMEOUT_SECONDS = 240.0

console = Console()

# ── Dialogue that should trigger cases extraction ───────────────────────────

CONVERSATION = [
    {
        "user": "请帮我处理酒店重复预订，只取消确认是重复的那一单，保留有效订单。",
        "assistant": (
            "我先核对两个预订候选：订单 A 是用户主动确认保留的有效订单；订单 B 的入住日期、"
            "房型、入住人都与 A 相同，并且系统标记为 duplicate_candidate。"
        ),
    },
    {
        "user": "对，只能取消重复的那个，别影响我真正要住的订单。",
        "assistant": (
            "已确认 B 是重复订单，A 是有效订单。我只取消订单 B，并保留订单 A。"
            "取消后我再次检查，A 仍为 confirmed，B 已为 cancelled。"
        ),
    },
]

VERIFY_KEYWORDS = ["重复", "预订", "取消", "保留", "订单"]
CASES_DIR = "viking://user/default/memories/cases"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _log(message: str) -> None:
    console.print(f"[cyan][v3-case-extraction][/cyan] {message}")



def _local_server_available(url: str = DEFAULT_URL) -> bool:
    try:
        response = httpx.get(f"{url.rstrip('/')}/health", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


def _wait_for_task(client: ov.SyncHTTPClient, task_id: str, timeout: float) -> dict[str, Any]:
    started = time.time()
    while time.time() - started < timeout:
        task = client.get_task(task_id)
        status = task.get("status") if task else "not_found"
        _log(f"poll task: task_id={task_id} status={status}")
        if task and status in ("completed", "failed"):
            elapsed = time.time() - started
            _log(f"task finished: status={status} elapsed={elapsed:.2f}s result={task.get('result')}")
            return task
        time.sleep(TASK_POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Task {task_id} did not finish within {timeout}s")


def _entry_uri(entry: Any, parent: str) -> str:
    if isinstance(entry, dict):
        uri = entry.get("uri")
        if uri:
            return str(uri)
        name = entry.get("name")
        if name:
            return f"{parent.rstrip('/')}/{name}"
    uri = getattr(entry, "uri", None)
    if uri:
        return str(uri)
    name = getattr(entry, "name", None)
    if name:
        return f"{parent.rstrip('/')}/{name}"
    return str(entry)



def _read_memory_diff(client: ov.SyncHTTPClient, archive_uri: str | None) -> dict[str, Any]:
    if not archive_uri:
        return {}
    diff_uri = f"{archive_uri.rstrip('/')}/memory_diff.json"
    try:
        raw = client.read(diff_uri)
        data = json.loads(raw)
        ops = data.get("operations") or {}
        for kind in ("adds", "updates", "deletes"):
            for item in ops.get(kind, []) or []:
                _log(
                    "memory_diff "
                    f"{kind[:-1] if kind.endswith('s') else kind}: "
                    f"type={item.get('memory_type')} uri={item.get('uri')}"
                )
        return data
    except Exception as exc:
        _log(f"memory_diff not readable: uri={diff_uri} error={exc}")
        return {}


def _case_entries_from_memory_diff(diff: dict[str, Any]) -> list[str]:
    entries: list[str] = []
    operations = diff.get("operations") or {}
    for kind in ("adds", "updates"):
        for item in operations.get(kind, []) or []:
            if item.get("memory_type") == "cases" and item.get("uri"):
                entries.append(str(item["uri"]))
    return entries


def _read_case_memories(client: ov.SyncHTTPClient) -> list[tuple[str, str]]:
    try:
        entries = client.ls(CASES_DIR)
    except Exception as exc:
        _log(f"cases dir not readable yet: {CASES_DIR} error={exc}")
        return []

    memories: list[tuple[str, str]] = []
    for entry in entries or []:
        uri = _entry_uri(entry, CASES_DIR)
        if not uri.endswith(".md") or uri.endswith("/.overview.md") or uri.endswith("/.abstract.md"):
            continue
        try:
            content = client.read(uri)
        except Exception as exc:
            _log(f"skip unreadable case memory: uri={uri} error={exc}")
            continue
        memories.append((uri, content))
    return memories


# ── Phase 1: ingest dialogue and commit session ─────────────────────────────


def run_ingest(
    client: ov.SyncHTTPClient,
    session_id: str = DEFAULT_SESSION_ID,
    *,
    wait_processed: bool = True,
    task_timeout: float = TASK_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    console.rule(f"[bold]Phase 1: 写入对话并提交 Session — {DISPLAY_NAME}[/bold]")

    create_result = client.create_session(session_id=session_id)
    session_id = create_result.get("session_id", session_id)
    _log(f"session created: session_id={session_id}")

    session_time = datetime(2026, 6, 7, 9, 30)
    session_time_str = session_time.isoformat()

    for index, turn in enumerate(CONVERSATION, 1):
        _log(f"add dialogue turn {index}/{len(CONVERSATION)}")
        client.add_message(
            session_id,
            role="user",
            parts=[{"type": "text", "text": turn["user"]}],
            created_at=session_time_str,
        )
        client.add_message(
            session_id,
            role="assistant",
            parts=[{"type": "text", "text": turn["assistant"]}],
            created_at=session_time_str,
        )

    _log(f"added messages: count={len(CONVERSATION) * 2}")
    _log("commit session: trigger archive + V3 long-term extraction")
    commit_result = client.commit_session(
        session_id,
        memory_policy={"self": {"enabled": True}, "peer": {"enabled": False}},
    )
    _log(
        "commit accepted: "
        f"task_id={commit_result.get('task_id')} archive_uri={commit_result.get('archive_uri')} "
        f"trace_id={commit_result.get('trace_id')}"
    )

    task = None
    task_id = commit_result.get("task_id")
    if task_id:
        task = _wait_for_task(client, task_id, task_timeout)
        if task.get("status") == "failed":
            raise AssertionError(f"session commit task failed: {task}")

    if wait_processed:
        _log("wait_processed: drain vectorization/semantic queues")
        client.wait_processed(timeout=task_timeout)

    session_info = client.get_session(session_id)
    _log(f"session info: {session_info}")
    return {"session_id": session_id, "commit": commit_result, "task": task}


# ── Phase 2: verify cases memory ────────────────────────────────────────────


def run_verify(client: ov.SyncHTTPClient, archive_uri: str | None = None) -> list[tuple[str, str]]:
    console.rule(f"[bold]Phase 2: 验证 cases 记忆写入 — {DISPLAY_NAME}[/bold]")

    diff = _read_memory_diff(client, archive_uri)
    diff_case_uris = _case_entries_from_memory_diff(diff)
    memories = _read_case_memories(client)
    table = Table(title="V3 cases memories", show_header=True, header_style="bold")
    table.add_column("#", width=4)
    table.add_column("URI", style="cyan", max_width=56)
    table.add_column("命中关键词", style="green")
    table.add_column("片段", max_width=80)

    for index, (uri, content) in enumerate(memories, 1):
        hits = [keyword for keyword in VERIFY_KEYWORDS if keyword in content]
        snippet = content.replace("\n", " ")[:160]
        table.add_row(str(index), uri, ", ".join(hits), snippet)
        _log(f"case memory: uri={uri} chars={len(content)} hits={hits}")

    console.print(table)

    matching = [
        (uri, content)
        for uri, content in memories
        if "重复" in content and "预订" in content and ("取消" in content or "保留" in content)
    ]
    if not matching and diff_case_uris:
        # Directory listing can lag/shape-shift across deployments; if memory_diff says
        # cases were written, read those URIs directly.
        for uri in diff_case_uris:
            try:
                content = client.read(uri)
            except Exception as exc:
                _log(f"case uri from memory_diff unreadable: uri={uri} error={exc}")
                continue
            if "重复" in content and "预订" in content and ("取消" in content or "保留" in content):
                matching.append((uri, content))

    if not matching:
        diff_types = []
        operations = diff.get("operations") or {}
        for kind in ("adds", "updates", "deletes"):
            diff_types.extend(
                str(item.get("memory_type"))
                for item in operations.get(kind, []) or []
                if item.get("memory_type")
            )
        raise AssertionError(
            "No V3 cases memory matched duplicate-booking evidence. "
            f"cases_dir={CASES_DIR} memory_count={len(memories)} "
            f"memory_diff_types={diff_types}"
        )
    return matching


# ── Pytest entry: requires local server ─────────────────────────────────────


@pytest.mark.integration
@pytest.mark.skipif(
    not _local_server_available(DEFAULT_URL),
    reason=f"OpenViking local server is not running at {DEFAULT_URL}",
)
def test_local_service_add_dialogue_commit_triggers_v3_case_extraction():
    client = ov.SyncHTTPClient(url=DEFAULT_URL, api_key=resolve_api_key(), timeout=300)
    try:
        client.initialize()
        ingest = run_ingest(client, session_id=f"{DEFAULT_SESSION_ID}-{int(time.time())}")
        matches = run_verify(client, archive_uri=ingest["commit"].get("archive_uri"))
        assert matches
    finally:
        client.close()


# ── CLI entry, like test_compressor_v2_xiaomei.py ───────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=f"OpenViking V3 cases live test — {DISPLAY_NAME}")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Server URL (default: {DEFAULT_URL})")
    parser.add_argument(
        "--api-key",
        default=None,
        help=API_KEY_HELP,
    )
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID, help="Session ID")
    parser.add_argument(
        "--phase",
        choices=["all", "ingest", "verify"],
        default="all",
        help="all=ingest+verify, ingest=only submit session, verify=only read cases",
    )
    parser.add_argument("--timeout", type=float, default=TASK_TIMEOUT_SECONDS, help="Task timeout")
    args = parser.parse_args()

    console.print(
        Panel(
            f"[bold]OpenViking V3 cases live test — {DISPLAY_NAME}[/bold]\n"
            f"Server: {args.url} | Phase: {args.phase}",
            style="magenta",
            width=PANEL_WIDTH,
        )
    )

    api_key = resolve_api_key(args.api_key)
    if api_key:
        _log("api key resolved from --api-key/env/ov.conf (not printed)")
    else:
        _log("no api key resolved; relying on SyncHTTPClient ovcli.conf auto-load")
    client = ov.SyncHTTPClient(url=args.url, api_key=api_key, timeout=max(args.timeout, 60))
    try:
        client.initialize()
        _log(f"connected: {args.url}")
        ingest = None
        if args.phase in ("all", "ingest"):
            ingest = run_ingest(client, session_id=args.session_id, task_timeout=args.timeout)
        if args.phase in ("all", "verify"):
            archive_uri = ingest["commit"].get("archive_uri") if ingest else None
            run_verify(client, archive_uri=archive_uri)
        console.print(Panel("[bold green]V3 cases live test completed[/bold green]", style="green"))
    except Exception as exc:
        console.print(Panel(f"[bold red]Error:[/bold red] {exc}", style="red"))
        raise
    finally:
        client.close()


if __name__ == "__main__":
    main()
