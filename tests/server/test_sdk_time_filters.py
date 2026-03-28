# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime, timedelta, timezone

from openviking.server.identity import RequestContext, Role
from openviking.utils.time_utils import format_iso8601
from openviking_cli.session.user_id import UserIdentifier


async def _seed_find_time_filter_records(svc, query: str) -> dict[str, str]:
    embedder = svc.vikingdb_manager.get_embedder()
    vector = embedder.embed(query).dense_vector
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    now = datetime.now(timezone.utc)
    recent_time = format_iso8601(now - timedelta(hours=1))
    old_time = format_iso8601(now - timedelta(days=10))

    records = {
        "recent_email": {
            "uri": "viking://resources/email/recent-invoice.md",
            "parent_uri": "viking://resources/email",
            "abstract": "Recent invoice follow-up thread",
            "created_at": recent_time,
            "updated_at": recent_time,
        },
        "old_email": {
            "uri": "viking://resources/email/old-invoice.md",
            "parent_uri": "viking://resources/email",
            "abstract": "Older invoice follow-up thread",
            "created_at": old_time,
            "updated_at": old_time,
        },
    }

    for record in records.values():
        await svc.vikingdb_manager.upsert(
            {
                "uri": record["uri"],
                "parent_uri": record["parent_uri"],
                "is_leaf": True,
                "abstract": record["abstract"],
                "context_type": "resource",
                "category": "",
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
                "active_count": 0,
                "vector": vector,
                "meta": {},
                "related_uri": [],
                "account_id": "default",
                "owner_space": "",
                "level": 2,
            },
            ctx=ctx,
        )

    return {name: record["uri"] for name, record in records.items()}


async def _seed_search_time_filter_records(svc, query: str) -> dict[str, str]:
    embedder = svc.vikingdb_manager.get_embedder()
    vector = embedder.embed(query).dense_vector
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    now = datetime.now(timezone.utc)
    recent_time = format_iso8601(now - timedelta(minutes=30))
    old_time = format_iso8601(now - timedelta(days=30))

    records = {
        "recent_note": {
            "uri": "viking://resources/watch-schedule/recent-search-time-filter.md",
            "parent_uri": "viking://resources/watch-schedule",
            "abstract": "Recent watch vs scheduled discussion",
            "created_at": recent_time,
            "updated_at": recent_time,
        },
        "old_note": {
            "uri": "viking://resources/watch-schedule/old-search-time-filter.md",
            "parent_uri": "viking://resources/watch-schedule",
            "abstract": "Old watch vs scheduled discussion",
            "created_at": old_time,
            "updated_at": old_time,
        },
    }

    for record in records.values():
        await svc.vikingdb_manager.upsert(
            {
                "uri": record["uri"],
                "parent_uri": record["parent_uri"],
                "is_leaf": True,
                "abstract": record["abstract"],
                "context_type": "resource",
                "category": "",
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
                "active_count": 0,
                "vector": vector,
                "meta": {},
                "related_uri": [],
                "account_id": "default",
                "owner_space": "",
                "level": 2,
            },
            ctx=ctx,
        )

    return {name: record["uri"] for name, record in records.items()}


async def test_sdk_find_respects_since_and_time_field(http_client):
    client, svc = http_client
    uris = await _seed_find_time_filter_records(svc, "invoice follow-up")

    result = await client.find(
        query="invoice follow-up",
        target_uri="viking://resources/email",
        since="2d",
        time_field="created_at",
        limit=10,
    )

    found_uris = {item.uri for item in result.resources}
    assert uris["recent_email"] in found_uris
    assert uris["old_email"] not in found_uris


async def test_sdk_search_respects_since_default_updated_at(http_client):
    client, svc = http_client
    uris = await _seed_search_time_filter_records(svc, "watch vs scheduled")

    recent_result = await client.search(
        query="watch vs scheduled",
        target_uri="viking://resources/watch-schedule",
        since="2h",
        limit=10,
    )
    old_result = await client.search(
        query="watch vs scheduled",
        target_uri="viking://resources/watch-schedule",
        until="2000-01-01",
        limit=10,
    )

    recent_uris = {item.uri for item in recent_result.resources}
    old_uris = {item.uri for item in old_result.resources}

    assert uris["recent_note"] in recent_uris
    assert uris["old_note"] not in recent_uris
    assert uris["recent_note"] not in old_uris
