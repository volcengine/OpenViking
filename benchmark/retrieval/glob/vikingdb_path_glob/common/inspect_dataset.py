#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Inspect the synthetic dataset in memory: depth distribution + pattern ratios.

This does not touch disk or any server. It regenerates bucket relative paths
from the deterministic generator and reports, for a chosen sample size:

  * directory-level distribution (min / max / mean / histogram)
  * for every benchmark pattern, the exact matched fraction on the sample

Use it to sanity-check the layout and recall spectrum before running the real
import. Absolute-count landmarks (migrations / legacy-gateway) live only in the
padding bucket, so pass ``--root`` to include them the way a 100w glob would.

Usage:
  python3 -m vikingdb_path_glob.common.inspect_dataset            # 50k sample
  python3 -m vikingdb_path_glob.common.inspect_dataset --root     # whole-tree view
  python3 -m vikingdb_path_glob.common.inspect_dataset --sample 200000
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter
from typing import Iterator, List

from . import dataset
from .glob_match import GlobMatcher
from .patterns import PATTERNS


def _depth(rel: str) -> int:
    """Directory levels = path segments minus the file name."""
    return rel.count("/")


def _sample_root(sample: int, seed: int) -> Iterator[str]:
    """Approximate the 100w root view: draw across buckets, prefixing names.

    Buckets are visited largest-first so a bounded sample still includes the
    landmark host bucket (scale_fill) and its exact landmark subtrees.
    """
    remaining = sample
    for bucket in sorted(dataset.SCALE_BUCKETS, key=lambda b: -b.count):
        if remaining <= 0:
            break
        take = min(bucket.count, remaining)
        count = 0
        for rel in dataset.iter_bucket_relpaths(bucket.name, bucket.count, seed):
            yield f"{bucket.name}/{rel}"
            count += 1
            if count >= take:
                break
        remaining -= take


def _sample_bucket(bucket_name: str, sample: int, seed: int) -> Iterator[str]:
    count = 0
    total = next(b.count for b in dataset.SCALE_BUCKETS if b.name == bucket_name)
    for rel in dataset.iter_bucket_relpaths(bucket_name, total, seed):
        yield rel
        count += 1
        if count >= sample:
            break


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect synthetic glob dataset")
    parser.add_argument("--sample", type=int, default=50_000, help="paths to sample")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--root", action="store_true", help="100w root view (with landmarks)")
    parser.add_argument("--bucket", default="scale_50w", help="bucket for non-root view")
    args = parser.parse_args()

    if args.root:
        paths: List[str] = list(_sample_root(args.sample, args.seed))
        view = "100w root (bucket-prefixed, landmarks included)"
    else:
        paths = list(_sample_bucket(args.bucket, args.sample, args.seed))
        view = f"single bucket {args.bucket}"

    n = len(paths)
    depths = [_depth(p) for p in paths]
    hist = Counter(depths)

    print("=" * 78)
    print(f"Dataset inspection — view: {view}")
    print(f"  sampled paths: {n:,}   seed: {args.seed}")
    print("=" * 78)
    print("Directory depth (levels = dirs above the file):")
    print(f"  min={min(depths)}  max={max(depths)}  mean={statistics.mean(depths):.2f}  "
          f"median={int(statistics.median(depths))}")
    for level in sorted(hist):
        pct = 100.0 * hist[level] / n
        bar = "#" * int(pct / 2)
        print(f"    {level} levels: {hist[level]:>8,} ({pct:5.1f}%) {bar}")
    print()

    print("Pattern match ratios on the sample (truth is manifest-exact at run time):")
    print(f"  {'label':<20} {'tier':<10} {'target':<10} {'matched':>10} {'ratio':>8}")
    print("  " + "-" * 62)
    for gp in PATTERNS:
        matcher = GlobMatcher(gp.pattern)
        matched = sum(1 for p in paths if matcher.matches(p))
        ratio = 100.0 * matched / n
        print(f"  {gp.label:<20} {gp.tier:<10} {gp.target:<10} {matched:>10,} {ratio:>7.2f}%")
    print()
    print("Note: absolute landmarks (abs_*) only exist under the 100w root; run")
    print("      with --root to see their exact counts (10 and 100).")


if __name__ == "__main__":
    main()
