#!/usr/bin/env python3
"""Import a file Watch store export into the MySQL Watch store.

The file store keeps Watches in ``viking://resources/.watch_tasks.json``.
Export that file (for example ``ov read viking://resources/.watch_tasks.json``),
stop every OpenViking server, then run this script with the ov.conf that selects
``storage.watch_store.provider = "mysql"``. Watches are imported idle, deleted
entries are dropped and existing task IDs are skipped, so the import can be rerun.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from openviking.resource.watch_types import WatchTask
from openviking_cli.exceptions import ConflictError
from openviking_cli.utils.config.consts import OPENVIKING_CONFIG_ENV


def _load_tasks(path: Path) -> list[WatchTask]:
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("input JSON must be an object with a tasks array")
    tasks = data.get("tasks", [])
    if not isinstance(tasks, list):
        raise ValueError("input JSON field 'tasks' must be an array")
    return [
        WatchTask.from_dict(item)
        for item in tasks
        if isinstance(item, dict) and not item.get("deleted")
    ]


async def _run(args: argparse.Namespace) -> None:
    if args.config:
        os.environ[OPENVIKING_CONFIG_ENV] = str(args.config)
    from openviking_cli.utils.config import get_openviking_config

    config = get_openviking_config()
    if config.storage.watch_store.provider != "mysql":
        raise SystemExit(
            "storage.watch_store.provider must be 'mysql'; the file store reads the file itself"
        )
    tasks = [task for task in _load_tasks(args.input) if task.to_uri]
    target = f"{config.storage.watch_store.provider} service_name={config.service_name!r}"
    if args.dry_run:
        print(f"Would import {len(tasks)} watch task(s) into {target}")
        return
    store = config.storage.build_watch_store(service_name=config.service_name, viking_fs=None)
    await store.initialize()
    imported = skipped = 0
    try:
        for task in tasks:
            try:
                await store.create_task(task)
            except ConflictError as exc:
                skipped += 1
                print(f"Skipped {task.task_id}: {exc}")
            else:
                imported += 1
    finally:
        await store.close()
    print(f"Imported {imported} watch task(s) into {target}; skipped {skipped}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import a .watch_tasks.json export into the MySQL Watch store."
    )
    parser.add_argument("--input", type=Path, required=True, help="Path to .watch_tasks.json")
    parser.add_argument("--config", type=Path, help="ov.conf of the target deployment")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    main()
