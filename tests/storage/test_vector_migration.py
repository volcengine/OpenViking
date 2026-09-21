# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.server.identity import RequestContext, Role
from openviking.storage import vector_migration
from openviking.storage.abstract_overview import parse_abstract_overview
from openviking.storage.vector_migration import delete_vector_records
from openviking_cli.session.user_id import UserIdentifier


def _ctx(account_id: str = "acct", user_id: str = "alice") -> RequestContext:
    return RequestContext(user=UserIdentifier(account_id, user_id), role=Role.USER)


def test_transfer_scope_includes_chunks_but_not_sibling_prefixes():
    source = "viking://resources/src.md"

    assert vector_migration.uri_in_transfer_scope(source, source, recursive=False)
    assert vector_migration.uri_in_transfer_scope(f"{source}#chunk_0001", source, recursive=False)
    assert not vector_migration.uri_in_transfer_scope(
        "viking://resources/src.md.backup", source, recursive=True
    )
    assert not vector_migration.uri_in_transfer_scope(
        "viking://resources/src.md/child", source, recursive=False
    )
    assert vector_migration.uri_in_transfer_scope(
        "viking://resources/src.md/child", source, recursive=True
    )


def test_rewrite_vector_record_for_copy_preserves_payload_and_resets_metadata():
    record = {
        "id": "old-id",
        "uri": "viking://user/alice/memories/src.md#chunk_1",
        "level": 2,
        "vector": [0.1, 0.2],
        "sparse_vector": {"7": 0.8},
        "content": "chunk",
        "tags": ["team=a"],
        "created_at": 10,
        "updated_at": 11,
        "active_count": 9,
        "account_id": "acct",
        "owner_user_id": "alice",
    }

    result = vector_migration.rewrite_vector_record(
        record,
        source_uri="viking://user/alice/memories/src.md",
        target_uri="viking://user/alice/memories/dst.md",
        ctx=_ctx(),
        mode="copy",
        timestamp=123,
    )

    assert result["uri"] == "viking://user/alice/memories/dst.md#chunk_1"
    assert result["id"] != "old-id"
    assert result["vector"] == [0.1, 0.2]
    assert result["sparse_vector"] == {"7": 0.8}
    assert result["content"] == "chunk"
    assert result["tags"] == ["team=a"]
    assert result["created_at"] == 123
    assert result["updated_at"] == 123
    assert result["active_count"] == 0
    assert result["owner_user_id"] == "alice"


@pytest.mark.parametrize("level", [0, 1, 2])
def test_rewrite_vector_record_for_move_preserves_metadata(level):
    record = {
        "id": "old-id",
        "uri": "viking://resources/src",
        "level": level,
        "vector": [0.1],
        "created_at": 10,
        "updated_at": 11,
        "active_count": 9,
    }

    result = vector_migration.rewrite_vector_record(
        record,
        source_uri="viking://resources/src",
        target_uri="viking://resources/dst",
        ctx=_ctx(),
        mode="move",
        timestamp=123,
    )

    assert result["uri"] == "viking://resources/dst"
    assert result["id"] != "old-id"
    assert result["created_at"] == 10
    assert result["updated_at"] == 11
    assert result["active_count"] == 9


@pytest.mark.parametrize("mode", ["copy", "move"])
def test_rewrite_vector_record_updates_generated_l1_uri_references(mode):
    record = {
        "id": "old-id",
        "uri": "viking://resources/source",
        "level": 1,
        "vector": [0.1],
        "abstract": "[chapter](viking://resources/source/chapter.md)",
        "content": """---
directory: viking://resources/source/
---

[chapter](viking://resources/source/chapter.md)
""",
        "created_at": 10,
        "updated_at": 11,
        "active_count": 9,
    }

    result = vector_migration.rewrite_vector_record(
        record,
        source_uri="viking://resources/source",
        target_uri="viking://resources/target",
        ctx=_ctx(),
        mode=mode,
        timestamp=123,
    )

    assert result["abstract"] == "[chapter](viking://resources/target/chapter.md)"
    content_doc = parse_abstract_overview(result["content"])
    assert content_doc.metadata["directory"] == "viking://resources/target/"
    assert content_doc.body.strip() == "[chapter](viking://resources/target/chapter.md)"


class FakeVectorStore:
    def __init__(self, records):
        self.records = [dict(record) for record in records]
        self.deleted_ids = []

    async def filter(self, **_kwargs):
        return [dict(record) for record in self.records]

    async def delete(self, ids, *, ctx):
        self.deleted_ids.extend(ids)
        return len(ids)


@pytest.mark.asyncio
async def test_delete_vector_records_deletes_records_in_scope_only():
    store = FakeVectorStore(
        [
            {
                "id": "old-dir",
                "uri": "viking://session",
                "account_id": "acct",
                "vector": [0.1, 0.2],
            },
            {
                "id": "old-file",
                "uri": "viking://session/s1/messages.jsonl",
                "account_id": "acct",
                "vector": [0.3, 0.4],
            },
            {
                "id": "new-file",
                "uri": "viking://user/alice/sessions/s1/messages.jsonl",
                "account_id": "acct",
                "vector": [0.5, 0.6],
            },
        ]
    )

    result = await delete_vector_records(
        store,
        account_id="acct",
        uri="viking://session",
    )

    assert result.deleted == 2
    assert store.deleted_ids == ["old-dir", "old-file"]
