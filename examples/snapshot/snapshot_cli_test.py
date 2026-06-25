from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

OVCLI_CONFIG_FILE = "/home/byteide/.openviking/ovcli.conf"
CLI_BIN = "ov"
WORKSPACE_URI = "viking://resources/snapshot_cli_demo"
COMMAND_TIMEOUT = 180


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


def parse_json(stdout: str) -> dict[str, Any]:
    depth = 0
    start: int | None = None
    in_string = False
    escaped = False

    for index, char in enumerate(stdout):
        if start is None:
            if char == "{":
                start = index
                depth = 1
            continue

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(stdout[start : index + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


def print_commit_oid(label: str, snapshot: dict[str, Any]) -> None:
    print(f"{label} commit_oid: {snapshot.get('commit_oid') or '<missing>'}")


def show_snapshot(label: str, snapshot: dict[str, Any]) -> None:
    commit_oid = snapshot.get("commit_oid")
    if not commit_oid:
        raise RuntimeError(f"{label} snapshot output did not include commit_oid: {snapshot}")
    print(f"showing {label} commit_oid: {commit_oid}")
    run_ov(["snapshot", "show", commit_oid, "-o", "json"])


def run_ov(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["OPENVIKING_CLI_CONFIG_FILE"] = str(Path(OVCLI_CONFIG_FILE).resolve())
    print(f"\n$ {CLI_BIN} {' '.join(args)}")
    proc = subprocess.run(
        [CLI_BIN] + args,
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT,
        env=env,
    )
    print("--- stdout ---")
    print(proc.stdout.rstrip())
    print("--- stderr ---")
    print(proc.stderr.rstrip())
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed with exit code {proc.returncode}: {CLI_BIN} {' '.join(args)}")
    return proc


def mkdir(uri: str) -> None:
    run_ov(["mkdir", uri, "-o", "json"])


def write_text(uri: str, content: str, mode: str) -> None:
    run_ov(["write", uri, "--content", content, "--mode", mode, "--wait", "-o", "json"])


def remove_resource(uri: str) -> None:
    run_ov(["rm", uri, "--wait", "-o", "json"])


def print_find(query: str, root_uri: str) -> None:
    run_ov(["find", query, "--uri", root_uri, "--limit", "10", "-o", "json"])


def commit_snapshot(message: str) -> dict[str, Any]:
    proc = run_ov(["snapshot", "commit", "-m", message, "-o", "json"])
    return parse_json(proc.stdout).get("result") or {}


def get_task(task_id: str) -> dict[str, Any]:
    proc = run_ov(["task", "status", task_id, "-o", "json"])
    return parse_json(proc.stdout).get("result") or {}


def wait_for_task(task_id: str | None, *, timeout: float = COMMAND_TIMEOUT, poll_interval: float = 0.5) -> None:
    """Poll a background task by id until it reaches a terminal state.

    restore schedules vectorization/indexing asynchronously and returns a
    ``task_id``; finding before that task completes can read stale vectors.
    """
    if not task_id:
        print("wait_for_task: no task_id (no vector side-effects to await)")
        return
    deadline = time.time() + timeout
    while True:
        task = get_task(task_id)
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
    run_id, root_uri = unique_run_uri()
    uris = resource_uris(root_uri)
    alpha = f"alpha_{run_id}"
    beta = f"beta_{run_id}"
    todo = f"todo_{run_id}"
    changelog = f"changelog_{run_id}"
    gamma = f"gamma_{run_id}"
    archive = f"archive_{run_id}"

    print_section("setup")
    print(f"config: {Path(OVCLI_CONFIG_FILE).resolve()}")
    print(f"workspace: {root_uri}")
    mkdir(root_uri)
    mkdir(f"{root_uri}/notes")

    print_section("v1 initial import")
    write_text(uris["guide"], f"# Guide\n\nInitial CLI content with {alpha}.\n", "create")
    write_text(uris["todo"], f"# Todo\n\nRemember {todo}.\n", "create")
    v1 = commit_snapshot("cli v1 initial import")
    print_commit_oid("v1", v1)
    print_find(alpha, root_uri)

    print_section("v2 modify delete add")
    write_text(uris["guide"], f"# Guide\n\nUpdated CLI content with {beta}.\n", "replace")
    remove_resource(uris["todo"])
    write_text(uris["changelog"], f"# Changelog\n\nCreated {changelog}.\n", "create")
    v2 = commit_snapshot("cli v2 modify delete add")
    print_commit_oid("v2", v2)
    print_find(beta, root_uri)
    print_find(todo, root_uri)
    print_find(changelog, root_uri)

    print_section("v3 second changes")
    mkdir(f"{root_uri}/archive")
    write_text(uris["changelog"], f"# Changelog\n\nCreated {changelog}. Added {gamma}.\n", "replace")
    write_text(uris["archive"], f"# Archive\n\nArchived marker {archive}.\n", "create")
    v3 = commit_snapshot("cli v3 second changes")
    print_commit_oid("v3", v3)
    print_find(gamma, root_uri)
    print_find(archive, root_uri)
    run_ov(["snapshot", "log", "--limit", "10", "-o", "json"])
    show_snapshot("v1", v1)
    show_snapshot("v2", v2)
    show_snapshot("v3", v3)

    print_section("restore to v1")
    source_commit = v1.get("commit_oid")
    if not source_commit:
        raise RuntimeError(f"snapshot commit output did not include commit_oid: {v1}")
    restore_proc = run_ov(["snapshot", "restore", source_commit, root_uri, "-m", "cli restore to v1", "-o", "json"])
    restore = parse_json(restore_proc.stdout).get("result") or {}
    wait_for_task(restore.get("task_id"))
    run_ov(["ls", root_uri, "--recursive", "-o", "json"])
    run_ov(["read", uris["guide"]])
    run_ov(["read", uris["todo"]])
    print_find(alpha, root_uri)
    print_find(beta, root_uri)
    print_find(changelog, root_uri)
    run_ov(["snapshot", "log", "--limit", "10", "-o", "json"])

    print_section("done")
    print("CLI snapshot multi-version example finished")


if __name__ == "__main__":
    main()
