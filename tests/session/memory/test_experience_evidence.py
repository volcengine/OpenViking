# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.experience_evidence import (
    CandidateExperienceEvidence,
    ExperienceEvidenceBundle,
    ExperienceEvidenceLoader,
    ExperienceEvidenceQuery,
    TrajectoryEvidence,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.telemetry.replay import decode_value, encode_value
from openviking_cli.session.user_id import UserIdentifier


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    """These isolated loader tests do not need the session integration client."""
    yield


def _ctx() -> RequestContext:
    return RequestContext(UserIdentifier("account", "user"), Role.USER)


def _raw(memory_file: MemoryFile) -> str:
    return MemoryFileUtils.write(memory_file)


def _query(*, case_uri: str = "") -> ExperienceEvidenceQuery:
    return ExperienceEvidenceQuery(
        trajectory_summary="duplicate booking failure",
        trajectory_uri="viking://user/user/memories/trajectories/current.md",
        experience_dir="viking://user/user/memories/experiences",
        trajectory_dir="viking://user/user/memories/trajectories",
        case_uri=case_uri,
        case_name="case-1",
        task_signature="book-flight",
    )


def test_experience_evidence_bundle_codec_round_trips_raw_ordered_evidence() -> None:
    source = TrajectoryEvidence(
        memory_file=MemoryFile(
            uri="viking://trajectory/source",
            content="source",
            memory_type="trajectories",
            extra_fields={"outcome": "failure", "nested": {"rank": 1}},
        )
    )
    bundle = ExperienceEvidenceBundle(
        candidates=[
            CandidateExperienceEvidence(
                memory_file=MemoryFile(
                    uri="viking://experience/one",
                    content="candidate",
                    memory_type="experiences",
                    extra_fields={"experience_name": "one"},
                ),
                source_trajectories=[source],
            )
        ],
        comparison_trajectories=[source],
    )

    assert decode_value(encode_value(bundle)) == bundle


@pytest.mark.asyncio
async def test_loader_returns_candidates_with_bounded_source_trajectories() -> None:
    experience_uri = "viking://user/user/memories/experiences/avoid_duplicate.md"
    source_uris = [
        f"viking://user/user/memories/trajectories/source-{index}.md" for index in range(5)
    ]
    experience = MemoryFile(
        uri=experience_uri,
        content="candidate content",
        memory_type="experiences",
        extra_fields={"experience_name": "avoid_duplicate"},
        links=[
            {"link_type": "derived_from", "to_uri": uri, "from_uri": experience_uri}
            for uri in source_uris
        ],
    )
    files = {
        experience_uri: _raw(experience),
        **{
            uri: _raw(
                MemoryFile(
                    uri=uri,
                    content=f"source {index}",
                    memory_type="trajectories",
                    extra_fields={"outcome": "failure"},
                )
            )
            for index, uri in enumerate(source_uris)
        },
    }
    viking_fs = AsyncMock()
    viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])
    loader = ExperienceEvidenceLoader(viking_fs)
    loader._search_uris = AsyncMock(side_effect=[[experience_uri], []])

    bundle = await loader.load(_query(), _ctx())

    assert [item.memory_file.uri for item in bundle.candidates] == [experience_uri]
    assert [
        item.memory_file.uri for item in bundle.candidates[0].source_trajectories
    ] == source_uris[-3:]
    assert bundle.comparison_trajectories == []


@pytest.mark.asyncio
async def test_loader_uses_directory_fallback_and_ignores_internal_files() -> None:
    experience_uri = "viking://user/user/memories/experiences/fallback.md"
    viking_fs = AsyncMock()
    viking_fs.ls = AsyncMock(
        return_value=[
            {"name": ".overview.md", "uri": "viking://ignored/.overview.md"},
            {"name": "fallback.md", "uri": experience_uri},
            {"name": "folder", "uri": "viking://ignored/folder"},
        ]
    )
    viking_fs.read_file = AsyncMock(
        return_value=_raw(
            MemoryFile(
                uri=experience_uri,
                content="fallback",
                memory_type="experiences",
                extra_fields={"experience_name": "fallback"},
            )
        )
    )
    loader = ExperienceEvidenceLoader(viking_fs)
    loader._search_uris = AsyncMock(side_effect=[[], []])

    bundle = await loader.load(_query(), _ctx())

    assert [item.memory_file.uri for item in bundle.candidates] == [experience_uri]


@pytest.mark.asyncio
async def test_loader_prefers_case_linked_successes_in_reverse_link_recency() -> None:
    case_uri = "viking://user/user/memories/cases/case-1.md"
    success_uris = [
        f"viking://user/user/memories/trajectories/success-{index}.md" for index in range(5)
    ]
    failure_uri = "viking://user/user/memories/trajectories/failure.md"
    legacy_uri = "viking://user/user/memories/trajectories/legacy.md"
    case = MemoryFile(
        uri=case_uri,
        content="case",
        memory_type="cases",
        links=[
            *[
                {
                    "from_uri": case_uri,
                    "to_uri": uri,
                    "link_type": "successful_trajectory",
                    "created_at": f"2026-07-17T0{index + 1}:00:00Z",
                }
                for index, uri in (
                    (1, success_uris[1]),
                    (4, success_uris[4]),
                    (0, success_uris[0]),
                    (3, success_uris[3]),
                    (2, success_uris[2]),
                )
            ],
            {"from_uri": case_uri, "to_uri": failure_uri, "link_type": "failed_trajectory"},
            {"from_uri": case_uri, "to_uri": legacy_uri, "link_type": "related_to"},
        ],
    )
    files = {
        case_uri: _raw(case),
        **{
            uri: _raw(
                MemoryFile(
                    uri=uri,
                    content="x" * 7000,
                    memory_type="trajectories",
                    extra_fields={"outcome": "success"},
                )
            )
            for uri in success_uris
        },
        failure_uri: _raw(
            MemoryFile(
                uri=failure_uri,
                content="failure",
                memory_type="trajectories",
                extra_fields={"outcome": "failure"},
            )
        ),
        legacy_uri: _raw(
            MemoryFile(
                uri=legacy_uri,
                content="legacy success",
                memory_type="trajectories",
                extra_fields={"outcome": "success"},
            )
        ),
    }
    viking_fs = AsyncMock()
    viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])
    loader = ExperienceEvidenceLoader(viking_fs)
    loader._search_uris = AsyncMock(return_value=[])

    bundle = await loader.load(_query(case_uri=case_uri), _ctx())

    assert [item.memory_file.uri for item in bundle.comparison_trajectories] == list(
        reversed(success_uris)
    )
    assert all(
        item.memory_file.content.endswith("\n...<truncated>")
        for item in bundle.comparison_trajectories
    )
    assert [call.args[0] for call in viking_fs.read_file.await_args_list] == [
        case_uri,
        *reversed(success_uris),
    ]
    loader._search_uris.assert_awaited_once()


@pytest.mark.asyncio
async def test_loader_resolves_case_uri_from_current_trajectory_backlink() -> None:
    current_uri = _query().trajectory_uri
    case_uri = "viking://user/user/memories/cases/case-1.md"
    success_uri = "viking://user/user/memories/trajectories/success.md"
    files = {
        current_uri: _raw(
            MemoryFile(
                uri=current_uri,
                content="current",
                memory_type="trajectories",
                backlinks=[{"from_uri": case_uri, "to_uri": current_uri}],
            )
        ),
        case_uri: _raw(
            MemoryFile(
                uri=case_uri,
                content="case",
                memory_type="cases",
                links=[
                    {
                        "from_uri": case_uri,
                        "to_uri": success_uri,
                        "link_type": "successful_trajectory",
                    }
                ],
            )
        ),
        success_uri: _raw(
            MemoryFile(
                uri=success_uri,
                content="success",
                memory_type="trajectories",
                extra_fields={"outcome": "success"},
            )
        ),
    }
    viking_fs = AsyncMock()
    viking_fs.read_file = AsyncMock(side_effect=lambda uri, ctx=None: files[uri])
    loader = ExperienceEvidenceLoader(viking_fs)
    loader._search_uris = AsyncMock(return_value=[])

    bundle = await loader.load(_query(), _ctx())

    assert [item.memory_file.uri for item in bundle.comparison_trajectories] == [success_uri]
