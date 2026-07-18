# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Generated-relation provenance and replacement contract tests."""

from unittest.mock import AsyncMock

import pytest

from openviking.storage.viking_fs import RelationEntry, VikingFS
from openviking_cli.exceptions import InvalidArgumentError

pytestmark = pytest.mark.asyncio


def _vfs(monkeypatch, entries):
    vfs = VikingFS(agfs=object())
    monkeypatch.setattr(vfs, "_ensure_mutable_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(vfs, "_ensure_access", lambda *args, **kwargs: None)
    monkeypatch.setattr(vfs, "_uri_to_path", lambda *args, **kwargs: "/source")
    monkeypatch.setattr(vfs, "_read_relation_table", AsyncMock(return_value=list(entries)))
    write = AsyncMock()
    monkeypatch.setattr(vfs, "_write_relation_table", write)
    return vfs, write


async def test_manual_relation_shape_remains_backward_compatible():
    entry = RelationEntry(id="link_1", uris=["viking://resources/b"], reason="manual")

    assert entry.to_dict() == {
        "id": "link_1",
        "uris": ["viking://resources/b"],
        "reason": "manual",
        "created_at": entry.created_at,
    }
    assert RelationEntry.from_dict(entry.to_dict()) == entry


async def test_relations_exposes_generated_provenance_only(monkeypatch):
    manual = RelationEntry(id="link_1", uris=["viking://resources/manual"], reason="manual")
    generated = RelationEntry(
        id="generated_one",
        uris=["viking://resources/generated"],
        reason="shared concepts",
        source="generated:concepts-v1",
        metadata={"confidence": 0.9},
    )
    vfs, _ = _vfs(monkeypatch, [])
    monkeypatch.setattr(vfs, "get_relation_table", AsyncMock(return_value=[manual, generated]))
    monkeypatch.setattr(vfs, "_is_accessible", lambda *args, **kwargs: True)

    assert await vfs.relations("viking://resources/source") == [
        {"uri": "viking://resources/manual", "reason": "manual"},
        {
            "uri": "viking://resources/generated",
            "reason": "shared concepts",
            "source": "generated:concepts-v1",
            "metadata": {"confidence": 0.9},
        },
    ]


async def test_replace_generated_relations_preserves_other_owners(monkeypatch):
    manual = RelationEntry(id="link_1", uris=["viking://resources/manual"], reason="manual")
    other = RelationEntry(
        id="generated_other",
        uris=["viking://resources/other"],
        source="generated:other",
    )
    previous = RelationEntry(
        id="generated_previous",
        uris=["viking://resources/old"],
        reason="old reason",
        created_at="2026-01-01T00:00:00Z",
        source="generated:concepts-v1",
        metadata={"confidence": 0.5},
    )
    vfs, write = _vfs(monkeypatch, [manual, other, previous])

    result = await vfs.replace_generated_relations(
        "viking://resources/source",
        "concepts-v1",
        [
            {
                "uri": "viking://resources/old",
                "reason": "new reason",
                "metadata": {"confidence": 0.9, "concepts": ["FastAPI", "JWT"]},
            },
            {"uri": "viking://resources/new", "reason": "shared concepts"},
        ],
    )

    assert result == {
        "source": "generated:concepts-v1",
        "created": 1,
        "updated": 1,
        "removed": 0,
        "total": 2,
    }
    written = write.await_args.args[1]
    assert written[:2] == [manual, other]
    generated = {entry.uris[0]: entry for entry in written[2:]}
    assert generated["viking://resources/old"].created_at == previous.created_at
    assert generated["viking://resources/old"].metadata["confidence"] == 0.9
    assert generated["viking://resources/new"].source == "generated:concepts-v1"


async def test_replace_generated_relations_empty_removes_only_producer(monkeypatch):
    manual = RelationEntry(id="link_1", uris=["viking://resources/manual"])
    generated = RelationEntry(
        id="generated_old",
        uris=["viking://resources/old"],
        source="generated:concepts-v1",
    )
    vfs, write = _vfs(monkeypatch, [manual, generated])

    result = await vfs.replace_generated_relations("viking://resources/source", "concepts-v1", [])

    assert result["removed"] == 1
    assert result["total"] == 0
    assert write.await_args.args[1] == [manual]


async def test_replace_generated_relations_deduplicates_and_skips_self(monkeypatch):
    vfs, write = _vfs(monkeypatch, [])

    await vfs.replace_generated_relations(
        "viking://resources/source",
        "concepts-v1",
        [
            {"uri": "viking://resources/source", "reason": "self"},
            {"uri": "viking://resources/target", "reason": "first"},
            {"uri": "viking://resources/target", "reason": "latest"},
        ],
    )

    generated = write.await_args.args[1]
    assert len(generated) == 1
    assert generated[0].uris == ["viking://resources/target"]
    assert generated[0].reason == "latest"


@pytest.mark.parametrize("producer", ["", "bad producer", "generated:bad", "x" * 65])
async def test_replace_generated_relations_rejects_invalid_producer(monkeypatch, producer):
    vfs, _ = _vfs(monkeypatch, [])

    with pytest.raises(InvalidArgumentError):
        await vfs.replace_generated_relations("viking://resources/source", producer, [])


async def test_replace_generated_relations_rejects_non_json_metadata(monkeypatch):
    vfs, _ = _vfs(monkeypatch, [])

    with pytest.raises(InvalidArgumentError, match="JSON serializable"):
        await vfs.replace_generated_relations(
            "viking://resources/source",
            "concepts-v1",
            [{"uri": "viking://resources/target", "metadata": {"bad": object()}}],
        )
