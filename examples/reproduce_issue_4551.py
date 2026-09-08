#!/usr/bin/env python3
"""Import a slash-bearing event and report unintended memory URI hierarchy."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Any

from openviking_sdk import SyncHTTPClient

EVENT_URI_MARKER = "/memories/events/"
EXPECTED_EVENT_PATH_COMPONENTS = 4
TERMINAL_TASK_STATUSES = frozenset({"completed", "failed", "cancelled"})

# Both turns describe one durable decision and repeat the official slash-bearing
# project name so memory extraction reliably keeps the slash in a dynamic field.
MESSAGES = (
    {
        "role": "user",
        "content": (
            "On 2026-08-31, the release owner formally approved the production rollout "
            "of the project alpha/beta. This is a durable project decision. The official "
            "project spelling is alpha/beta, including the slash; preserve that spelling."
        ),
    },
    {
        "role": "assistant",
        "content": (
            "Confirmed: the alpha/beta production rollout was formally approved on "
            "2026-08-31, and the slash is part of the official project name."
        ),
    },
)


def wait_for_task(
    client: SyncHTTPClient,
    task_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Wait for a commit task to finish and return its terminal task payload.

    Args:
        client: Initialized synchronous OpenViking HTTP client.
        task_id: Commit task identifier returned by ``session.commit()``.
        timeout_seconds: Maximum wait time in seconds.

    Returns:
        The task payload whose status is completed, failed, or cancelled.

    Raises:
        TimeoutError: The task does not reach a terminal state before the deadline.
    """
    deadline = time.monotonic() + timeout_seconds
    last_status = None
    while time.monotonic() < deadline:
        task = client.get_task(task_id)
        status = str((task or {}).get("status", "missing")).lower()
        if status != last_status:
            print(f"commit task: {status}")
            last_status = status
        if task and status in TERMINAL_TASK_STATUSES:
            return task
        time.sleep(1)
    raise TimeoutError(f"Commit task {task_id} did not finish within {timeout_seconds}s")


def load_memory_diff(client: SyncHTTPClient, memory_diff_uri: str) -> dict[str, Any]:
    """Read and decode the memory diff produced by a completed session commit."""
    payload = client.read(memory_diff_uri)
    if isinstance(payload, dict):
        return payload
    return json.loads(payload)


def extracted_event_uris(memory_diff: dict[str, Any]) -> list[str]:
    """Return event URIs added or updated by one memory extraction."""
    operations = memory_diff.get("operations", {})
    event_uris = []
    for operation_name in ("adds", "updates"):
        for operation in operations.get(operation_name, []):
            uri = str(operation.get("uri", ""))
            if operation.get("memory_type") == "events" and EVENT_URI_MARKER in uri:
                event_uris.append(uri)
    return event_uris


def has_unintended_event_hierarchy(uri: str) -> bool:
    """Return whether an event URI has more components than year/month/day/file."""
    _, marker, relative_path = uri.partition(EVENT_URI_MARKER)
    if not marker:
        return False
    components = [component for component in relative_path.split("/") if component]
    return len(components) > EXPECTED_EVENT_PATH_COMPONENTS


def reproduce(url: str, api_key: str | None, session_id: str, timeout: float) -> int:
    """Import the fixture, commit it, and print evidence of unintended hierarchy.

    The created session and extracted memories remain on the configured server so
    their URI tree can be inspected after the script exits.
    """
    client_args: dict[str, Any] = {"url": url}
    if api_key:
        client_args["api_key"] = api_key
    client = SyncHTTPClient(**client_args)
    client.initialize()

    try:
        created = client.create_session(session_id=session_id)
        session = client.session(created["session_id"])
        print(f"session: {session.session_id}")

        for message in MESSAGES:
            session.add_message(role=message["role"], content=message["content"])
            print(f"added {message['role']} message: {message['content']}")

        commit = session.commit()
        print(f"commit accepted: {json.dumps(commit, ensure_ascii=False)}")
        task_id = commit.get("task_id")
        if not task_id:
            raise RuntimeError(f"Commit did not return a task_id: {commit}")

        task = wait_for_task(client, task_id, timeout)
        if task.get("status") != "completed":
            raise RuntimeError(f"Commit task failed: {json.dumps(task, ensure_ascii=False)}")

        task_result = task.get("result", {})
        memory_diff_uri = task_result.get("memory_diff_uri")
        if not memory_diff_uri:
            archive_uri = task_result.get("archive_uri") or commit.get("archive_uri")
            if not archive_uri:
                raise RuntimeError(f"Commit task did not return an archive URI: {task}")
            memory_diff_uri = f"{str(archive_uri).rstrip('/')}/memory_diff.json"

        memory_diff = load_memory_diff(client, memory_diff_uri)
        print(f"memory diff: {memory_diff_uri}")
        event_uris = extracted_event_uris(memory_diff)
        bad_uris = [uri for uri in event_uris if has_unintended_event_hierarchy(uri)]

        print("event URIs:")
        for uri in event_uris:
            print(f"  {uri}")

        if bad_uris:
            print("\nREPRODUCED: dynamic '/' introduced an additional directory level.")
            for uri in bad_uris:
                relative_path = uri.split(EVENT_URI_MARKER, 1)[1]
                print(f"  relative path components: {relative_path.split('/')}")
            return 0

        print("\nNOT REPRODUCED: extraction did not emit a slash-bearing event name.")
        print(json.dumps(memory_diff, ensure_ascii=False, indent=2))
        return 2
    finally:
        client.close()


def main() -> int:
    """Parse command-line options and run the issue reproduction."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.getenv("OPENVIKING_URL", "http://localhost:1933"),
        help="OpenViking server URL",
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("OPENVIKING_API_KEY"),
        help="API key; defaults to OPENVIKING_API_KEY",
    )
    parser.add_argument(
        "--session-id",
        default=f"issue-4551-repro-{uuid.uuid4().hex[:8]}",
        help="Session ID to create",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300,
        help="Maximum seconds to wait for memory extraction",
    )
    args = parser.parse_args()
    return reproduce(args.url, args.api_key, args.session_id, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
