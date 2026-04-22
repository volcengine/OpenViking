#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""End-to-end smoke for the memory consolidation engine (Phase A).

Runs MemoryConsolidator.run() against an existing local OV instance for a
named scope, in dry-run mode by default. Proves the full phase chain
fires end-to-end and the audit record lands at the expected URI.

Usage:
    uv run python examples/memory-consolidation/consolidate_smoke.py
    uv run python examples/memory-consolidation/consolidate_smoke.py --apply
    uv run python examples/memory-consolidation/consolidate_smoke.py \\
        --scope "viking://agent/brianle/memories/patterns/"

This script does NOT seed test data. It runs against whatever is currently
under the scope. With no clusters present (most likely), the consolidator
exercises orient -> gather (empty) -> archive (empty or actual cold ones)
-> reindex (skipped) -> record. The audit record is the proof of life.
"""

import argparse
import asyncio
import sys
from pathlib import Path


def _bootstrap_path() -> None:
    """Make the local checkout importable without install."""
    here = Path(__file__).resolve()
    sys.path.insert(0, str(here.parents[2]))


_bootstrap_path()


async def _run(scope_uri: str, apply: bool, data_path: str, seed: bool = False) -> int:
    from openviking.async_client import AsyncOpenViking  # noqa: F401
    from openviking.maintenance import MemoryConsolidator
    from openviking.server.identity import RequestContext, Role, UserIdentifier
    from openviking.session.memory_archiver import MemoryArchiver
    from openviking.session.memory_deduplicator import MemoryDeduplicator
    from openviking.storage import VikingDBManagerProxy

    # Embedded async client -- in-process service, no external openviking
    # server required. data_path holds OV state for this run.
    client = AsyncOpenViking(path=data_path)
    await client.initialize()
    try:
        service = client._client._service
        viking_fs = service.viking_fs

        user = UserIdentifier(
            account_id="brianle",
            user_id="brianle",
            agent_id="memory_consolidator",
        )
        ctx = RequestContext(user=user, role=Role.ROOT)

        vikingdb = VikingDBManagerProxy(service.vikingdb_manager, ctx)

        dedup = MemoryDeduplicator(vikingdb)
        archiver = MemoryArchiver(viking_fs=viking_fs, storage=vikingdb)
        consolidator = MemoryConsolidator(
            vikingdb=vikingdb,
            viking_fs=viking_fs,
            dedup=dedup,
            archiver=archiver,
            service=None,  # skip reindex phase in smoke
        )

        if seed:
            print("Seeding scope with 3 deliberately-similar memories...")
            seeded = await _seed(viking_fs, scope_uri, ctx)
            print("Triggering build_index on the scope...")
            try:
                await service.resources.build_index([scope_uri], ctx=ctx)
            except Exception as e:
                print(f"  build_index error: {e}")
            print(f"Waiting up to 30s for embeddings on {len(seeded)} files...")
            await _wait_for_index(vikingdb, scope_uri, expected_count=len(seeded))
            print()

        return await _execute(consolidator, scope_uri, apply, viking_fs, ctx)
    finally:
        await client.close()

async def _seed(viking_fs, scope_uri: str, ctx) -> list[str]:
    """Write 3 deliberately-similar memory files under the scope.

    Returns the list of seeded URIs. Caller waits on embedding before
    running the consolidator.
    """
    base = scope_uri.rstrip("/")
    try:
        await viking_fs.mkdir(base, ctx=ctx, exist_ok=True)
    except Exception:
        pass

    seeds = {
        f"{base}/dup_alpha.md": (
            "# bun build for TypeScript errors\n\n"
            "When working in a Next.js project, run `bun run build` to "
            "surface TypeScript errors that the dev server suppresses.\n"
        ),
        f"{base}/dup_beta.md": (
            "# Use bun run build to find TS errors\n\n"
            "In Next.js apps, `bun run build` is the fastest way to see "
            "TypeScript errors that the dev server hides.\n"
        ),
        f"{base}/dup_gamma.md": (
            "# Surface TypeScript errors via bun build\n\n"
            "For Next.js projects, run `bun run build` to find TypeScript "
            "errors the dev server doesn't surface.\n"
        ),
    }

    for uri, body in seeds.items():
        await viking_fs.write(uri, body, ctx=ctx)
        print(f"  seeded {uri}")
    return list(seeds.keys())


async def _wait_for_index(vikingdb, scope_uri: str, expected_count: int, timeout_s: float = 30.0):
    """Poll the vector index until expected_count L2 entries are visible."""
    import time as _t
    from openviking.storage.expr import And, Eq

    deadline = _t.monotonic() + timeout_s
    found = 0
    while _t.monotonic() < deadline:
        try:
            records, _ = await vikingdb.scroll(
                filter=And(conds=[Eq("level", 2)]),
                limit=200,
                cursor=None,
                output_fields=["uri"],
            )
            found = sum(1 for r in records if r.get("uri", "").startswith(scope_uri))
            if found >= expected_count:
                print(f"  index has {found} entries under scope")
                return
        except Exception as e:
            print(f"  scroll error: {e}")
        await asyncio.sleep(1.0)
    print(f"  timeout after {timeout_s}s; index has {found}/{expected_count}")


async def _execute(consolidator, scope_uri, apply, viking_fs, ctx):
    print(f"scope:   {scope_uri}")
    print(f"mode:    {'APPLY' if apply else 'DRY-RUN'}")
    print(f"account: {ctx.account_id}")
    print()

    result = await consolidator.run(scope_uri, ctx, dry_run=not apply)

    print("=" * 60)
    print("Result")
    print("=" * 60)
    print(f"started:    {result.started_at}")
    print(f"completed:  {result.completed_at}")
    print(f"partial:    {result.partial}")
    print(f"errors:     {result.errors}")
    print(f"phases:     {result.phase_durations}")
    print(f"candidates: {result.candidates}")
    print(f"applied:    {result.ops_applied}")
    print(f"audit:      {result.audit_uri}")
    print()

    if result.cluster_decisions:
        print("Cluster decisions:")
        for d in result.cluster_decisions:
            print(f"  - {d}")
        print()

    if result.audit_uri:
        try:
            audit = await viking_fs.read(result.audit_uri, ctx=ctx)
            if isinstance(audit, bytes):
                audit = audit.decode("utf-8", errors="replace")
            print("Audit record (first 400 chars):")
            print(audit[:400])
        except Exception as e:
            print(f"audit read failed: {e}")

    return 1 if result.partial else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenViking consolidation smoke")
    parser.add_argument(
        "--scope",
        default="viking://agent/brianle/memories/patterns/",
        help="Scope URI to consolidate",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply ops (default is dry-run)",
    )
    parser.add_argument(
        "--data-path",
        default="/tmp/ov-consolidate-smoke",
        help="Embedded OV data dir for the smoke (created if missing)",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed 3 deliberately-similar memory files under the scope before running",
    )
    args = parser.parse_args()

    return asyncio.run(_run(args.scope, args.apply, args.data_path, args.seed))


if __name__ == "__main__":
    sys.exit(main())
