# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import httpx


async def test_list_experience_trajectories_uses_default_pagination(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    captured = {}

    async def fake_list(*, experience_uri, ctx, limit, offset, start_date, end_date):
        captured.update(
            experience_uri=experience_uri,
            ctx=ctx,
            limit=limit,
            offset=offset,
            start_date=start_date,
            end_date=end_date,
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
    assert captured["start_date"] is None
    assert captured["end_date"] is None


async def test_list_experience_trajectories_passes_date_range(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    captured = {}

    async def fake_list(**kwargs):
        captured.update(kwargs)
        return {
            "experience_uri": kwargs["experience_uri"],
            "items": [],
            "total": 0,
            "limit": kwargs["limit"],
            "offset": kwargs["offset"],
            "has_more": False,
        }

    monkeypatch.setattr(service.agent_evolution, "list_trajectories_by_experience", fake_list)
    response = await client.get(
        "/api/v1/agent-evolution/experiences/trajectories",
        params={
            "experience_uri": "viking://user/default/memories/experiences/exchange.md",
            "start_date": "2026-08-01",
            "end_date": "2026-08-10",
        },
    )

    assert response.status_code == 200
    assert captured["start_date"] == "2026-08-01"
    assert captured["end_date"] == "2026-08-10"


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


async def test_get_experience_outcome_distribution(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    captured = {}

    async def fake_get(*, experience_uri, ctx, start_date, end_date):
        captured.update(
            experience_uri=experience_uri,
            ctx=ctx,
            start_date=start_date,
            end_date=end_date,
        )
        return {
            "experience_uri": experience_uri,
            "outcome_distribution": [{"outcome": "success", "count": 2}],
        }

    monkeypatch.setattr(
        service.agent_evolution,
        "get_experience_outcome_distribution",
        fake_get,
    )
    uri = "viking://user/default/memories/experiences/exchange.md"

    response = await client.get(
        "/api/v1/agent-evolution/experiences/outcomes",
        params={"experience_uri": uri},
    )

    assert response.status_code == 200
    assert response.json()["result"] == {
        "experience_uri": uri,
        "outcome_distribution": [{"outcome": "success", "count": 2}],
    }
    assert captured["experience_uri"] == uri
    assert captured["start_date"] is None
    assert captured["end_date"] is None


async def test_get_experience_outcome_distribution_passes_date_range(
    client: httpx.AsyncClient,
    service,
    monkeypatch,
):
    captured = {}

    async def fake_get(**kwargs):
        captured.update(kwargs)
        return {
            "experience_uri": kwargs["experience_uri"],
            "outcome_distribution": [],
        }

    monkeypatch.setattr(
        service.agent_evolution,
        "get_experience_outcome_distribution",
        fake_get,
    )
    response = await client.get(
        "/api/v1/agent-evolution/experiences/outcomes",
        params={
            "experience_uri": "viking://user/default/memories/experiences/exchange.md",
            "start_date": "2026-08-01",
            "end_date": "2026-08-10",
        },
    )

    assert response.status_code == 200
    assert captured["start_date"] == "2026-08-01"
    assert captured["end_date"] == "2026-08-10"
