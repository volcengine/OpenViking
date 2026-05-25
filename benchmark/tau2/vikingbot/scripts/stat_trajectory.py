#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stat tool for trajectory json files.

Example:
  python scripts/stat_trajectory.py \
    --dir result/airline_test \
    --pattern "task_*_0_0_trajectory.json"
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Any, Dict, List, Tuple


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _extract_stats(data: Dict[str, Any]) -> Tuple[int, int]:
    """Return (tool_calls, total_tokens)."""
    tools_used = data.get("tools_used") or []
    tool_calls = len(tools_used) if isinstance(tools_used, list) else 0

    token_usage = data.get("token_usage") or {}
    if isinstance(token_usage, dict):
        total_tokens = _safe_int(token_usage.get("total_tokens"))
    else:
        total_tokens = 0
    return tool_calls, total_tokens


def main() -> int:
    parser = argparse.ArgumentParser(description="Stat trajectory json files.")
    parser.add_argument("--dir", required=True, help="Target directory")
    parser.add_argument(
        "--pattern",
        default="task_*_1_1_trajectory.json",
        help="Glob pattern of files (default: task_*_1_1_trajectory.json)",
    )
    args = parser.parse_args()

    root = os.path.abspath(args.dir)
    pattern = os.path.join(root, args.pattern)
    files = sorted(glob.glob(pattern))

    if not files:
        print(f"No files matched: {pattern}")
        return 1

    total_tool_calls = 0
    total_tokens = 0
    valid_files = 0
    bad_files: List[str] = []

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            tool_calls, tokens = _extract_stats(data)
            total_tool_calls += tool_calls
            total_tokens += tokens
            valid_files += 1
        except Exception:
            bad_files.append(path)

    if valid_files == 0:
        print("No valid json files found.")
        return 1

    avg_tool_calls = total_tool_calls / valid_files
    avg_tokens = total_tokens / valid_files

    print("Matched files:", len(files))
    print("Valid files:", valid_files)
    if bad_files:
        print("Invalid files:", len(bad_files))
        for p in bad_files:
            print("  -", p)
    print("Average tool calls:", round(avg_tool_calls, 2))
    print("Average total tokens:", round(avg_tokens, 2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
