"""Root-only, credential-free metadata export for offline collectors."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque

from openviking.observability.reporting.facts import file_metadata
from openviking.server.identity import RequestContext, Role
from openviking.storage.expr import Eq
from openviking.storage.vector_ids import seed_uri_for_vector_id
from openviking_cli.session.user_id import UserIdentifier


async def inventory_lines(service, manager, *, account_id=None, page_size=500, pause_seconds=0.05):
    """Export a scan window, including true empty spaces and an explicit final marker.

    A failure emits a failed marker; partial scans must never replace a complete inventory.
    Files are listed without reading content. Vector values and credentials are excluded.
    """
    started = int(time.time() * 1000)
    count = 0

    def line(kind, data):
        nonlocal count
        count += 1
        return json.dumps({"event_name": kind, **data}, ensure_ascii=False) + "\n"

    try:
        accounts = await manager.list_accounts()
        matched_account = False
        for account in accounts:
            identifier = str(account["account_id"])
            if account_id is not None and identifier != account_id:
                continue
            matched_account = True
            context = RequestContext(
                user=UserIdentifier(identifier, "default"), role=Role(Role.ROOT), bypass_acl=True
            )
            yield line("inventory_scope", {"account_id": identifier, "scan_started_at": started})
            page = 1
            while True:
                users = await manager.list_users_page(
                    identifier, limit=page_size, page=page, expose_key=False
                )
                for user in users["users"]:
                    yield line(
                        "inventory_user",
                        {
                            "account_id": identifier,
                            "user_id": user["user_id"],
                            "role": user.get("role"),
                        },
                    )
                if page * page_size >= users["total"]:
                    break
                page += 1
            fs = service.viking_fs
            if fs is None:
                raise RuntimeError("Filesystem unavailable")
            queue = deque(["viking://"])
            while queue:
                parent = queue.popleft()
                offset = 0
                while True:
                    entries = await fs.ls(
                        parent,
                        ctx=context,
                        node_limit=page_size,
                        offset=offset,
                        show_all_hidden=True,
                        sort_by="name",
                    )
                    for entry in entries:
                        if entry.get("access") == "denied":
                            raise RuntimeError("Inventory traversal denied")
                        uri = entry["uri"]
                        metadata = file_metadata(uri, context, is_dir=entry.get("isDir", False))
                        if metadata is None:
                            continue
                        yield line("inventory_file", {**metadata, "account_id": identifier})
                        if entry.get("isDir"):
                            queue.append(uri)
                        elif uri.endswith("/SKILL.md") and metadata.get("skill_uri"):
                            yield line(
                                "inventory_skill",
                                {
                                    "account_id": identifier,
                                    "skill_uri": metadata["skill_uri"],
                                    "scope": metadata["scope"],
                                },
                            )
                    if len(entries) < page_size:
                        break
                    offset += len(entries)
                    await asyncio.sleep(pause_seconds)
            vectors = fs._get_vector_store()
            if vectors is None:
                raise RuntimeError("Vector metadata backend unavailable")
            async for record in vectors._strict_scan(
                context,
                Eq("account_id", identifier),
                output_fields=["id", "uri", "level", "context_type", "owner_user_id", "updated_at"],
                batch_size=page_size,
                what="metric_inventory",
            ):
                data = {
                    key: record.get(key)
                    for key in ("id", "uri", "level", "context_type", "owner_user_id", "updated_at")
                }
                data.update(
                    account_id=identifier,
                    file_uri=seed_uri_for_vector_id(str(record["uri"]), record.get("level")),
                )
                yield line("inventory_vector_record", data)
        if account_id is not None and not matched_account:
            raise LookupError("Requested account is not present in the registry")
        marker = {
            "event_name": "inventory_batch_end",
            "status": "complete",
            "row_count": count,
            "scan_started_at": started,
            "scan_finished_at": int(time.time() * 1000),
            "consistency": "scan_window",
        }
    except Exception:
        marker = {
            "event_name": "inventory_batch_end",
            "status": "failed",
            "row_count": count,
            "scan_started_at": started,
            "scan_finished_at": int(time.time() * 1000),
        }
    yield json.dumps(marker) + "\n"
