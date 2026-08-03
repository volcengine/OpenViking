#!/usr/bin/env python3
"""Import real code repos through the shared Service layer with indexing enabled."""

from __future__ import annotations

import argparse
import asyncio
import os
import time

from openviking.server.identity import RequestContext, Role
from openviking.service.core import OpenVikingService
from openviking_cli.session.user_id import UserIdentifier

DEFAULT_SOURCE = os.path.expanduser("~/.openviking/data/benchmark/OpenViking-main")
BENCHMARK_PARENT = "viking://resources/benchmark/effectiveness"


async def main():
    parser = argparse.ArgumentParser(
        description="Step 1 (Effectiveness): Import real code repos (with indexing)"
    )
    parser.add_argument(
        "--source",
        default=DEFAULT_SOURCE,
        help=f"Local directory to import (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--parent",
        default=BENCHMARK_PARENT,
        help=f"Parent Viking URI (default: {BENCHMARK_PARENT})",
    )
    args = parser.parse_args()

    source = os.path.expanduser(args.source)
    if not os.path.isdir(source):
        print(f"ERROR: Source directory does not exist: {source}")
        return

    print("=" * 80)
    print("Step 1 (Effectiveness): Import Code Repos (with VLM/embedding)")
    print("=" * 80)
    print(f"  Source:   {source}")
    print(f"  Parent:   {args.parent}")
    print("  Indexing: ENABLED (build_index=True, summarize=True)")
    print()

    user = UserIdentifier.the_default_user()
    service = OpenVikingService(user=user)
    ctx = RequestContext(user=user, role=Role.USER)
    await service.initialize()

    t0 = time.monotonic()
    try:
        result = await service.resources.add_resource(
            path=source,
            ctx=ctx,
            parent=args.parent,
            reason="benchmark effectiveness",
            wait=True,
            create_parent=True,
            build_index=True,
            summarize=True,
        )
        elapsed = time.monotonic() - t0
        root_uri = result.get("root_uri", "?")
        print(f"OK ({elapsed:.1f}s) -> {root_uri}")
        print()
        print("Import completed successfully.")
        print("Next step: run step2_quality.py to evaluate retrieval quality")
    except Exception as exc:
        elapsed = time.monotonic() - t0
        print(f"FAILED ({elapsed:.1f}s): {exc}")
    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())
