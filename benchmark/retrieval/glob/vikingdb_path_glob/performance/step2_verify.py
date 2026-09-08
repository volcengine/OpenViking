#!/usr/bin/env python3
# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Step 2 (Performance): verify both glob faces are fully populated.

The remote VikingDB face is populated asynchronously (vectorization lags behind
the import HTTP response), so before benchmarking we poll ``glob`` until the
visible file count for each scale matches the manifest's expected count.

Run this once per engine you plan to benchmark:
  * engine=fs   -> confirms the on-disk AGFS tree is complete.
  * engine=auto -> confirms the VikingDB uri records have caught up.

The comparison uses ``**/*`` (every generated file at any depth) and a large
node_limit, comparing against the v2 manifest's exact file count. The v2 dataset
does not generate hidden files or hidden directories.

Usage:
  python3 step2_verify.py
  python3 step2_verify.py --scales 500 1k 5k
  python3 step2_verify.py --timeout 1800 --interval 15
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from openviking_cli.client.sync_http import SyncHTTPClient  # noqa: E402

from vikingdb_path_glob.common import dataset  # noqa: E402
from vikingdb_path_glob.common.glob_match import expected_matches  # noqa: E402

DEFAULT_OUTPUT = os.path.expanduser("~/.openviking/data/benchmark/glob_synthetic_v2")
BASE_URI = "viking://resources/benchmark/glob"
VERIFY_PATTERN = "**/*"
VERIFY_NODE_LIMIT = 2_000_000


def scale_base_uri(scale: str, bucket: str) -> str:
    return BASE_URI if not bucket else f"{BASE_URI}/{bucket}"


def glob_count(client: SyncHTTPClient, uri: str, pattern: str) -> int:
    result = client.glob(uri=uri, pattern=pattern, node_limit=VERIFY_NODE_LIMIT)
    if not isinstance(result, dict):
        return 0
    # ``matches`` is a list of URI strings; directory hits carry a trailing
    # slash. Count distinct file URIs only, to compare against the manifest.
    matches = result.get("matches", [])
    return len({m.rstrip("/") for m in matches if isinstance(m, str) and not m.endswith("/")})


def heal_remote_consistency(base_uri: str) -> None:
    """Reconcile the remote VikingDB face with the AGFS metadata tree.

    Bulk imports occasionally drop an individual record on the remote face
    (the AGFS entry is written but the corresponding VikingDB uri record is
    not). ``check_consistency`` walks the subtree and reports any such gaps;
    ``reindex`` re-materializes them. Both are first-class client APIs, so this
    step is safe to run before every verification pass.
    """
    client = SyncHTTPClient(timeout=3600)
    client.initialize()
    try:
        report = client.check_consistency(uri=base_uri)
        missing = int(report.get("missing_record_count", 0) or 0)
        expected = report.get("expected_count")
        if report.get("ok") and missing == 0:
            print(f"  Heal: consistent ({expected} records), nothing to reindex.")
            return
        print(f"  Heal: {missing} record(s) missing on remote face; reindexing subtree ...")
        result = client.reindex(uri=base_uri, mode="vectors_only", wait=True, recursive=True)
        print(
            "  Heal: reindex rebuilt="
            f"{result.get('rebuilt_records')} failed={result.get('failed_records')} "
            f"scanned={result.get('scanned_records')}"
        )
        recheck = client.check_consistency(uri=base_uri)
        still = int(recheck.get("missing_record_count", 0) or 0)
        if recheck.get("ok") and still == 0:
            print("  Heal: subtree now consistent.")
        else:
            print(f"  Heal: WARNING still {still} record(s) missing after reindex.")
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 2 (Performance): verify faces")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--scales", nargs="+", default=None, help="subset of scales")
    parser.add_argument("--url", default=None, help="OpenViking server URL")
    parser.add_argument("--timeout", type=int, default=3600, help="max wait seconds per scale")
    parser.add_argument("--interval", type=int, default=20, help="poll interval seconds")
    parser.add_argument(
        "--heal",
        action="store_true",
        help=(
            "before polling, run check_consistency on the base URI and reindex "
            "any records that were imported into AGFS but never landed in the "
            "remote VikingDB face (recovers rare per-record import drops)."
        ),
    )
    args = parser.parse_args()

    output = os.path.expanduser(args.output)
    index = dataset.load_manifest_index(output)
    scale_to_bucket = {e["scale"]: e["bucket"] for e in index["scales"]}

    wanted = args.scales or [e["scale"] for e in index["scales"]]

    print("=" * 80)
    print("Step 2 (Performance): Verify Glob Faces Populated")
    print("=" * 80)
    print(f"  Base URI: {BASE_URI}")
    print(f"  Pattern:  {VERIFY_PATTERN} (all v2 files, any depth)")
    print("  Ensure ov.conf glob.engine is set to the face you want to verify.")
    print()

    if args.heal:
        heal_remote_consistency(BASE_URI)

    all_ready = True
    for scale in wanted:
        if scale not in scale_to_bucket:
            print(f"  {scale}: SKIP (unknown scale)")
            continue
        bucket = scale_to_bucket[scale]
        rel_paths = dataset.load_scale_relpaths(output, scale)
        expected = len(expected_matches(VERIFY_PATTERN, rel_paths))
        uri = scale_base_uri(scale, bucket)

        client = (
            SyncHTTPClient(url=args.url, timeout=3600)
            if args.url
            else SyncHTTPClient(timeout=3600)
        )
        client.initialize()
        deadline = time.monotonic() + args.timeout
        found = 0
        ready = False
        try:
            while True:
                found = glob_count(client, uri, VERIFY_PATTERN)
                if found >= expected:
                    ready = True
                    break
                if time.monotonic() >= deadline:
                    break
                print(f"  {scale:5} waiting: found={found:,}/{expected:,} ...", flush=True)
                time.sleep(args.interval)
        finally:
            client.close()

        status = "READY" if ready else "TIMEOUT"
        all_ready = all_ready and ready
        print(f"  {scale:5} {status:8} found={found:,} expected={expected:,}  uri={uri}")

    print()
    print("All scales ready." if all_ready else "Some scales not ready (see TIMEOUT rows).")


if __name__ == "__main__":
    main()
