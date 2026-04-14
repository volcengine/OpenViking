"""
Delete all Supermemory documents for one or more containerTags (sample_ids).

Usage:
    # Delete a single container
    python delete_container.py conv-26

    # Delete multiple containers
    python delete_container.py conv-26 conv-31 conv-45

    # Delete first N samples from locomo10.json
    python delete_container.py --from-data --limit 2

    # Delete all samples from locomo10.json
    python delete_container.py --from-data
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path.home() / ".openviking_benchmark_env")

try:
    from supermemory import Supermemory
except ImportError:
    print("Error: supermemory package not installed. Run: pip install supermemory", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "data" / "locomo10.json")
DEFAULT_RECORD_PATH = str(SCRIPT_DIR / "result" / ".ingest_record.json")


def sanitize_tag(raw: str) -> str:
    """Sanitize a tag string to match openclaw-supermemory convention.
    e.g. 'conv-26' -> 'conv_26'
    """
    tag = re.sub(r"[^a-zA-Z0-9_]", "_", raw)
    tag = re.sub(r"_+", "_", tag)
    tag = tag.strip("_")
    return tag


def wipe_container(client: Supermemory, container_tag: str) -> int:
    """
    Delete all documents in a containerTag using documents.list + deleteBulk.
    Returns number of documents deleted.
    """
    all_ids: list[str] = []
    page = 1

    while True:
        response = client.documents.list(
            container_tags=[container_tag],
            limit=100,
            page=page,
        )

        memories = getattr(response, "memories", None)
        if memories is None and isinstance(response, dict):
            memories = response.get("memories", [])

        if not memories:
            break

        for doc in memories:
            doc_id = getattr(doc, "id", None) or (doc.get("id") if isinstance(doc, dict) else None)
            if doc_id:
                all_ids.append(doc_id)

        # Check pagination
        pagination = getattr(response, "pagination", None) or (response.get("pagination") if isinstance(response, dict) else None)
        total_pages = None
        if pagination:
            total_pages = getattr(pagination, "totalPages", None) or (pagination.get("totalPages") if isinstance(pagination, dict) else None)

        if total_pages is None or page >= total_pages:
            break
        page += 1

    if not all_ids:
        return 0

    # Delete in batches of 100
    deleted = 0
    for i in range(0, len(all_ids), 100):
        batch = all_ids[i : i + 100]
        client.documents.delete_bulk(ids=batch)
        deleted += len(batch)

    return deleted


def clear_ingest_records(container_tag: str, record_path: str) -> int:
    """Remove ingest records for the given container_tag. Returns count removed."""
    try:
        with open(record_path, "r", encoding="utf-8") as f:
            record = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0

    # Records are keyed as "supermemory:{sample_id}:{session_key}"
    # Match by sanitized sample_id to handle keys like "conv-26" vs "conv_26"
    keys_to_remove = [k for k in record if len(k.split(":")) >= 2 and sanitize_tag(k.split(":")[1]) == container_tag]

    for k in keys_to_remove:
        del record[k]

    with open(record_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    return len(keys_to_remove)


def delete_container(client: Supermemory, sample_id: str, record_path: str) -> bool:
    container_tag = sanitize_tag(sample_id)
    print(f"  [containerTag={container_tag}] listing documents...", file=sys.stderr)

    try:
        deleted = wipe_container(client, container_tag)
        if deleted == 0:
            print(f"  [WARN] No documents found (may already be deleted)", file=sys.stderr)
        else:
            print(f"  [OK] Deleted {deleted} documents", file=sys.stderr)
    except Exception as e:
        print(f"  [ERROR] Failed to delete documents: {e}", file=sys.stderr)
        return False

    removed = clear_ingest_records(container_tag, record_path)
    if removed:
        print(f"  Cleared {removed} ingest record(s)", file=sys.stderr)

    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete all Supermemory documents for given sample(s)")
    parser.add_argument("samples", nargs="*", help="sample_id(s) to delete (e.g. conv-26 conv-31)")
    parser.add_argument("--api-key", default=None, help="Supermemory API key (or SUPERMEMORY_API_KEY env var)")
    parser.add_argument("--from-data", action="store_true", help="load sample_ids from locomo10.json")
    parser.add_argument("--input", default=DEFAULT_DATA_PATH, help="path to locomo10.json")
    parser.add_argument("--limit", type=int, default=None, help="max samples to delete (with --from-data)")
    parser.add_argument(
        "--record",
        default=DEFAULT_RECORD_PATH,
        help=f"Path to ingest progress record (default: {DEFAULT_RECORD_PATH})",
    )
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("SUPERMEMORY_API_KEY", "")
    if not api_key:
        print("Error: Supermemory API key required (--api-key or SUPERMEMORY_API_KEY env var)", file=sys.stderr)
        sys.exit(1)

    sample_ids: list[str] = list(args.samples)

    if args.from_data:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        if args.limit:
            data = data[: args.limit]
        sample_ids += [s["sample_id"] for s in data]

    if not sample_ids:
        print("Error: no sample_ids specified. Pass sample_ids or use --from-data", file=sys.stderr)
        sys.exit(1)

    sample_ids = list(dict.fromkeys(sample_ids))  # deduplicate, preserve order
    print(f"Deleting documents for {len(sample_ids)} sample(s)...", file=sys.stderr)

    client = Supermemory(api_key=api_key)
    ok = 0
    for sid in sample_ids:
        print(f"\n=== {sid} ===", file=sys.stderr)
        if delete_container(client, sid, args.record):
            ok += 1

    print(f"\nDone: {ok}/{len(sample_ids)} succeeded", file=sys.stderr)


if __name__ == "__main__":
    main()
