from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any

OV_CONFIG_FILE = "/home/byteide/.openviking/ov.conf"
WORKSPACE_URI = "viking://resources/snapshot_sdk_demo"
WAIT_TIMEOUT = 180.0


def unique_run_uri() -> tuple[str, str]:
    run_id = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    return run_id, f"{WORKSPACE_URI}_{run_id}"


def resource_uris(root_uri: str) -> dict[str, str]:
    return {
        "guide": f"{root_uri}/guide.md",
        "todo": f"{root_uri}/notes/todo.md",
        "changelog": f"{root_uri}/changelog.md",
        "archive": f"{root_uri}/archive/old.md",
    }


def print_section(title: str) -> None:
    print(f"\n{'=' * 20} {title} {'=' * 20}")


def short_oid(commit_oid: str | None) -> str:
    return commit_oid[:12] if commit_oid else "<none>"


def write_text(client: Any, uri: str, content: str, mode: str) -> None:
    result = client.write(uri, content, mode=mode, wait=True, timeout=WAIT_TIMEOUT)
    print(f"write: {uri} (mode={result.get('mode')}, bytes={result.get('written_bytes')})")


def remove_resource(client: Any, uri: str) -> None:
    client.rm(uri, wait=True, timeout=WAIT_TIMEOUT)
    print(f"rm: {uri}")


def print_find(client: Any, query: str, root_uri: str) -> None:
    results = client.find(query, target_uri=root_uri, limit=10)
    if not results.resources:
        print(f"find {query!r}: (no matches)")
        return
    print(f"find {query!r}: {len(results.resources)} match(es)")
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")


def print_read(client: Any, uri: str) -> None:
    content = client.read(uri)
    first_line = content.splitlines()[0] if content else ""
    print(f"read {uri}: {len(content)} chars | {first_line}")


def commit_snapshot(client: Any, message: str) -> dict[str, Any]:
    result = client.snapshot.commit(message=message)
    print(f"commit {message!r}: result={result.get('result')} oid={short_oid(result.get('commit_oid'))}")
    return result


def wait_for_task(
    client: Any,
    task_id: str | None,
    *,
    timeout: float = WAIT_TIMEOUT,
    poll_interval: float = 0.5,
) -> None:
    """Poll a background task by id until it reaches a terminal state.

    restore schedules vectorization/indexing asynchronously and returns a
    ``task_id``; finding before that task completes can read stale vectors.
    """
    if not task_id:
        print("wait_for_task: no task_id (no vector side-effects to await)")
        return
    deadline = time.time() + timeout
    while True:
        task = client.get_task(task_id) or {}
        status = task.get("status")
        if status in ("completed", "failed"):
            print(f"wait_for_task {task_id[:12]}: {status}")
            if status == "failed":
                raise RuntimeError(f"task {task_id} failed: {task.get('error')}")
            return
        if time.time() > deadline:
            raise TimeoutError(f"task {task_id} not complete after {timeout}s (status={status})")
        time.sleep(poll_interval)


def main() -> None:
    os.environ["OPENVIKING_CONFIG_FILE"] = str(Path(OV_CONFIG_FILE).resolve())

    import openviking as ov

    run_id, root_uri = unique_run_uri()
    uris = resource_uris(root_uri)
    alpha = f"alpha_{run_id}"
    beta = f"beta_{run_id}"
    todo = f"todo_{run_id}"
    changelog = f"changelog_{run_id}"
    gamma = f"gamma_{run_id}"
    archive = f"archive_{run_id}"

    client = ov.OpenViking(path="./data")
    client.initialize()
    try:
        print_section("setup")
        print(f"config: {Path(OV_CONFIG_FILE).resolve()}")
        print(f"workspace: {root_uri}")
        client.mkdir(root_uri)
        client.mkdir(f"{root_uri}/notes")
        print(f"mkdir: {root_uri}, {root_uri}/notes")

        print_section("v1 initial import")
        write_text(client, uris["guide"], f"# Guide\n\nInitial SDK content with {alpha}.\n", mode="create")
        write_text(client, uris["todo"], f"# Todo\n\nRemember {todo}.\n", mode="create")
        v1 = commit_snapshot(client, "sdk v1 initial import")
        print_find(client, alpha, root_uri)

        print_section("v2 modify delete add")
        write_text(client, uris["guide"], f"# Guide\n\nUpdated SDK content with {beta}.\n", mode="replace")
        remove_resource(client, uris["todo"])
        write_text(client, uris["changelog"], f"# Changelog\n\nCreated {changelog}.\n", mode="create")
        v2 = commit_snapshot(client, "sdk v2 modify delete add")
        print_find(client, beta, root_uri)
        print_find(client, todo, root_uri)
        print_find(client, changelog, root_uri)

        print_section("v3 second changes")
        client.mkdir(f"{root_uri}/archive")
        print(f"mkdir: {root_uri}/archive")
        write_text(client, uris["changelog"], f"# Changelog\n\nCreated {changelog}. Added {gamma}.\n", mode="replace")
        write_text(client, uris["archive"], f"# Archive\n\nArchived marker {archive}.\n", mode="create")
        v3 = commit_snapshot(client, "sdk v3 second changes")
        print_find(client, gamma, root_uri)
        print_find(client, archive, root_uri)
        log_before = client.snapshot.log(limit=10)
        print(f"snapshot log: {len(log_before)} commit(s)")
        for commit in log_before:
            print(f"  {short_oid(commit.get('oid'))} {commit.get('message', '')}")
        for label, snap in (("v1", v1), ("v2", v2), ("v3", v3)):
            meta = client.snapshot.show(snap["commit_oid"])
            print(f"snapshot show {label}: oid={short_oid(meta.get('oid'))} message={meta.get('message', '')!r}")

        print_section("restore to v1")
        restore = client.snapshot.restore(
            project_dir=root_uri,
            source_commit=v1["commit_oid"],
            message="sdk restore to v1",
        )
        print(
            f"snapshot restore: result={restore.get('result')} oid={short_oid(restore.get('commit_oid'))} "
            f"written={len(restore.get('written_paths') or [])} deleted={len(restore.get('deleted_paths') or [])}"
        )
        wait_for_task(client, restore.get("task_id"))
        entries = client.ls(root_uri, recursive=True)
        print(f"ls after restore: {len(entries)} entry(ies)")
        for entry in entries:
            print(f"  {entry.get('uri') if isinstance(entry, dict) else entry}")
        print_read(client, uris["guide"])
        print_read(client, uris["todo"])
        print_find(client, alpha, root_uri)
        print_find(client, beta, root_uri)
        print_find(client, changelog, root_uri)
        log_after = client.snapshot.log(limit=10)
        print(f"snapshot log after restore: {len(log_after)} commit(s)")
        for commit in log_after:
            print(f"  {short_oid(commit.get('oid'))} {commit.get('message', '')}")

        print_section("done")
        print("Python SDK snapshot multi-version example finished")
    finally:
        client.close()


if __name__ == "__main__":
    main()
