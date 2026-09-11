#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Step 0 (Performance): generate the synthetic path_glob dataset.

Creates a single tree of one-byte files laid out like a multi-level source
repo, split into disjoint scale buckets (see ``common/dataset.py``), and writes
a manifest that serves as ground truth for effectiveness scoring.

Glob matches on path only, so files carry a single filler byte (empty files are
skipped by the importer). The full dataset is 1,000,000 files but only ~1 MB of
content.

Usage:
  python3 step0_prepare_data.py                 # full 1,000,000-file dataset
  python3 step0_prepare_data.py --smoke         # tiny dataset for a dry run
  python3 step0_prepare_data.py --seed 7        # different deterministic layout
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Make the sibling ``common`` package importable when run as a script.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from vikingdb_path_glob.common import dataset  # noqa: E402

DEFAULT_OUTPUT = os.path.expanduser("~/.openviking/data/benchmark/glob_synthetic_v2")

# Smoke mode shrinks every bucket by this divisor for a fast end-to-end dry run.
_SMOKE_DIVISOR = 1000


def _smoke_buckets() -> list[dataset.ScaleBucket]:
    scaled = []
    for bucket in dataset.SCALE_BUCKETS:
        count = max(1, bucket.count // _SMOKE_DIVISOR)
        scaled.append(dataset.ScaleBucket(bucket.name, count))
    return scaled


def generate(output_dir: str, seed: int, smoke: bool) -> None:
    buckets = _smoke_buckets() if smoke else list(dataset.SCALE_BUCKETS)

    os.makedirs(dataset.manifest_dir(output_dir), exist_ok=True)
    total = sum(b.count for b in buckets)
    print(f"  Output:      {output_dir}")
    print(f"  Seed:        {seed}")
    print(f"  Total files: {total:,}{'  (smoke)' if smoke else ''}")
    print()

    # Rewrite the manifest index with the actual bucket sizes in use so that
    # smoke runs and full runs both produce a self-consistent manifest.
    _write_index(output_dir, seed, buckets, total)

    t0 = time.monotonic()
    created = 0
    for bucket in buckets:
        bucket_root = os.path.join(output_dir, "files", bucket.name)
        manifest_path = dataset.bucket_manifest_path(output_dir, bucket.name)
        with open(manifest_path, "w", encoding="utf-8") as manifest:
            for rel in dataset.iter_bucket_relpaths(bucket.name, bucket.count, seed):
                abs_path = os.path.join(bucket_root, rel)
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as file:
                    file.write(dataset.FILE_CONTENT)
                manifest.write(rel + "\n")
                created += 1
                if created % 50_000 == 0:
                    elapsed = time.monotonic() - t0
                    rate = created / elapsed if elapsed else 0
                    print(f"  [{created:,}/{total:,}] files  ({elapsed:.0f}s, {rate:,.0f}/s)")
        print(f"  bucket {bucket.name:12} done ({bucket.count:,} files)")

    elapsed = time.monotonic() - t0
    print()
    print(f"  Created {created:,} files in {elapsed:.0f}s")


def _write_index(output_dir, seed, buckets, total) -> None:
    import json

    scales = []
    bucket_counts = {b.name: b.count for b in buckets}
    for scale, bucket_name, full_count in dataset.SCALES:
        if bucket_name:
            root_bucket = bucket_name.split("/", 1)[0]
            if "/" in bucket_name:
                count = (
                    full_count
                    if bucket_counts.get(root_bucket, 0) >= dataset.SCALE_500_SUBBUCKET_TOTAL
                    else 0
                )
            else:
                count = bucket_counts.get(root_bucket, 0)
        else:
            count = total  # root scale
        scales.append({"scale": scale, "bucket": bucket_name, "count": count})

    payload = {
        "seed": seed,
        "total_files": total,
        "buckets": [{"name": b.name, "count": b.count} for b in buckets],
        "scales": scales,
    }
    with open(dataset.manifest_index_path(output_dir), "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 0 (Performance): prepare glob dataset")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"default: {DEFAULT_OUTPUT}")
    parser.add_argument("--seed", type=int, default=42, help="deterministic layout seed")
    parser.add_argument("--smoke", action="store_true", help="tiny dataset for a dry run")
    args = parser.parse_args()

    output = os.path.expanduser(args.output)
    print("=" * 80)
    print("Step 0 (Performance): Prepare Synthetic path_glob Dataset")
    print("=" * 80)
    generate(output, args.seed, args.smoke)
    print()
    print(f"Dataset ready at: {os.path.join(output, 'files')}")
    print(f"Manifest ready at: {dataset.manifest_dir(output)}")
    print("Next: run step1_add_resource.py to import (vectors_only).")


if __name__ == "__main__":
    main()
