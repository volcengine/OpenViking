"""Retry proven unregistered Commit failures without sending messages again.

An already cleared live file is restored from its original archive using byte
revision checks and deferred processing. No memory writes, model calls, or
generic network retries are made here. Callers serialize writes for each user.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from openviking_sdk import OpenVikingError


class UnprovenCommitRetry(ValueError):
    pass


def require(value: bool, reason: str) -> None:
    if not value:
        raise UnprovenCommitRetry(reason)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def error_code(exc: OpenVikingError) -> str:
    code = exc.code
    return str(getattr(code, "value", code))


async def read_raw(client: Any, uri: str) -> str:
    # Even content/read?raw=true restores Session privacy references. The
    # authenticated download endpoint returns the original stored bytes.
    response = await client._request("GET", "/api/v1/content/download", params={"uri": uri})
    if response.status_code != 200:
        client._handle_response(response)
    require(response.status_code == 200, "Original file download failed")
    return response.content.decode("utf-8")


async def unaccepted_snapshot(client: Any, session_id: str, sent_at: datetime) -> dict[str, Any]:
    session = await client.get_session(session_id, auto_create=False)
    uri = session.get("uri")
    require(
        isinstance(uri, str) and uri.endswith("/sessions/" + session_id),
        "Unexpected Session identity",
    )
    require(session.get("auto_commit_policy") is None, "Automatic commits are enabled")
    entries = await client.ls(uri + "/history", simple=True, abs_limit=4096, node_limit=4096)
    archives = []
    for value in entries:
        if not isinstance(value, str):
            continue
        name = value.rstrip("/").rsplit("/", 1)[-1]
        match = re.fullmatch(r"archive_(\d+)", name)
        if match:
            archives.append((int(match.group(1)), uri + "/history/" + name))
    require(bool(archives), "No failed Phase1 archive is available")
    number, archive = max(archives)
    meta_text = await read_raw(client, archive + "/.meta.json")
    failed_text = await read_raw(client, archive + "/.failed.json")
    session_meta_text = await read_raw(client, uri + "/.meta.json")
    persisted = json.loads(session_meta_text)
    meta = json.loads(meta_text)
    failed = json.loads(failed_text)
    phase = meta.get("phase1", {})
    require(phase.get("status") == "preparing", "Archive is ready or already published")
    stage = failed.get("stage")
    require(
        stage in ("phase1_persist", "publication_lease")
        and "lock acquire timed out" in str(failed.get("error", "")),
        "Failure is not a Phase1 persistence lock conflict",
    )
    created = datetime.fromisoformat(phase["created_at"].replace("Z", "+00:00"))
    require(
        sent_at - timedelta(seconds=5)
        <= created
        <= datetime.now(timezone.utc) + timedelta(seconds=5),
        "Failed archive does not belong to this Commit attempt",
    )
    queued = phase.get("queue_message", {})
    require(
        queued.get("session_id") == session_id
        and queued.get("session_uri") == uri
        and queued.get("archive_uri") == archive
        and queued.get("user") == session.get("user"),
        "Archive owner or Session binding differs",
    )
    require(
        phase.get("keep_recent_count") == 0 and phase.get("retained_message_ids") == [],
        "Commit used retention; whole-live retry is not authorized",
    )
    live = await read_raw(client, uri + "/messages.jsonl")
    archived = await read_raw(client, archive + "/messages.jsonl")
    require(
        bool(archived.strip()) and archived.endswith("\n"),
        "Original archive is empty or incomplete",
    )
    require(
        live == archived or (stage == "publication_lease" and live == ""),
        "Live bytes changed or the failed boundary is not safe to restore",
    )
    messages = [json.loads(line) for line in archived.split("\n") if line.strip()]
    ids = [message["id"] for message in messages]
    require(
        all(isinstance(value, str) and value for value in ids) and len(ids) == len(set(ids)),
        "Original message identities are invalid",
    )
    if stage == "publication_lease":
        require(
            persisted.get("message_count") == 0
            and persisted.get("commit_count") == number
            and session.get("commit_count") == number
            and session.get("message_count") in (0, len(ids)),
            "Persisted publication boundary or logical Session count differs",
        )
    else:
        require(
            number > int(persisted.get("commit_count", 0))
            and persisted.get("message_count") == len(ids) == session.get("message_count"),
            "Session already advanced past the failed persistence boundary",
        )
    require(
        ids == phase.get("original_message_ids") == phase.get("archived_message_ids"),
        "Live messages differ from the original rejected snapshot",
    )
    task_id = queued.get("task_id")
    require(isinstance(task_id, str) and bool(task_id), "Original Task intent has no identity")
    # Frozen native code creates the Task only after Phase1 ready, and publishes
    # QueueFS work only after Task creation. The copied SDK returns None on 404.
    require(await client.get_task(task_id) is None, "Original Task exists; never repeat Commit")
    return {
        "session_uri": uri,
        "archive_uri": archive,
        "unregistered_task_id": task_id,
        "message_count": len(ids),
        "live_SHA": digest(live),
        "archive_meta_SHA": digest(meta_text),
        "archive_raw_SHA": digest(archived),
        "session_meta_SHA": digest(session_meta_text),
        "failure_SHA": digest(failed_text),
        "commit_count": int(session.get("commit_count", 0)),
        "failed_stage": stage,
        "_restore_live": live == "",
        "_archive_raw": archived,
    }


async def restore_original_live(client: Any, snapshot: dict[str, Any]) -> None:
    require(
        snapshot["_restore_live"] and snapshot["failed_stage"] == "publication_lease",
        "Only an empty live file after a proven publication failure can be restored",
    )
    # Native batch-write checks the old byte revision under its file lock. Binary
    # content preserves native message IDs/privacy bytes; defer_processing avoids
    # semantic/indexing work. The Session metadata and failed archive are retained.
    response = await client._request(
        "POST",
        "/api/v1/content/batch-write",
        json={
            "root_uri": snapshot["session_uri"],
            "wait": True,
            "defer_processing": True,
            "operations": [
                {
                    "uri": snapshot["session_uri"] + "/messages.jsonl",
                    "mode": "replace",
                    "expected_sha256": digest(""),
                    "content_base64": base64.b64encode(
                        snapshot["_archive_raw"].encode("utf-8")
                    ).decode("ascii"),
                }
            ],
        },
    )
    client._handle_response(response)
    require(
        await read_raw(client, snapshot["session_uri"] + "/messages.jsonl")
        == snapshot["_archive_raw"],
        "Restored live bytes do not match the original archive",
    )


def audit_proof(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in snapshot.items() if not key.startswith("_")}


async def commit_session_with_proof(
    client: Any, session_id: str, audit: Callable[..., None]
) -> dict[str, Any]:
    for attempt in range(3):
        sent_at = datetime.now(timezone.utc)
        try:
            return await client.commit_session(session_id, keep_recent_count=0)
        except OpenVikingError as exc:
            if (
                error_code(exc) != "CONFLICT"
                or "lock acquire timed out" not in str(exc)
                or attempt == 2
            ):
                raise
            try:
                before = await unaccepted_snapshot(client, session_id, sent_at)
                await asyncio.sleep(2**attempt)
                after = await unaccepted_snapshot(client, session_id, sent_at)
                require(before == after, "Session changed while checking the rejected Commit")
                if after["_restore_live"]:
                    await restore_original_live(client, after)
                    restored = await unaccepted_snapshot(client, session_id, sent_at)
                    expected = dict(after, live_SHA=after["archive_raw_SHA"], _restore_live=False)
                    require(
                        restored == expected,
                        "Session or original archive changed during live restoration",
                    )
                    audit("commit_original_live_restored", **audit_proof(restored))
            except Exception as proof_error:
                audit(
                    "commit_retry_not_proven",
                    reason=type(proof_error).__name__,
                    error_code=error_code(exc),
                    attempt=attempt + 1,
                )
                raise exc from proof_error
            audit("commit_unregistered_phase1_retry", attempt=attempt + 1, **audit_proof(after))
    raise RuntimeError("Commit retry loop exited unexpectedly")
