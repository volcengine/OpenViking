# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Regression tests for #4290: unbounded `tags` on set_tags / reindex.

`tags: list[str]` carried no length constraint, so one authenticated request
could submit 10k entries or a single 10KB value. Every tag becomes its own
`must` clause in `build_search_tags_filter()` and the whole list is stored on
the record, so the cost lands on every later search as well as on the write.
"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from openviking.server.identity import RequestContext, Role
from openviking.server.routers.content import (
    MAX_SEARCH_TAG_LENGTH,
    MAX_SEARCH_TAGS,
    ReindexRequest,
    SetTagsRequest,
)
from openviking.service.fs_service import FSService
from openviking.service.session_service import SessionService
from openviking.storage.viking_fs import VikingFS
from openviking_cli.session.user_id import UserIdentifier
from tests.utils.mock_agfs import MockLocalAGFS


@pytest.fixture
def service(temp_dir):
    mock_agfs = MockLocalAGFS(root_path=temp_dir / "mock_agfs_root")
    viking_fs = VikingFS(agfs=mock_agfs)
    return SimpleNamespace(
        fs=FSService(viking_fs=viking_fs),
        sessions=SessionService(viking_fs=viking_fs),
        viking_fs=viking_fs,
    )


def _tags(count, value="v"):
    return [f"k{index}={value}" for index in range(count)]


# ── Rejected ──


@pytest.mark.parametrize("model", [SetTagsRequest, ReindexRequest])
@pytest.mark.parametrize(
    "tags,reason",
    [
        (_tags(MAX_SEARCH_TAGS + 1), "one over the count cap"),
        (_tags(10_000), "the reported 10k list"),
        (["k=" + "v" * MAX_SEARCH_TAG_LENGTH], "one over the length cap"),
        (["k=" + "v" * 10_240], "the reported 10KB value"),
    ],
)
def test_oversized_tags_are_rejected(model, tags, reason):
    with pytest.raises(ValidationError):
        model(uri="viking://resources/x", tags=tags)


# ── Accepted ──


@pytest.mark.parametrize("model", [SetTagsRequest, ReindexRequest])
@pytest.mark.parametrize(
    "tags",
    [
        ["topic=rag", "lang=python", "stage=draft", "owner=team"],
        _tags(MAX_SEARCH_TAGS),
        ["k=" + "v" * (MAX_SEARCH_TAG_LENGTH - 2)],
        [],
    ],
)
def test_realistic_tags_are_accepted(model, tags):
    assert model(uri="viking://resources/x", tags=tags).tags == tags


def test_reindex_tags_stay_optional():
    assert ReindexRequest(uri="viking://resources/x").tags is None


def test_caps_leave_room_for_experience_lineage_tags():
    """Lineage builds one tag per read Experience, keyed on the escaped URI.

    Those tags never pass through these request models, but the length cap has
    to stay above them anyway — a number below this would be a trap for anyone
    who later moves the check into the shared normalizer.
    """
    from openviking.session.memory.experience_lineage import experience_source_tag

    uri = (
        "viking://user/A-Very-Long-User-Id-With-Caps/memories/experiences/"
        "2026-08-25-A-Rather-Long-Experience-Folder-Name-With-Caps/experience.md"
    )
    assert len(experience_source_tag(uri)) < MAX_SEARCH_TAG_LENGTH


# ── Over HTTP: rejected before any write happens ──


async def test_http_set_tags_rejects_an_oversized_list(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    uri = "viking://resources/set-tags-bounds"
    await service.viking_fs.mkdir(uri, exist_ok=True, ctx=ctx)

    response = await client.post(
        "/api/v1/content/set_tags", json={"uri": uri, "tags": _tags(10_000)}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_http_fs_attrs_set_tags_shares_the_same_bound(client, service):
    """`/fs/attrs/set_tags` reuses SetTagsRequest, so it must be bounded too."""
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    uri = "viking://resources/set-tags-bounds-fs"
    await service.viking_fs.mkdir(uri, exist_ok=True, ctx=ctx)

    response = await client.post(
        "/api/v1/fs/attrs/set_tags", json={"uri": uri, "tags": ["k=" + "v" * 10_240]}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"


async def test_http_set_tags_still_accepts_a_normal_list(client, service):
    ctx = RequestContext(user=UserIdentifier.the_default_user(), role=Role.ROOT)
    uri = "viking://resources/set-tags-bounds-ok"
    await service.viking_fs.mkdir(uri, exist_ok=True, ctx=ctx)

    response = await client.post(
        "/api/v1/content/set_tags",
        json={"uri": uri, "tags": ["topic=rag", "lang=python"]},
    )
    assert response.status_code != 400, response.text
