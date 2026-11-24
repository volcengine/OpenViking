# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from unittest.mock import AsyncMock, Mock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.service.agent_evolution_service import AgentEvolutionService
from openviking_cli.exceptions import InvalidArgumentError
from openviking_cli.session.user_id import UserIdentifier


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier("account", "alice"), role=Role.USER)


@pytest.mark.asyncio
async def test_experience_trajectory_queries_reject_other_user_uri():
    service = AgentEvolutionService(viking_fs=Mock(), vikingdb=Mock())

    for query in (
        service.list_trajectories_by_experience,
        service.get_experience_outcome_distribution,
    ):
        with pytest.raises(InvalidArgumentError, match="owned by the current user"):
            await query(
                experience_uri="viking://user/bob/memories/experiences/exchange.md",
                ctx=_ctx(),
            )


@pytest.mark.asyncio
async def test_list_trajectories_by_experience_rejects_invalid_date_range():
    service = AgentEvolutionService(viking_fs=Mock(), vikingdb=Mock())

    with pytest.raises(InvalidArgumentError, match="start_date must be earlier"):
        await service.list_trajectories_by_experience(
            experience_uri="viking://user/alice/memories/experiences/exchange.md",
            ctx=_ctx(),
            start_date="2026-08-11",
            end_date="2026-08-10",
        )


@pytest.mark.asyncio
async def test_list_trajectories_by_experience_rejects_limit_above_1000():
    service = AgentEvolutionService(viking_fs=Mock(), vikingdb=Mock())

    with pytest.raises(InvalidArgumentError):
        await service.list_trajectories_by_experience(
            experience_uri="viking://user/alice/memories/experiences/exchange.md",
            ctx=_ctx(),
            limit=1001,
        )


@pytest.mark.asyncio
async def test_list_trajectories_by_experience_accepts_1000_and_paginates_all_results():
    experience_uri = "viking://user/alice/memories/experiences/exchange.md"
    viking_fs = Mock()
    viking_fs.stat = AsyncMock(return_value={"isDir": False})
    vikingdb = Mock()
    vikingdb.filter = AsyncMock(
        side_effect=[
            [
                {"uri": f"viking://user/alice/memories/trajectories/{index}.md"}
                for index in range(1000)
            ],
            [{"uri": "viking://user/alice/memories/trajectories/1000.md"}],
        ]
    )
    vikingdb.count = AsyncMock(side_effect=[1001, 1001])
    service = AgentEvolutionService(viking_fs=viking_fs, vikingdb=vikingdb)

    first_page = await service.list_trajectories_by_experience(
        experience_uri=experience_uri,
        ctx=_ctx(),
        limit=1000,
    )
    second_page = await service.list_trajectories_by_experience(
        experience_uri=experience_uri,
        ctx=_ctx(),
        limit=1000,
        offset=1000,
    )

    assert len(first_page["items"]) == 1000
    assert first_page["has_more"] is True
    assert second_page["items"] == [{"uri": "viking://user/alice/memories/trajectories/1000.md"}]
    assert second_page["has_more"] is False
    assert [call.kwargs["offset"] for call in vikingdb.filter.await_args_list] == [0, 1000]
