"""
Delete all memories for one or more mem0 users.

Usage:
    # Delete a single user
    python delete_user.py conv-26

    # Delete multiple users
    python delete_user.py conv-26 conv-31 conv-45

    # Delete first N users from locomo10.json
    python delete_user.py --from-data --limit 2

    # Delete all users from locomo10.json
    python delete_user.py --from-data
"""

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path.home() / ".openviking_benchmark_env")

try:
    from mem0 import MemoryClient
except ImportError:
    print("Error: mem0 package not installed. Run: pip install mem0ai", file=sys.stderr)
    sys.exit(1)

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "data" / "locomo10.json")


def delete_user(client: MemoryClient, user_id: str) -> bool:
    try:
        client.delete_all(user_id=user_id)
        print(f"  [OK] {user_id}")
        return True
    except Exception as e:
        print(f"  [ERROR] {user_id}: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete all mem0 memories for given user(s)")
    parser.add_argument("users", nargs="*", help="user_id(s) to delete (e.g. conv-26 conv-31)")
    parser.add_argument("--api-key", default=None, help="mem0 API key (or MEM0_API_KEY env var)")
    parser.add_argument("--from-data", action="store_true", help="load user_ids from locomo10.json")
    parser.add_argument("--input", default=DEFAULT_DATA_PATH, help="path to locomo10.json")
    parser.add_argument("--limit", type=int, default=None, help="max users to delete (with --from-data)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("MEM0_API_KEY", "")
    if not api_key:
        print("Error: mem0 API key required (--api-key or MEM0_API_KEY env var)", file=sys.stderr)
        sys.exit(1)

    # Convert bare sample_ids (e.g. "conv-26") to mem0 user_id format
    user_ids: list[str] = list(args.users)

    if args.from_data:
        with open(args.input, "r", encoding="utf-8") as f:
            data = json.load(f)
        if args.limit:
            data = data[: args.limit]
        user_ids += [s["sample_id"] for s in data]

    if not user_ids:
        print("Error: no users specified. Pass user_ids or use --from-data", file=sys.stderr)
        sys.exit(1)

    user_ids = list(dict.fromkeys(user_ids))  # deduplicate, preserve order
    print(f"Deleting memories for {len(user_ids)} user(s)...")

    client = MemoryClient(api_key=api_key)
    ok = sum(delete_user(client, uid) for uid in user_ids)
    print(f"\nDone: {ok}/{len(user_ids)} succeeded")


if __name__ == "__main__":
    main()
