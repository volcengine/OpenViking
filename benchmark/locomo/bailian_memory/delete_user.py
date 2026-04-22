"""
Delete all memories for one or more users from Bailian (ModelStudio) Memory.

Usage:
    # Delete a single user
    python delete_user.py conv-26

    # Delete multiple users
    python delete_user.py conv-26 conv-31 conv-45

    # Delete first N users from locomo10.json
    python delete_user.py --from-data --limit 2

    # Delete all users from locomo10.json
    python delete_user.py --from-data

    DASHSCOPE_API_KEY=sk-xxx BAILIAN_MEMORY_LIBRARY_ID=xxx python delete_user.py --from-data
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path.home() / ".openviking_benchmark_env")

SCRIPT_DIR = Path(__file__).parent.resolve()
DEFAULT_DATA_PATH = str(SCRIPT_DIR / ".." / "data" / "locomo10.json")

BAILIAN_MEMORY_BASE_URL = "https://dashscope.aliyuncs.com/api/v2/apps/memory"


def _auth_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def list_memory_nodes(api_key: str, memory_library_id: str, user_id: str) -> list[str]:
    """Return all memory_node_ids for the given user."""
    node_ids = []
    page_num = 1
    page_size = 100

    while True:
        resp = requests.get(
            f"{BAILIAN_MEMORY_BASE_URL}/memory_nodes",
            headers=_auth_headers(api_key),
            params={
                "memory_library_id": memory_library_id,
                "user_id": user_id,
                "page_num": page_num,
                "page_size": page_size,
            },
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        nodes = body.get("memory_nodes", [])
        for n in nodes:
            nid = n.get("memory_node_id")
            if nid:
                node_ids.append(nid)
        if len(nodes) < page_size:
            break
        page_num += 1

    return node_ids


def delete_memory_node(api_key: str, memory_library_id: str, node_id: str) -> None:
    resp = requests.delete(
        f"{BAILIAN_MEMORY_BASE_URL}/memory_nodes/{node_id}",
        headers=_auth_headers(api_key),
        params={"memory_library_id": memory_library_id},
        timeout=30,
    )
    resp.raise_for_status()


def delete_user(api_key: str, memory_library_id: str, user_id: str) -> bool:
    try:
        node_ids = list_memory_nodes(api_key, memory_library_id, user_id)
        if not node_ids:
            print(f"  [OK] {user_id} (no memories found)")
            return True
        for nid in node_ids:
            delete_memory_node(api_key, memory_library_id, nid)
        print(f"  [OK] {user_id} (deleted {len(node_ids)} nodes)")
        return True
    except Exception as e:
        print(f"  [ERROR] {user_id}: {e}")
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete all Bailian Memory nodes for given user(s)")
    parser.add_argument("users", nargs="*", help="user_id(s) to delete (e.g. conv-26 conv-31)")
    parser.add_argument("--api-key", default=None, help="DashScope API key (or DASHSCOPE_API_KEY env var)")
    parser.add_argument("--memory-library-id", default=None, help="Bailian memory library ID (or BAILIAN_MEMORY_LIBRARY_ID env var)")
    parser.add_argument("--from-data", action="store_true", help="load user_ids from locomo10.json")
    parser.add_argument("--input", default=DEFAULT_DATA_PATH, help="path to locomo10.json")
    parser.add_argument("--limit", type=int, default=None, help="max users to delete (with --from-data)")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        print("Error: DashScope API key required (--api-key or DASHSCOPE_API_KEY env var)", file=sys.stderr)
        sys.exit(1)

    memory_library_id = args.memory_library_id or os.environ.get("BAILIAN_MEMORY_LIBRARY_ID", "")
    if not memory_library_id:
        print("Error: memory_library_id required (--memory-library-id or BAILIAN_MEMORY_LIBRARY_ID env var)", file=sys.stderr)
        sys.exit(1)

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

    ok = sum(delete_user(api_key, memory_library_id, uid) for uid in user_ids)
    print(f"\nDone: {ok}/{len(user_ids)} succeeded")


if __name__ == "__main__":
    main()
