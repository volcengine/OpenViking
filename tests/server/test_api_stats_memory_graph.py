# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Tests for memory graph health statistics endpoint."""

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.graph_health import _load_schema_heading_requirements
from openviking.session.memory.utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier


async def _write_memory(service, uri: str, memory_file: MemoryFile) -> None:
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    await service.viking_fs.write_file(uri, MemoryFileUtils.write(memory_file), ctx=ctx)


async def test_memory_graph_health_treats_missing_root_as_empty_graph(client):
    resp = await client.get(
        "/api/v1/stats/memory-graph",
        params={"uri": "viking://agent/default/memories"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    result = body["result"]
    assert result["healthy"] is True
    assert result["scanned_entry_count"] == 0
    assert result["memory_file_count"] == 0
    assert result["samples"] == []


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


async def test_memory_graph_health_reports_experience_quality_signals(client, service):
    root_uri = "viking://agent/default/memories"
    required_content_headings = _load_schema_heading_requirements({"experiences"})["experiences"][
        "content"
    ]
    omitted_heading = required_content_headings[1]
    first_exp_uri = f"{root_uri}/experiences/refund_request_process.md"
    second_exp_uri = f"{root_uri}/experiences/request_refund_processing.md"
    traj_uri = f"{root_uri}/trajectories/refund.md"
    first_link = {
        "from_uri": first_exp_uri,
        "to_uri": traj_uri,
        "link_type": "derived_from",
        "weight": 1.0,
    }
    second_link = {
        "from_uri": second_exp_uri,
        "to_uri": traj_uri,
        "link_type": "derived_from",
        "weight": 1.0,
    }

    await _write_memory(
        service,
        first_exp_uri,
        MemoryFile(
            content="\n\n".join(
                f"## {heading}\n- Refund request requires checking eligibility."
                for heading in required_content_headings
            ),
            memory_type="experiences",
            links=[first_link],
        ),
    )
    await _write_memory(
        service,
        second_exp_uri,
        MemoryFile(
            content="\n\n".join(
                f"## {heading}\n- Request refund processing requires checking eligibility."
                for heading in required_content_headings
                if heading != omitted_heading
            ),
            memory_type="experiences",
            links=[second_link],
        ),
    )
    await _write_memory(
        service,
        traj_uri,
        MemoryFile(
            content="Refund trajectory",
            memory_type="trajectories",
            backlinks=[first_link, second_link],
        ),
    )

    resp = await client.get("/api/v1/stats/memory-graph", params={"uri": root_uri})

    assert resp.status_code == 200
    quality = resp.json()["result"]["experience_quality"]
    assert quality["name_similar_pair_count"] == 1
    assert quality["content_similar_pair_count"] == 1
    assert quality["source_overlap_pair_count"] == 1
    assert quality["duplicate_exact_source_set_count"] == 1
    assert quality["source_links_per_experience"]["linkless"] == 0
    assert quality["source_links_per_experience"]["single_source"] == 2
    assert quality["source_links_per_experience"]["single_source_rate"] == 1.0
    assert quality["source_links_per_experience"]["p90"] == 1
    assert quality["content_chars"]["empty"] == 0
    assert quality["required_heading_check_enabled"] is True
    assert quality["required_heading_fields"]["content"] == required_content_headings
    assert quality["required_headings"] == required_content_headings
    assert quality["complete_required_heading_count"] == 1
    assert quality["missing_required_heading_count"] == 1
    assert quality["missing_required_heading_examples"][0]["missing"] == {
        "content": [omitted_heading]
    }


async def test_memory_graph_health_reports_empty_experience_content(client, service):
    root_uri = "viking://agent/default/memories"
    exp_uri = f"{root_uri}/experiences/metadata_only_refund.md"
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
        MemoryFile(content="", memory_type="experiences", links=[link]),
    )
    await _write_memory(
        service,
        traj_uri,
        MemoryFile(content="Refund trajectory", memory_type="trajectories", backlinks=[link]),
    )

    resp = await client.get("/api/v1/stats/memory-graph", params={"uri": root_uri})

    assert resp.status_code == 200
    quality = resp.json()["result"]["experience_quality"]
    assert quality["content_chars"]["empty"] == 1
    assert quality["required_heading_check_enabled"] is True
    assert quality["missing_required_heading_count"] == 1
