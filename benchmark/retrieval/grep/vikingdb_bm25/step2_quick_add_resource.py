#!/usr/bin/env python3
"""Step 2: Quick upload — import benchmark files skipping VLM+embedding.

Walks the benchmark directory and uploads each file via the OpenViking Python SDK
with build_index=False, which skips VLM summarization and embedding. This makes
the upload phase fast and avoids circuit-breaker issues from VLM failures.

After all files are uploaded, run step3_build_index.py to trigger VLM+embedding
in a controlled batch, then step4_benchmark.py to measure grep performance.

Supports resume: a progress file (.add_resource_progress) tracks completed files.
If interrupted, re-run to automatically skip already-imported files.

Usage:
  python3 step2_quick_add_resource.py [--no-resume] [--max-failures N]
"""
import argparse
import os
import sys

BASE_DIR = os.path.expanduser("~/.openviking/data/benchmark")
DATA_DIR = os.path.expanduser("~/.openviking/data")
PROGRESS_FILE = os.path.join(BASE_DIR, ".add_resource_progress")


def load_progress() -> set:
    """Load set of already-imported relative paths from progress file."""
    done = set()
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    done.add(line)
    return done


def save_progress(rel_path: str) -> None:
    """Append a completed relative path to the progress file and flush immediately."""
    with open(PROGRESS_FILE, "a") as f:
        f.write(rel_path + "\n")
        f.flush()
        os.fsync(f.fileno())


def main():
    parser = argparse.ArgumentParser(
        description="Step 2: Quick upload benchmark files (skip VLM+embedding)"
    )
    parser.add_argument(
        "--no-resume", action="store_true", help="Disable auto-resume, start from scratch"
    )
    parser.add_argument(
        "--max-failures", type=int, default=10, help="Abort after N failures (default: 10)"
    )
    args = parser.parse_args()

    from openviking.sync_client import SyncOpenViking

    client = SyncOpenViking()
    client.initialize()

    # Collect all files first (deterministic order)
    all_files = []
    for root, dirs, files in os.walk(BASE_DIR):
        dirs.sort()
        for fname in sorted(files):
            if fname.endswith(".md"):
                all_files.append(os.path.join(root, fname))

    # Load resume state
    done_set = set()
    if not args.no_resume:
        done_set = load_progress()
        if done_set:
            print(f"Resuming: {len(done_set)} files already imported (from {PROGRESS_FILE})")

    count = 0
    skipped = 0
    failed = 0

    for filepath in all_files:
        rel = os.path.relpath(filepath, DATA_DIR)
        rel_dir = os.path.dirname(rel)
        parent_uri = f"viking://resources/{rel_dir}"

        # Skip already-imported files
        if rel in done_set:
            skipped += 1
            continue

        idx = count + skipped + 1
        print(f"[{idx}/{len(all_files)}] Uploading {rel} ...", end=" ", flush=True)

        try:
            client.add_resource(
                path=filepath,
                parent=parent_uri,
                build_index=False,
                wait=False,
                create_parent=True,
            )
            print("OK")
            save_progress(rel)
        except Exception as e:
            print(f"FAILED: {e}")
            failed += 1
            if failed >= args.max_failures:
                print(f"\nToo many failures ({failed}), aborting. Re-run to resume.")
                sys.exit(1)

        count += 1
        if count % 100 == 0:
            print(f"  ... {count} files uploaded this run ({failed} failed, {skipped} skipped)")

    print(f"\nDone! {count} uploaded, {skipped} skipped, {failed} failed")
    if failed == 0:
        print("Next step: run step3_build_index.py to trigger VLM+embedding")


if __name__ == "__main__":
    main()
