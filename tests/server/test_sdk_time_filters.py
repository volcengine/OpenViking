# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from datetime import datetime, timedelta, timezone

from openviking.server.identity import RequestContext, Role
from openviking.utils.time_utils import format_iso8601
from openviking_cli.session.user_id import UserIdentifier
from tests.server.test_http_client_sdk import http_client as http_client


async def _seed_time_filter_records(service):
    ctx = RequestContext(user=UserIdentifier("sdk_test_account", "sdk_test_user"), role=Role.ADMIN)
    root = "viking://resources/time-filter"
    now = datetime.now(timezone.utc)
    recent = format_iso8601(now - timedelta(minutes=30))
    old = format_iso8601(now - timedelta(days=10))
    embedding = await service.embedding_provider.embed(ctx.account_id, "OAuth", is_query=True)
    for name, created_at, updated_at in (
        ("new", recent, recent),
        ("edited", old, recent),
        ("stale", old, old),
    ):
        await service.vikingdb_manager.upsert(
            {
                "uri": f"{root}/{name}.md",
                "parent_uri": root,
                "is_leaf": True,
                "abstract": "OAuth note",
                "context_type": "resource",
                "created_at": created_at,
                "updated_at": updated_at,
                "vector": embedding.dense_vector,
                "account_id": ctx.account_id,
                "level": 2,
            },
            ctx=ctx,
        )
    return root


async def test_sdk_find_respects_since_and_time_field(http_client):
    client, service = http_client
    root = await _seed_time_filter_records(service)

    result = await client.find(
        query="OAuth",
        target_uri=root,
        options={"since": "2h", "time_field": "created_at"},
    )

    assert [hit["uri"] for hit in result["resources"]] == [f"{root}/new.md"]


async def test_sdk_search_respects_since_default_updated_at(http_client):
    client, service = http_client
    root = await _seed_time_filter_records(service)

    for options, names in (({"since": "2h"}, {"new", "edited"}), ({"until": "7d"}, {"stale"})):
        result = await client.search(query="OAuth", target_uri=root, options=options)

        assert {hit["uri"] for hit in result["resources"]} == {
            f"{root}/{name}.md" for name in names
        }
