from __future__ import annotations

import pprint
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from openviking_cli.utils.config.ovcli_config import load_ovcli_config

OVCLI_CONFIG_FILE = "/home/byteide/.openviking/ovcli.conf"
WORKSPACE_URI = "viking://resources/snapshot_http_demo"
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


def print_result(label: str, value: Any) -> None:
    print(f"\n--- {label} ---")
    pprint.pp(value, width=120)


def build_headers(config: Any) -> dict[str, str]:
    headers = dict(config.extra_headers or {})
    if config.api_key:
        headers["X-API-Key"] = config.api_key
    if config.account:
        headers["X-OpenViking-Account"] = config.account
    if config.user:
        headers["X-OpenViking-User"] = config.user
    if config.actor_peer_id:
        headers["X-OpenViking-Actor-Peer"] = config.actor_peer_id
    return headers


def request_json(client: httpx.Client, method: str, path: str, label: str | None = None, **kwargs: Any) -> dict[str, Any]:
    response = client.request(method, path, **kwargs)
    output_label = label or f"{method} {path}"
    print_result(f"{output_label} status", response.status_code)
    if response.headers.get("content-type", "").startswith("application/json"):
        data = response.json()
        print_result(f"{output_label} response", data)
        response.raise_for_status()
        return data
    print(response.text)
    response.raise_for_status()
    return {"raw": response.text}


def mkdir(client: httpx.Client, uri: str) -> None:
    request_json(client, "POST", "/api/v1/fs/mkdir", json={"uri": uri})


def write_text(client: httpx.Client, uri: str, content: str, mode: str) -> None:
    print(f"write: {uri}")
    print(content.rstrip())
    request_json(
        client,
        "POST",
        "/api/v1/content/write",
        json={"uri": uri, "content": content, "mode": mode, "wait": True},
    )


def remove_resource(client: httpx.Client, uri: str) -> None:
    request_json(client, "DELETE", "/api/v1/fs", params={"uri": uri, "recursive": False, "wait": True})


def read_text(client: httpx.Client, uri: str) -> None:
    request_json(client, "GET", "/api/v1/content/read", label=f"read content after restore: {uri}", params={"uri": uri})


def print_find(client: httpx.Client, query: str, root_uri: str) -> None:
    request_json(
        client,
        "POST",
        "/api/v1/search/find",
        json={"query": query, "target_uri": root_uri, "limit": 10},
    )


def commit_snapshot(client: httpx.Client, message: str) -> dict[str, Any]:
    data = request_json(
        client,
        "POST",
        "/api/v1/snapshot/commit",
        json={"message": message},
    )
    return data.get("result") or {}


def show_snapshot(client: httpx.Client, target_ref: str) -> None:
    request_json(client, "GET", "/api/v1/snapshot/show", label=f"snapshot show {target_ref}", params={"target_ref": target_ref})


def wait_for_task(client: httpx.Client, task_id: str | None, *, timeout: float = WAIT_TIMEOUT, poll_interval: float = 0.5) -> None:
    """Poll a background task by id until it reaches a terminal state.

    restore schedules vectorization/indexing asynchronously and returns a
    ``task_id``; finding before that task completes can read stale vectors.
    """
    if not task_id:
        print("wait_for_task: no task_id (no vector side-effects to await)")
        return
    deadline = time.time() + timeout
    while True:
        data = request_json(client, "GET", f"/api/v1/tasks/{task_id}", label=f"task status {task_id[:12]}")
        status = (data.get("result") or {}).get("status")
        if status in ("completed", "failed"):
            print(f"wait_for_task {task_id[:12]}: {status}")
            if status == "failed":
                raise RuntimeError(f"task {task_id} failed: {(data.get('result') or {}).get('error')}")
            return
        if time.time() > deadline:
            raise TimeoutError(f"task {task_id} not complete after {timeout}s (status={status})")
        time.sleep(poll_interval)


def main() -> None:
    config_path = str(Path(OVCLI_CONFIG_FILE).resolve())
    config = load_ovcli_config(config_path)
    if config is None or not config.url:
        raise RuntimeError(f"missing url in {config_path}")

    run_id, root_uri = unique_run_uri()
    uris = resource_uris(root_uri)
    alpha = f"alpha_{run_id}"
    beta = f"beta_{run_id}"
    todo = f"todo_{run_id}"
    changelog = f"changelog_{run_id}"
    gamma = f"gamma_{run_id}"
    archive = f"archive_{run_id}"

    with httpx.Client(
        base_url=config.url.rstrip("/"),
        headers=build_headers(config),
        timeout=config.timeout,
    ) as client:
        print_section("setup")
        print(f"config: {config_path}")
        print(f"server: {config.url}")
        print(f"workspace: {root_uri}")
        mkdir(client, root_uri)
        mkdir(client, f"{root_uri}/notes")

        print_section("v1 initial import")
        write_text(client, uris["guide"], f"# Guide\n\nInitial HTTP content with {alpha}.\n", "create")
        write_text(client, uris["todo"], f"# Todo\n\nRemember {todo}.\n", "create")
        v1 = commit_snapshot(client, "http v1 initial import")
        print_find(client, alpha, root_uri)

        print_section("v2 modify delete add")
        write_text(client, uris["guide"], f"# Guide\n\nUpdated HTTP content with {beta}.\n", "replace")
        remove_resource(client, uris["todo"])
        write_text(client, uris["changelog"], f"# Changelog\n\nCreated {changelog}.\n", "create")
        v2 = commit_snapshot(client, "http v2 modify delete add")
        print_find(client, beta, root_uri)
        print_find(client, todo, root_uri)
        print_find(client, changelog, root_uri)

        print_section("v3 second changes")
        mkdir(client, f"{root_uri}/archive")
        write_text(client, uris["changelog"], f"# Changelog\n\nCreated {changelog}. Added {gamma}.\n", "replace")
        write_text(client, uris["archive"], f"# Archive\n\nArchived marker {archive}.\n", "create")
        v3 = commit_snapshot(client, "http v3 second changes")
        print_find(client, gamma, root_uri)
        print_find(client, archive, root_uri)
        request_json(
            client,
            "GET",
            "/api/v1/snapshot/log",
            label="snapshot log before restore",
            params={"branch": "main", "limit": 10},
        )
        show_snapshot(client, v1.get("commit_oid", ""))
        show_snapshot(client, v2.get("commit_oid", ""))
        show_snapshot(client, v3.get("commit_oid", ""))

        print_section("restore to v1")
        restore = request_json(
            client,
            "POST",
            "/api/v1/snapshot/restore",
            label=f"restore workspace to v1 {v1.get('commit_oid')}",
            json={"project_dir": root_uri, "source_commit": v1.get("commit_oid"), "message": "http restore to v1"},
        )
        wait_for_task(client, (restore.get("result") or {}).get("task_id"))
        request_json(
            client,
            "GET",
            "/api/v1/fs/ls",
            label="list workspace after restore",
            params={"uri": root_uri, "recursive": True},
        )
        read_text(client, uris["guide"])
        read_text(client, uris["todo"])
        print_find(client, alpha, root_uri)
        print_find(client, beta, root_uri)
        print_find(client, changelog, root_uri)
        request_json(
            client,
            "GET",
            "/api/v1/snapshot/log",
            label="snapshot log after restore",
            params={"branch": "main", "limit": 10},
        )

    print_section("done")
    print("HTTP API snapshot multi-version example finished")


if __name__ == "__main__":
    main()
