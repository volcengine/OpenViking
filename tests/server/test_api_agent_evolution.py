# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx


async def test_list_experience_trajectories_uses_default_pagination(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    captured = {}

    async def fake_list(*, experience_uri, ctx, limit, offset):
        captured.update(
            experience_uri=experience_uri,
            ctx=ctx,
            limit=limit,
            offset=offset,
        )
        return {
            "experience_uri": experience_uri,
            "items": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "has_more": False,
        }

    monkeypatch.setattr(
        service.agent_evolution,
        "list_trajectories_by_experience",
        fake_list,
    )
    uri = "viking://user/default/memories/experiences/exchange.md"

    response = await client.get(
        "/api/v1/agent-evolution/experiences/trajectories",
        params={"experience_uri": uri},
    )

    assert response.status_code == 200
    assert response.json()["result"]["limit"] == 50
    assert captured["experience_uri"] == uri
    assert captured["limit"] == 50
    assert captured["offset"] == 0


async def test_list_experience_trajectories_rejects_limit_above_1000(
    client: httpx.AsyncClient,
):
    response = await client.get(
        "/api/v1/agent-evolution/experiences/trajectories",
        params={
            "experience_uri": "viking://user/default/memories/experiences/exchange.md",
            "limit": 1001,
        },
    )

    assert response.status_code == 400
