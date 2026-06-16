# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.storage.vector_migration import copy_vector_records, delete_vector_records


class FakeVectorStore:
    def __init__(self, records):
        self.records = [dict(record) for record in records]
        self.upserts = []
        self.deleted_ids = []

    async def filter(self, **_kwargs):
        return [dict(record) for record in self.records]

    async def upsert(self, data, *, ctx):
        self.upserts.append(dict(data))
        return data["id"]

    async def delete(self, ids, *, ctx):
        self.deleted_ids.extend(ids)
        return len(ids)


@pytest.mark.asyncio
async def test_copy_vector_records_rewrites_file_and_chunk_uris():
    store = FakeVectorStore(
        [
            {
                "id": "old-file",
                "uri": "viking://agent/code-agent/memories/facts/project.md",
                "account_id": "acct",
                "owner_user_id": None,
                "context_type": "memory",
                "level": 2,
                "abstract": "project",
                "vector": [0.1, 0.2],
            },
            {
                "id": "old-chunk",
                "uri": "viking://agent/code-agent/memories/facts/project.md#chunk_0000",
                "account_id": "acct",
                "context_type": "memory",
                "level": 2,
                "abstract": "chunk",
                "vector": [0.3, 0.4],
            },
            {
                "id": "outside",
                "uri": "viking://agent/code-agent/memories/facts/other.md",
                "account_id": "acct",
                "context_type": "memory",
                "level": 2,
                "abstract": "other",
                "vector": [0.5, 0.6],
            },
        ]
    )

    result = await copy_vector_records(
        store,
        account_id="acct",
        source_uri="viking://agent/code-agent/memories/facts/project.md",
        target_uri="viking://user/alice/peers/code-agent/memories/facts/project.md",
        recursive=False,
    )

    assert result.copied == 2
    assert result.skipped == 0
    assert {record["uri"] for record in store.upserts} == {
        "viking://user/alice/peers/code-agent/memories/facts/project.md",
        "viking://user/alice/peers/code-agent/memories/facts/project.md#chunk_0000",
    }
    assert {record["owner_user_id"] for record in store.upserts} == {"alice"}
    assert {record["context_type"] for record in store.upserts} == {"memory"}
    assert all(record["active_count"] == 0 for record in store.upserts)
    assert all(record["id"] not in {"old-file", "old-chunk"} for record in store.upserts)


@pytest.mark.asyncio
async def test_copy_vector_records_rewrites_directory_subtree_and_skips_scalar_only_records():
    store = FakeVectorStore(
        [
            {
                "id": "old-dir",
                "uri": "viking://agent/code-agent/skills/review",
                "account_id": "acct",
                "context_type": "skill",
                "level": 0,
                "abstract": "review skill",
                "vector": [0.1, 0.2],
            },
            {
                "id": "old-file",
                "uri": "viking://agent/code-agent/skills/review/SKILL.md",
                "account_id": "acct",
                "context_type": "skill",
                "level": 2,
                "abstract": "skill body",
                "vector": [0.3, 0.4],
            },
            {
                "id": "no-vector",
                "uri": "viking://agent/code-agent/skills/review/README.md",
                "account_id": "acct",
                "context_type": "skill",
                "level": 2,
                "abstract": "no vector",
            },
        ]
    )

    result = await copy_vector_records(
        store,
        account_id="acct",
        source_uri="viking://agent/code-agent/skills/review",
        target_uri="viking://user/alice/skills/review",
        recursive=True,
    )

    assert result.copied == 2
    assert result.skipped == 1
    assert {record["uri"] for record in store.upserts} == {
        "viking://user/alice/skills/review",
        "viking://user/alice/skills/review/SKILL.md",
    }
    assert {record["context_type"] for record in store.upserts} == {"skill"}


@pytest.mark.asyncio
async def test_delete_vector_records_deletes_records_in_scope_only():
    store = FakeVectorStore(
        [
            {
                "id": "old-dir",
                "uri": "viking://agent/code-agent/memories",
                "account_id": "acct",
                "vector": [0.1, 0.2],
            },
            {
                "id": "old-file",
                "uri": "viking://agent/code-agent/memories/facts/project.md",
                "account_id": "acct",
                "vector": [0.3, 0.4],
            },
            {
                "id": "new-file",
                "uri": "viking://user/alice/peers/code-agent/memories/facts/project.md",
                "account_id": "acct",
                "vector": [0.5, 0.6],
            },
        ]
    )

    result = await delete_vector_records(
        store,
        account_id="acct",
        uri="viking://agent/code-agent/memories",
    )

    assert result.deleted == 2
    assert store.deleted_ids == ["old-dir", "old-file"]
