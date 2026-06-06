# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.merge_op.base import FieldType, MergeOp
from openviking.session.memory.streaming_memory_updater import (
    MemoryUpdateRequest,
    StreamingMemoryUpdater,
    StreamingMemoryUpdaterConfig,
)
from openviking_cli.session.user_id import UserIdentifier


class InMemoryVikingFS:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.writes = []

    async def ls(self, uri: str, output: str = "original", ctx=None):
        del output, ctx
        prefix = uri.rstrip("/") + "/"
        return [
            {"name": path.removeprefix(prefix), "uri": path, "isDir": False}
            for path in sorted(self.files)
            if path.startswith(prefix) and "/" not in path.removeprefix(prefix)
        ]

    async def read_file(self, uri: str, ctx=None):
        uri = _canonical_user_uri(uri, ctx)
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return self.files[uri]

    async def write_file(self, uri: str, content: str, ctx=None):
        uri = _canonical_user_uri(uri, ctx)
        self.files[uri] = content
        self.writes.append((uri, content, ctx))


def _canonical_user_uri(uri: str, ctx=None) -> str:
    if not uri.startswith("viking://user/memories/"):
        return uri
    user_id = getattr(getattr(ctx, "user", None), "user_id", None) or "u"
    return uri.replace("viking://user/memories/", f"viking://user/{user_id}/memories/", 1)


def _ctx() -> RequestContext:
    return RequestContext(user=UserIdentifier.the_default_user("u"), role=Role.ROOT)


def _registry() -> MemoryTypeRegistry:
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(
        MemoryTypeSchema(
            memory_type="cases",
            description="case memory",
            directory="viking://user/{{ user_space }}/memories/cases",
            filename_template="{{ case_name }}.md",
            operation_mode="add_only",
            fields=[
                MemoryField(
                    name="case_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="task_signature",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="input",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="rubric",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
            ],
        )
    )
    return registry


def _case_op(name: str) -> ResolvedOperation:
    return ResolvedOperation(
        old_memory_file_content=None,
        memory_type="cases",
        uris=[f"viking://user/u/memories/cases/{name}.md"],
        memory_fields={
            "case_name": name,
            "task_signature": f"{name} signature",
            "input": '{"summary":"case input"}',
            "rubric": '{"criteria":[{"name":"done","description":"done","required":true,"weight":1.0}]}',
        },
    )


@pytest.mark.asyncio
async def test_streaming_memory_updater_submit_applies_fast_path(monkeypatch):
    fs = InMemoryVikingFS({})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )
    monkeypatch.setattr(
        "openviking.session.memory.memory_updater.get_viking_fs",
        lambda: fs,
    )

    updater = StreamingMemoryUpdater(
        registry=_registry(),
        config=StreamingMemoryUpdaterConfig(max_operations_per_update=8, max_wait_seconds=60),
    )
    result = await updater.submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[_case_op("重复预订处理")],
                delete_file_contents=[],
                errors=[],
            ),
            messages=[Message(id="m1", role="user", parts=[TextPart("处理重复预订")])],
            ctx=_ctx(),
        )
    )

    assert result.request_count == 1
    assert result.operations.upsert_operations[0].memory_type == "cases"
    assert result.apply_result.written_uris == ["viking://user/u/memories/cases/重复预订处理.md"]
    assert fs.writes
    written_uri, written_content, _ = fs.writes[0]
    assert written_uri.endswith("/memories/cases/重复预订处理.md")
    assert "重复预订处理" in written_content
