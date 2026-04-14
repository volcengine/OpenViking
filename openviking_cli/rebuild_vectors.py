# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""CLI entry point for rebuilding vectors after embedding changes."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Sequence

from openviking.service.core import OpenVikingService
from openviking.storage.embedding_compat import persist_embedding_metadata
from openviking_cli.utils.config.open_viking_config import OpenVikingConfigSingleton


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rebuild OpenViking vectors after an embedding configuration change.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to ov.conf config file",
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help="Only rebuild the specified account_id (repeatable)",
    )
    parser.add_argument(
        "--all-accounts",
        action="store_true",
        help="Rebuild every discovered account in the workspace",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Optional queue wait timeout in seconds per account",
    )
    return parser


def _resolve_account_targets(
    explicit_accounts: Sequence[str],
    discovered_accounts: Sequence[str],
    default_account: str,
) -> list[str]:
    if explicit_accounts:
        return sorted(set(explicit_accounts))
    if discovered_accounts:
        return list(discovered_accounts)
    return [default_account]


async def _run(args: argparse.Namespace) -> int:
    if args.config:
        os.environ["OPENVIKING_CONFIG_FILE"] = args.config

    OpenVikingConfigSingleton.reset_instance()

    service = OpenVikingService(skip_embedding_compat_check=True)
    try:
        await service.initialize()
        rebuilder = service.create_vector_rebuild_service()
        discovered_accounts = (
            await rebuilder.discover_accounts() if args.all_accounts or not args.account else []
        )
        target_accounts = _resolve_account_targets(
            explicit_accounts=args.account,
            discovered_accounts=discovered_accounts,
            default_account=service.config.default_account,
        )

        if not target_accounts:
            print("No accounts found to rebuild.", file=sys.stderr)
            return 1

        print("Rebuilding vectors for accounts:", ", ".join(target_accounts))
        reports = await rebuilder.rebuild_accounts(target_accounts, wait_timeout=args.timeout)
        meta_path = persist_embedding_metadata(service.config)

        print("")
        for report in reports:
            print(
                f"[{report.account_id}] deleted_records={report.deleted_records} "
                f"indexed_directories={report.indexed_directories}"
            )
            if report.queue_status:
                for queue_name, queue_data in report.queue_status.items():
                    processed = queue_data.get("processed", 0)
                    requeues = queue_data.get("requeue_count", 0)
                    errors = queue_data.get("error_count", 0)
                    print(
                        f"  - {queue_name}: processed={processed} "
                        f"requeue_count={requeues} error_count={errors}"
                    )

        if meta_path is not None:
            print("")
            print(f"Updated embedding metadata: {meta_path}")
        print("Vector rebuild completed.")
        return 0
    finally:
        try:
            await service.close()
        finally:
            OpenVikingConfigSingleton.reset_instance()


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
