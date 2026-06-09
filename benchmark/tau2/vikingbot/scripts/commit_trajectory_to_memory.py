#!/usr/bin/env python3
"""Commit runner trajectories into OpenViking memory.

This script reads trajectory JSON files produced by vikingbot_tau2_runner.py
and commits a minimal conversation (user -> assistant) into OpenViking.

Usage:
  python3 commit_trajectory_to_memory.py --input /path/to/result_dir
  python3 commit_trajectory_to_memory.py --input /path/to/file.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Iterable

from vikingbot.config.loader import ensure_config
from vikingbot.openviking_mount.ov_server import VikingClient


def _iter_files(root: Path, pattern: str) -> Iterable[Path]:
    if root.is_file():
        yield root
        return
    for path in sorted(root.glob(pattern)):
        if path.is_file():
            yield path


def _build_messages(
    data: dict[str, Any],
    include_eval_result: bool,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []

    system_prompt = data.get("system_prompt") or ""
    if system_prompt:
        messages.append({"role": "user", "content": f"system:\n{system_prompt}"})

    user_text = data.get("user_prompt") or data.get("user_query") or ""
    if user_text:
        messages.append({"role": "user", "content": user_text})

    tools_used = data.get("tools_used") or []
    if isinstance(tools_used, list):
        for tool_info in tools_used:
            if not isinstance(tool_info, dict):
                continue
            tool_name = tool_info.get("tool_name", "")
            args = tool_info.get("args", "")
            assistant_content = tool_name
            if args:
                assistant_content = f"tool-call: \n name: {tool_name}\n arguments: {args}"
            if assistant_content:
                messages.append({"role": "assistant", "content": assistant_content})

            result = tool_info.get("result")
            if result is not None:
                messages.append({"role": "user", "content": f"tool-response: \n{result}"})
    messages.append({"role": "assistant", "content": data.get("final_content", "")})
    reward = data.get("reward")
    success = bool(reward == 1)
    result = f"task_success: {success}"
    if include_eval_result and data.get("evaluation_result") is not None:
        evaluation_result = data.get("evaluation_result")
        if not isinstance(evaluation_result, str):
            evaluation_result = json.dumps(evaluation_result, ensure_ascii=False)
        result += f"\n evaluation report: {evaluation_result}"
    messages.append({"role": "user", "content": result})
    return messages


def _get_session_id(data: dict[str, Any], prefix: str | None) -> str | None:
    session_id = data.get("session_id")
    if session_id:
        return f"{prefix}{session_id}" if prefix else session_id
    data_split = data.get("data_split")
    task_no = data.get("task_no")
    if data_split is not None and task_no is not None:
        base = f"tau2_{data_split}_{task_no}"
        return f"{prefix}{base}" if prefix else base
    return None


async def _commit_single(
    client: VikingClient,
    path: Path,
    user_id: str,
    session_prefix: str | None,
    dry_run: bool,
    only_wrong: bool,
    include_eval_result: bool,
) -> tuple[Path, bool, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return path, False, f"Failed to load JSON: {exc}"
    if only_wrong and data.get("reward") == 1:
        return path, False, "Skipped: reward == 1"
    messages = _build_messages(data, include_eval_result)
    if not messages:
        return path, False, "No messages to commit"

    session_id = _get_session_id(data, session_prefix)
    if not session_id:
        return path, False, "Missing session_id/data_split/task_no"

    if dry_run:
        print(messages)
        return path, True, f"DRY RUN: would commit {len(messages)} messages to {session_id}"
    await client.commit(session_id, messages, user_id)

    return path, True, f"Committed {len(messages)} messages to {session_id}"


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Commit runner trajectories to OpenViking")
    parser.add_argument("--input", required=True, help="Trajectory file or directory")
    parser.add_argument(
        "--pattern",
        default="*.json",
        help="Glob pattern when input is a directory (default: *.json)",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="Optional explicit user id. Normally omitted when using a user-key benchmark config.",
    )
    parser.add_argument(
        "--session-prefix",
        default=None,
        help="Prefix to prepend to session_id",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="ov.conf path for the benchmark runtime user",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be committed",
    )
    parser.add_argument(
        "--only-wrong",
        action="store_true",
        help="Only commit trajectories with reward != 1",
    )
    parser.add_argument(
        "--include-eval-result",
        action="store_true",
        help="Include evaluation_result in the final user message",
    )
    args = parser.parse_args()

    root = Path(args.input).expanduser()
    if not root.exists():
        print(f"Input not found: {root}")
        return 1

    if args.config:
        ensure_config(Path(args.config).expanduser())

    client = await VikingClient.create()
    files = list(_iter_files(root, args.pattern))
    if not files:
        print("No files matched.")
        return 1

    ok = 0
    for path in files:
        _, success, msg = await _commit_single(
            client,
            path,
            user_id=args.user_id,
            session_prefix=args.session_prefix,
            dry_run=args.dry_run,
            only_wrong=args.only_wrong,
            include_eval_result=args.include_eval_result,
        )
        status = "OK" if success else "SKIP"
        print(f"[{status}] {path.name} - {msg}")
        if success:
            ok += 1

    print(f"Committed {ok}/{len(files)} files")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
