#!/usr/bin/env python3
"""
Traverse a folder for files matching task_{id}_{epoch}_{try}_messages.json
and count how many have assistant tool_calls invoking tools whose name
starts with "openviking_".
"""

import argparse
import json
import re
from pathlib import Path


def iter_matching_files(root: Path, epoch: int, trial: int):
    pattern = re.compile(rf"^task_\d+_{epoch}_{trial}_messages\.json$")
    for p in root.rglob("task_*_messages.json"):
        if p.is_file() and pattern.match(p.name):
            yield p


def file_has_openviking_tool_call(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False

    if not isinstance(data, list):
        return False

    for msg in data:
        if not isinstance(msg, dict):
            continue

        role = msg.get("role")

        # Assistant tool_calls
        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            if isinstance(tool_calls, list):
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    if tc.get("type") != "function":
                        continue
                    fn = tc.get("function")
                    if not isinstance(fn, dict):
                        continue
                    name = fn.get("name")
                    if isinstance(name, str) and name.startswith("openviking_"):
                        return True

        # Tool role messages (tool results) with name field
        if role == "tool":
            name = msg.get("name")
            if isinstance(name, str) and name.startswith("openviking_"):
                return True
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Count files whose assistant tool_calls contain openviking_ tools."
    )
    parser.add_argument("--root", required=True, help="Root directory to traverse")
    parser.add_argument("--epoch", type=int, required=True, help="Epoch value")
    parser.add_argument("--try", dest="trial", type=int, required=True, help="Try value")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        raise SystemExit(f"Root path not found: {root}")

    matched_files = list(iter_matching_files(root, args.epoch, args.trial))
    print(len(matched_files))
    count = 0
    for path in matched_files:
        if file_has_openviking_tool_call(path):
            count += 1

    print(count)


if __name__ == "__main__":
    main()
