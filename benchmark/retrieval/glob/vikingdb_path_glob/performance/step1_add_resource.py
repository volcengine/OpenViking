#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Step 1 (Performance): import the synthetic dataset (vectors_only).

Imports each package directory (``<bucket>/pkg_XXXX``) via the public HTTP SDK
using ``processing_mode="vectors_only"``.
A single import populates BOTH glob faces from one dataset: the on-disk AGFS
tree (used by ``engine=fs``) and the VikingDB collection ``uri`` records (used by
``engine=auto`` remote path_glob).

Import is resumable at package granularity (progress file). Re-run to continue.

Usage:
  python3 step1_add_resource.py
  python3 step1_add_resource.py --buckets scale_500 scale_1k   # subset only
  python3 step1_add_resource.py --buckets scale_10w --max-units 20
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# The public SDK ships under ``sdk/python`` in the repo; add it to the path the
# same way ``openviking_cli.client._http_compat`` does, so this script works
# from a source checkout without a separate ``pip install openviking-sdk``.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..")
)
_SDK_ROOT = os.path.join(_REPO_ROOT, "sdk", "python")
if os.path.isdir(os.path.join(_SDK_ROOT, "openviking_sdk")) and _SDK_ROOT not in sys.path:
    sys.path.insert(0, _SDK_ROOT)

from openviking_sdk import OpenVikingError, SyncHTTPClient  # noqa: E402

from vikingdb_path_glob.common import dataset  # noqa: E402

DEFAULT_OUTPUT = os.path.expanduser("~/.openviking/data/benchmark/glob_synthetic_v2")
PROGRESS_FILE = os.path.expanduser("~/.openviking/data/benchmark/.glob-import-progress")
BENCHMARK_PARENT = "viking://resources/benchmark/glob"


def progress_path(shard_index: int, shard_count: int) -> str:
    """Return the progress file for a shard.

    Each ``add_resource`` writes to the remote VikingDB face synchronously, so
    a single-process import of 1M files is dominated by network round-trips.
    The unit list is deterministic, so it can be split into ``shard_count``
    disjoint slices (unit i belongs to shard ``i % shard_count``) that run in
    parallel processes. Each shard appends to its own progress file to avoid
    write contention; ``--shard-count 1`` keeps the original single file.
    """
    if shard_count <= 1:
        return PROGRESS_FILE
    return f"{PROGRESS_FILE}.shard-{shard_index:02d}-of-{shard_count:02d}"


def load_progress(path: str) -> set[str]:
    """Load completed unit keys.

    Reads ``path`` plus every sibling shard progress file so a resumed run
    skips units finished by any earlier run, even if the shard count changed
    between runs. Combined with the idempotent (upsert) ``add_resource``, this
    makes the import safely resumable under any sharding.
    """
    import glob as _glob

    completed: set[str] = set()
    candidates = {path, PROGRESS_FILE}
    candidates.update(_glob.glob(f"{PROGRESS_FILE}.shard-*"))
    for candidate in candidates:
        if not os.path.exists(candidate):
            continue
        with open(candidate) as file:
            completed.update(line.strip() for line in file if line.strip())
    return completed


def save_progress(path: str, key: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as file:
        file.write(key + "\n")


def _import_units(files_root: str, bucket_name: str) -> list[str]:
    """Return bucket-relative subdir units to import, in deterministic order.

    The dataset lays each bucket out as a monorepo (services/libs/apps/docs/
    tools). A single ``add_resource`` per second-level directory (e.g.
    ``services/auth``) keeps each resumable unit a moderate size. A top-level
    directory that holds files directly (e.g. ``tools``) is itself one unit.
    """
    bucket_path = os.path.join(files_root, bucket_name)
    if not os.path.isdir(bucket_path):
        return []
    units: list[str] = []
    for top in sorted(os.listdir(bucket_path)):
        top_path = os.path.join(bucket_path, top)
        if not os.path.isdir(top_path):
            continue
        subdirs = sorted(
            name for name in os.listdir(top_path)
            if os.path.isdir(os.path.join(top_path, name))
        )
        if subdirs:
            units.extend(f"{top}/{sub}" for sub in subdirs)
        else:
            units.append(top)
    return units


def _mkdir_chain(client: SyncHTTPClient, uri: str) -> None:
    """mkdir every ancestor of ``uri`` under the benchmark parent, idempotently."""
    try:
        client.mkdir(uri=uri)
    except OpenVikingError as exc:
        # A pre-existing directory is fine: the server reports it as CONFLICT
        # ("already exists"); older builds used ALREADY_EXISTS.
        if exc.code not in ("ALREADY_EXISTS", "CONFLICT"):
            raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 1 (Performance): import dataset")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"default: {DEFAULT_OUTPUT}")
    parser.add_argument("--parent", default=BENCHMARK_PARENT, help=f"default: {BENCHMARK_PARENT}")
    parser.add_argument("--buckets", nargs="+", default=None, help="import only these buckets")
    parser.add_argument(
        "--max-units",
        type=int,
        default=None,
        help="import at most N pending units from the selected buckets, useful for staged validation",
    )
    parser.add_argument(
        "--shard-count", type=int, default=1,
        help="split units into N disjoint shards for parallel imports (default 1)",
    )
    parser.add_argument(
        "--shard-index", type=int, default=0,
        help="which shard this process imports (0-based, < shard-count)",
    )
    args = parser.parse_args()

    if args.shard_count < 1 or not (0 <= args.shard_index < args.shard_count):
        print("ERROR: require shard-count>=1 and 0<=shard-index<shard-count")
        return

    output = os.path.expanduser(args.output)
    files_root = os.path.join(output, "files")
    if not os.path.isdir(files_root):
        print(f"ERROR: dataset not found: {files_root}")
        print("Run step0_prepare_data.py first.")
        return

    index = dataset.load_manifest_index(output)
    bucket_names = [b["name"] for b in index["buckets"]]
    if args.buckets:
        bucket_names = [name for name in bucket_names if name in set(args.buckets)]

    # Build the resumable unit list: one entry per second-level subdir (or a
    # top-level dir that holds files directly).
    units: list[tuple[str, str]] = []
    for bucket_name in bucket_names:
        for rel in _import_units(files_root, bucket_name):
            units.append((bucket_name, rel))

    # Keep only this shard's slice (deterministic round-robin over the ordered
    # unit list, so shards are disjoint and cover every unit exactly once).
    if args.shard_count > 1:
        units = [u for i, u in enumerate(units) if i % args.shard_count == args.shard_index]

    prog_file = progress_path(args.shard_index, args.shard_count)
    completed = load_progress(prog_file)
    pending_units = [u for u in units if f"{u[0]}/{u[1]}" not in completed]
    if args.max_units is not None:
        if args.max_units < 1:
            print("ERROR: require max-units>=1 when provided")
            return
        pending_units = pending_units[: args.max_units]

    total = len(pending_units)
    print("=" * 80)
    print("Step 1 (Performance): Import Dataset (vectors_only)")
    print("=" * 80)
    print(f"  Files root: {files_root}")
    print(f"  Parent:     {args.parent}")
    if args.shard_count > 1:
        print(f"  Shard:      {args.shard_index}/{args.shard_count}")
    print(f"  Selected:   {len(units)} units")
    print(f"  Pending:    {len(pending_units)} units")
    if args.max_units is not None:
        print(f"  Max units:  {args.max_units}")
    print(f"  Progress:   {prog_file}")
    print()
    if total == 0:
        print("Nothing to import.")
        return

    client = SyncHTTPClient(timeout=3600)
    client.initialize()

    ok = failed = skipped = 0
    t0 = time.monotonic()
    try:
        for idx, (bucket_name, rel) in enumerate(pending_units, 1):
            key = f"{bucket_name}/{rel}"

            # parent mirrors the on-disk layout: place ``<rel>`` under the URI
            # ``.../glob/<bucket>/<dirname(rel)>`` (dirname is "" for tools).
            rel_parent = os.path.dirname(rel)
            parent_uri = f"{args.parent}/{bucket_name}"
            if rel_parent:
                parent_uri = f"{parent_uri}/{rel_parent}"
            local_dir = os.path.join(files_root, bucket_name, rel)
            try:
                _mkdir_chain(client, f"{args.parent}/{bucket_name}")
                if rel_parent:
                    _mkdir_chain(client, parent_uri)
                client.add_resource(
                    path=local_dir,
                    parent=parent_uri,
                    wait=True,
                    options={
                        "processing_mode": "vectors_only",
                    },
                )
                save_progress(prog_file, key)
                ok += 1
            except Exception as exc:  # noqa: BLE001 - report and continue
                failed += 1
                print(f"  FAILED {key}: {str(exc)[:300]}")

            if idx % 50 == 0 or idx == total:
                elapsed = time.monotonic() - t0
                print(
                    f"  [{idx}/{total}] ok={ok} failed={failed} skipped={skipped}  "
                    f"({elapsed:.0f}s)"
                )
    finally:
        client.close()

    print()
    print(f"Summary: ok={ok} failed={failed} skipped={skipped} total={total}")
    if failed:
        print("Re-run to resume the failed/remaining packages.")
    else:
        print("Next: run step2_verify.py to confirm both glob faces are populated.")


if __name__ == "__main__":
    main()
