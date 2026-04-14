#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Migrate existing OpenClaw memory files and transcripts into OpenViking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import openviking as ov
from openviking.migration.openclaw import migrate_openclaw


def _build_client(args: argparse.Namespace) -> Any:
    if args.ov_path:
        client = ov.SyncOpenViking(path=args.ov_path)
        client.initialize()
        return client

    client = ov.SyncHTTPClient(
        url=args.url,
        api_key=args.api_key,
        account=args.account,
        user=args.user,
        agent_id=args.agent_id,
        timeout=args.http_timeout,
    )
    client.initialize()
    return client


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--openclaw-dir",
        default=str(Path.home() / ".openclaw"),
        help="OpenClaw state directory (default: ~/.openclaw)",
    )
    parser.add_argument(
        "--mode",
        choices=("memory", "transcript", "all"),
        default="memory",
        help="What to migrate",
    )
    parser.add_argument(
        "--agent",
        action="append",
        dest="agent_ids",
        help="Only replay transcripts for the given OpenClaw agent id (repeatable)",
    )
    parser.add_argument(
        "--category-override",
        choices=("preferences", "entities", "events", "cases", "patterns", "tools", "skills"),
        help="Override the inferred OpenViking memory category for native OpenClaw memory files",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview planned imports only")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing OV targets")
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for async queue/task completion")
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Queue/task wait timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Task poll interval in seconds (default: 1)",
    )
    parser.add_argument(
        "--url",
        help="OpenViking server URL. If omitted, SyncHTTPClient falls back to ovcli.conf",
    )
    parser.add_argument("--api-key", help="Optional OpenViking API key")
    parser.add_argument("--account", help="Optional X-OpenViking-Account header")
    parser.add_argument("--user", help="Optional X-OpenViking-User header")
    parser.add_argument("--agent-id", help="Optional X-OpenViking-Agent header")
    parser.add_argument(
        "--http-timeout",
        type=float,
        default=60.0,
        help="HTTP client timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--ov-path",
        help="Use embedded SyncOpenViking against a local data path instead of HTTP mode",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if args.ov_path and any([args.url, args.api_key, args.account, args.user, args.agent_id]):
        raise SystemExit("--ov-path cannot be combined with HTTP connection flags")

    client = _build_client(args)
    try:
        result = migrate_openclaw(
            client,
            args.openclaw_dir,
            mode=args.mode,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
            wait=not args.no_wait,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            agent_ids=args.agent_ids,
            category_override=args.category_override,
        )
    finally:
        client.close()

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
