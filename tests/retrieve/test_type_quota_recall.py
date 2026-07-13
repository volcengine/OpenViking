# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace

from openviking.retrieve.type_quota_recall import search_type_quota_recall
from openviking.server.identity import RequestContext, Role
from openviking_cli.session.user_id import UserIdentifier


class _FakeFindResult:
    def __init__(self, memories=None):
        self.memories = memories or []


async def test_independent_type_searches_start_concurrently():
    started_targets: list[str] = []
    all_started = asyncio.Event()

    async def fake_find(**kwargs):
        started_targets.append(kwargs["target_uri"])
        if len(started_targets) == 4:
            all_started.set()
        await all_started.wait()
        return _FakeFindResult()

    service = SimpleNamespace(
        search=SimpleNamespace(find=fake_find),
        fs=SimpleNamespace(),
    )
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("test_user"),
        role=Role.USER,
        actor_peer_id="current",
    )

    result = await asyncio.wait_for(
        search_type_quota_recall(
            service=service,
            ctx=ctx,
            query="parallel recall",
            peer_scope="actor",
            quotas={
                "events": 1,
                "entities": 1,
                "preferences": 0,
                "experiences": 0,
            },
        ),
        timeout=1.0,
    )

    assert set(started_targets) == {
        "viking://user/test_user/memories/events",
        "viking://user/test_user/peers/current/memories/events",
        "viking://user/test_user/memories/entities",
        "viking://user/test_user/peers/current/memories/entities",
    }
    assert result.stats["searched"] == {
        "events": 0,
        "entities": 0,
        "preferences": 0,
        "experiences": 0,
    }


async def test_parallel_search_preserves_type_order():
    async def fake_find(**kwargs):
        target_uri = kwargs["target_uri"]
        memory_type = target_uri.rsplit("/", 1)[-1]
        if memory_type == "events":
            await asyncio.sleep(0.02)
        if "/peers/" in target_uri:
            return _FakeFindResult()
        return _FakeFindResult(
            [
                {
                    "uri": f"{target_uri}/{memory_type}.md",
                    "score": 0.9,
                    "abstract": f"{memory_type} abstract",
                }
            ]
        )

    async def fake_read(uri, **kwargs):
        del kwargs
        return f"content for {uri}"

    service = SimpleNamespace(
        search=SimpleNamespace(find=fake_find),
        fs=SimpleNamespace(read=fake_read),
    )
    ctx = RequestContext(
        user=UserIdentifier.the_default_user("test_user"),
        role=Role.USER,
        actor_peer_id="current",
    )

    result = await search_type_quota_recall(
        service=service,
        ctx=ctx,
        query="deterministic recall",
        peer_scope="actor",
        quotas={
            "events": 1,
            "entities": 1,
            "preferences": 0,
            "experiences": 0,
        },
    )

    assert [entry.type for entry in result.entries] == ["events", "entities"]
    assert [entry.rank for entry in result.entries] == [1, 1]
