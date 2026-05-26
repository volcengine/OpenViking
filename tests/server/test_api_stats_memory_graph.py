# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for memory graph health statistics endpoint."""

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier


async def _write_memory(service, uri: str, memory_file: MemoryFile) -> None:
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.viking_fs.write_file(uri, MemoryFileUtils.write(memory_file), ctx=ctx)


async def test_memory_graph_health_reports_healthy_bidirectional_links(client, service):
    root_uri = "viking://agent/default/memories"
    exp_uri = f"{root_uri}/experiences/refund.md"
    traj_uri = f"{root_uri}/trajectories/refund.md"
    link = {
        "from_uri": exp_uri,
        "to_uri": traj_uri,
        "link_type": "derived_from",
        "weight": 1.0,
    }

    await _write_memory(
        service,
        exp_uri,
        MemoryFile(content="Refund lesson", memory_type="experiences", links=[link]),
    )
    await _write_memory(
        service,
        traj_uri,
        MemoryFile(content="Refund trajectory", memory_type="trajectories", backlinks=[link]),
    )

    resp = await client.get("/api/v1/stats/memory-graph", params={"uri": root_uri})

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["healthy"] is True
    assert result["memory_type_counts"] == {"experiences": 1, "trajectories": 1}
    assert result["forward_link_count"] == 1
    assert result["backlink_count"] == 1
    assert result["experience_to_trajectory_links"] == 1
    assert result["trajectory_from_experience_backlinks"] == 1
    assert result["source_linkless_experience_count"] == 0
    assert result["broken_endpoint_count"] == 0
    assert result["missing_backlink_count"] == 0
    assert result["missing_forward_link_count"] == 0


async def test_memory_graph_health_reports_missing_backlink(client, service):
    root_uri = "viking://agent/default/memories"
    exp_uri = f"{root_uri}/experiences/exchange.md"
    traj_uri = f"{root_uri}/trajectories/exchange.md"
    link = {
        "from_uri": exp_uri,
        "to_uri": traj_uri,
        "link_type": "derived_from",
        "weight": 1.0,
    }

    await _write_memory(
        service,
        exp_uri,
        MemoryFile(content="Exchange lesson", memory_type="experiences", links=[link]),
    )
    await _write_memory(
        service,
        traj_uri,
        MemoryFile(content="Exchange trajectory", memory_type="trajectories"),
    )

    resp = await client.get(
        "/api/v1/stats/memory-graph",
        params={"uri": root_uri, "sample_limit": 5},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["healthy"] is False
    assert result["missing_backlink_count"] == 1
    assert result["broken_endpoint_count"] == 0
    assert result["samples"][0]["kind"] == "missing_backlink"
    assert result["samples"][0]["uri"] == exp_uri
    assert result["samples"][0]["peer_uri"] == traj_uri
