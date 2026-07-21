# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryOperationSource,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdateResult
from openviking.session.memory.merge_op.base import FieldType, MergeOp, SearchReplaceBlock, StrPatch
from openviking.session.memory.streaming_memory_updater import (
    MemoryMergeGroupKey,
    MemoryUpdateRequest,
    StreamingMemoryUpdater,
    StreamingMemoryUpdaterConfig,
    StreamingMemoryUpdateResult,
    classify_memory_merge_mode,
    enforce_merge_group_peer_id,
    get_streaming_memory_updater,
    merge_one_memory_type_operations,
    operation_to_patch,
    render_operation_after_file_content,
    split_request_by_merge_group,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
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

    async def rm(self, uri: str, recursive: bool = False, ctx=None, lock_handle=None):
        del recursive, lock_handle
        uri = _canonical_user_uri(uri, ctx)
        self.files.pop(uri, None)


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
            peer_enabled=False,
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
    registry.register(
        MemoryTypeSchema(
            memory_type="notes",
            description="note memory",
            directory="viking://user/{{ user_space }}/memories/notes",
            filename_template="{{ note_name }}.md",
            operation_mode="upsert",
            fields=[
                MemoryField(
                    name="note_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.PATCH,
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


def _note_op(name: str) -> ResolvedOperation:
    return ResolvedOperation(
        old_memory_file_content=None,
        memory_type="notes",
        uris=[f"viking://user/u/memories/notes/{name}.md"],
        memory_fields={
            "note_name": name,
            "content": f"{name} content",
        },
    )


def _note_op_with_source(name: str, extraction_id: str) -> ResolvedOperation:
    op = _note_op(name)
    op.memory_fields["source_extraction_id"] = extraction_id
    return op


def _peer_note_op(name: str, peer_id: str) -> ResolvedOperation:
    op = _note_op(name)
    op.memory_fields["peer_id"] = peer_id
    op.uris = [f"viking://user/u/peers/{peer_id}/memories/notes/{name}.md"]
    return op


def test_operation_to_patch_omits_raw_operation_metadata():
    schema = _registry().get("notes")
    old_file = MemoryFile(
        uri="viking://user/u/memories/notes/note.md",
        content="old content",
        memory_type="notes",
        extra_fields={"note_name": "note"},
    )
    op = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="notes",
        uris=["viking://user/u/memories/notes/note.md"],
        memory_fields={
            "note_name": "note",
            "content": StrPatch(
                blocks=[SearchReplaceBlock(search="old content", replace="new content")]
            ),
        },
    )

    patch = operation_to_patch(op, schema=schema, extract_context=ExtractContext([]))

    assert patch.metadata == {}
    assert patch.after_file.content == "new content"


def test_operation_to_patch_raises_when_after_file_preview_rendering_fails(monkeypatch):
    schema = _registry().get("notes")
    op = _note_op("note_render_failure")

    def fail_write(*args, **kwargs):
        raise RuntimeError("template render failed")

    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.MemoryFileUtils.write",
        fail_write,
    )

    with pytest.raises(RuntimeError, match="template render failed"):
        operation_to_patch(op, schema=schema, extract_context=ExtractContext([]))


def test_operation_to_patch_skips_failed_field_preview_update():
    schema = MemoryTypeSchema(
        memory_type="notes",
        description="note memory",
        directory="viking://user/{{ user_space }}/memories/notes",
        filename_template="{{ note_name }}.md",
        operation_mode="upsert",
        fields=[
            MemoryField(
                name="note_name",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
            MemoryField(
                name="summary",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
        ],
    )
    old_file = MemoryFile(
        uri="viking://user/u/memories/notes/note.md",
        content="old content",
        memory_type="notes",
        extra_fields={
            "note_name": "note",
            "summary": "old summary",
        },
    )
    op = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="notes",
        uris=["viking://user/u/memories/notes/note.md"],
        memory_fields={
            "note_name": "note",
            "content": StrPatch(
                blocks=[SearchReplaceBlock(search="old content", replace="new content")]
            ),
            "summary": StrPatch(
                blocks=[SearchReplaceBlock(search="missing summary", replace="new summary")]
            ),
        },
    )

    patch = operation_to_patch(op, schema=schema, extract_context=ExtractContext([]))

    assert patch.after_file.content == "new content"
    assert patch.after_file.extra_fields["summary"] == "old summary"
    assert isinstance(op.memory_fields["summary"], StrPatch)


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
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
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


@pytest.mark.asyncio
async def test_cached_updater_restores_vectorization_for_tool_and_skill_memories(monkeypatch):
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

    registry = _registry()
    for memory_type, name_field in (("tools", "tool_name"), ("skills", "skill_name")):
        registry.register(
            MemoryTypeSchema(
                memory_type=memory_type,
                description=f"{memory_type} memory",
                directory=f"viking://user/{{{{ user_space }}}}/memories/{memory_type}",
                filename_template=f"{{{{ {name_field} }}}}.md",
                operation_mode="add_only",
                content_template=f"{memory_type}: {{{{ {name_field} }}}}",
                fields=[
                    MemoryField(
                        name=name_field,
                        field_type=FieldType.STRING,
                        merge_op=MergeOp.IMMUTABLE,
                    )
                ],
            )
        )

    key = ("cached-updater-vectorization", id(fs))
    degraded = await get_streaming_memory_updater(
        key=key,
        registry=registry,
        vikingdb=None,
    )
    vikingdb = AsyncMock()
    vikingdb.enqueue_embedding_msg.return_value = True
    restored = await get_streaming_memory_updater(
        key=key,
        registry=registry,
        vikingdb=vikingdb,
    )

    assert restored is degraded
    assert restored.vikingdb is vikingdb

    operations = []
    for memory_type, name_field, name in (
        ("tools", "tool_name", "terminal"),
        ("skills", "skill_name", "analyze_code"),
    ):
        operations.append(
            ResolvedOperation(
                old_memory_file_content=None,
                memory_type=memory_type,
                uris=[f"viking://user/u/memories/{memory_type}/{name}.md"],
                memory_fields={name_field: name},
            )
        )

    result = await restored.submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=operations,
                delete_file_contents=[],
                errors=[],
            ),
            messages=[Message(id="m1", role="user", parts=[TextPart("use tools and skills")])],
            ctx=_ctx(),
        )
    )

    assert sorted(result.apply_result.written_uris) == sorted(
        operation.uris[0] for operation in operations
    )
    assert vikingdb.enqueue_embedding_msg.await_count == 2


@pytest.mark.asyncio
async def test_streaming_memory_updater_fast_path_filters_links(monkeypatch):
    fs = InMemoryVikingFS(
        {
            "viking://user/u/memories/events/existing.md": (
                'existing\n<!-- MEMORY_FIELDS\n{"memory_type":"events","content":"existing"}\n-->'
            )
        }
    )
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
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    op1 = _case_op("并发案例A")
    link = StoredLink(
        from_uri=op1.uris[0],
        to_uri="viking://user/u/memories/events/existing.md",
        link_type="related_to",
        weight=0.8,
        match_text="并发",
        description="valid link",
    )
    duplicate_link = link.model_copy(update={"weight": 0.6, "description": "short"})
    missing_link = StoredLink(
        from_uri=op1.uris[0],
        to_uri="viking://user/u/memories/events/missing.md",
        link_type="related_to",
        weight=0.9,
        match_text="缺失",
        description="invalid link",
    )

    result = await updater.submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[op1],
                delete_file_contents=[],
                errors=[],
                resolved_links=[link, duplicate_link, missing_link],
            ),
            messages=[Message(id="m1", role="user", parts=[TextPart("并发A")])],
            ctx=_ctx(),
        )
    )

    assert result.request_count == 1
    assert result.metadata["flush_reason"] == "append_only_fast_path"
    assert len(result.operations.upsert_operations) == 1
    assert len(result.operations.resolved_links) == 1
    assert result.operations.resolved_links[0].to_uri.endswith("/events/existing.md")
    assert result.apply_result.written_uris == [op1.uris[0]]


@pytest.mark.asyncio
async def test_streaming_memory_updater_batches_non_append_only_submits(monkeypatch):
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
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=2,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    op1 = _note_op("note_a")
    op2 = _note_op("note_b")

    result1, result2 = await asyncio.gather(
        updater.submit(
            MemoryUpdateRequest(
                operations=ResolvedOperations(
                    upsert_operations=[op1],
                    delete_file_contents=[],
                    errors=[],
                ),
                messages=[Message(id="m1", role="user", parts=[TextPart("note A")])],
                ctx=_ctx(),
            )
        ),
        updater.submit(
            MemoryUpdateRequest(
                operations=ResolvedOperations(
                    upsert_operations=[op2],
                    delete_file_contents=[],
                    errors=[],
                ),
                messages=[Message(id="m2", role="user", parts=[TextPart("note B")])],
                ctx=_ctx(),
            )
        ),
    )

    assert result1 is not result2
    assert result1.request_count == 1
    assert result2.request_count == 1
    assert result1.metadata["flush_reason"] == "count"
    assert result1.metadata["batch_request_count"] == 2
    assert result1.metadata["scoped_to_submitter"] is True
    assert result1.apply_result.written_uris == [op1.uris[0]]
    assert result2.apply_result.written_uris == [op2.uris[0]]
    assert sorted(result1.metadata["unscoped_written_uris"]) == sorted([op1.uris[0], op2.uris[0]])


def test_scope_memory_update_result_to_submitter_filters_shared_batch_by_source():
    from openviking.session.memory.streaming_memory_updater import (
        scope_memory_update_result_to_submitter,
    )

    op_a = _note_op_with_source("scoped_a", "extract_a")
    op_b = _note_op_with_source("scoped_b", "extract_b")
    apply_result = MemoryUpdateResult()
    apply_result.add_written(op_a.uris[0])
    apply_result.add_written(op_b.uris[0])
    batch_result = StreamingMemoryUpdateResult(
        operations=ResolvedOperations(
            upsert_operations=[op_a, op_b],
            delete_file_contents=[],
            errors=[],
        ),
        apply_result=apply_result,
        request_count=2,
        metadata={"flush_reason": "count", "operation_count": 2},
    )
    request = MemoryUpdateRequest(
        operations=ResolvedOperations(
            upsert_operations=[op_a],
            delete_file_contents=[],
            errors=[],
        ),
        messages=[Message(id="m1", role="user", parts=[TextPart("note A")])],
        ctx=_ctx(),
        metadata={"source_extraction_id": "extract_a", "session_id": "session_a"},
    )

    scoped = scope_memory_update_result_to_submitter(batch_result, request)

    assert scoped.request_count == 1
    assert scoped.metadata["batch_request_count"] == 2
    assert scoped.metadata["scoped_to_source_extraction_id"] == "extract_a"
    assert scoped.apply_result.written_uris == [op_a.uris[0]]
    assert scoped.operations.upsert_operations == [op_a]
    assert scoped.metadata["unscoped_written_uris"] == [op_a.uris[0], op_b.uris[0]]


def test_split_request_by_merge_group_groups_by_peer_and_memory_type():
    self_op = _note_op("self_note")
    peer_op = _peer_note_op("peer_note", "web-visitor-alice")
    case_op = _case_op("case_note")
    link = StoredLink(
        from_uri=self_op.uris[0],
        to_uri=peer_op.uris[0],
        link_type="related_to",
        weight=0.8,
    )
    request = MemoryUpdateRequest(
        operations=ResolvedOperations(
            upsert_operations=[self_op, peer_op, case_op],
            delete_file_contents=[],
            errors=[],
            resolved_links=[link],
        ),
        messages=[],
        ctx=_ctx(),
    )

    grouped = split_request_by_merge_group(request)

    assert [key for key, _ in grouped] == [
        MemoryMergeGroupKey(peer_id=None, memory_type="notes"),
        MemoryMergeGroupKey(peer_id="web-visitor-alice", memory_type="notes"),
        MemoryMergeGroupKey(peer_id=None, memory_type="cases"),
    ]
    assert [len(group_request.operations.upsert_operations) for _, group_request in grouped] == [
        1,
        1,
        1,
    ]
    assert [len(group_request.operations.resolved_links) for _, group_request in grouped] == [
        0,
        0,
        0,
    ]


def test_split_request_by_merge_group_infers_peer_from_uri_when_field_missing():
    peer_uri = "viking://user/u/peers/conv-42/memories/notes/peer_note.md"
    op = ResolvedOperation(
        old_memory_file_content=None,
        memory_fields={"note_name": "peer_note", "content": "peer content"},
        memory_type="notes",
        uris=[peer_uri],
    )
    request = MemoryUpdateRequest(
        operations=ResolvedOperations(
            upsert_operations=[op],
            delete_file_contents=[],
            errors=[],
        ),
        messages=[],
        ctx=_ctx(),
    )

    grouped = split_request_by_merge_group(request)

    assert [key for key, _ in grouped] == [
        MemoryMergeGroupKey(peer_id="conv-42", memory_type="notes")
    ]


def test_enforce_merge_group_peer_id_rewrites_merged_output_scope():
    op = ResolvedOperation(
        old_memory_file_content=None,
        memory_fields={"note_name": "peer_note", "content": "peer content"},
        memory_type="notes",
        uris=["viking://user/u/memories/notes/peer_note.md"],
    )

    enforce_merge_group_peer_id(
        [op],
        peer_id="conv-42",
        memory_type="notes",
        registry=_registry(),
        ctx=_ctx(),
    )

    assert op.memory_fields["peer_id"] == "conv-42"
    assert op.uris == ["viking://user/u/peers/conv-42/memories/notes/peer_note.md"]


def test_enforce_merge_group_self_scope_removes_peer_id():
    op = ResolvedOperation(
        old_memory_file_content=None,
        memory_fields={
            "note_name": "self_note",
            "content": "self content",
            "peer_id": "conv-42",
        },
        memory_type="notes",
        uris=["viking://user/u/peers/conv-42/memories/notes/self_note.md"],
    )

    enforce_merge_group_peer_id(
        [op],
        peer_id=None,
        memory_type="notes",
        registry=_registry(),
        ctx=_ctx(),
    )

    assert "peer_id" not in op.memory_fields
    assert op.uris == ["viking://user/u/memories/notes/self_note.md"]


def test_enforce_merge_group_peer_enabled_false_keeps_self_scope():
    op = ResolvedOperation(
        old_memory_file_content=None,
        memory_fields={
            "case_name": "case_note",
            "task_signature": "case signature",
            "input": "{}",
            "rubric": "{}",
            "peer_id": "conv-42",
        },
        memory_type="cases",
        uris=["viking://user/u/peers/conv-42/memories/cases/case_note.md"],
    )

    enforce_merge_group_peer_id(
        [op],
        peer_id="conv-42",
        memory_type="cases",
        registry=_registry(),
        ctx=_ctx(),
    )

    assert "peer_id" not in op.memory_fields
    assert op.uris == ["viking://user/u/memories/cases/case_note.md"]


@pytest.mark.asyncio
async def test_streaming_memory_updater_batches_per_merge_group(monkeypatch):
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
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=2,
            max_wait_seconds=0.05,
            timer_check_interval_seconds=0.01,
        ),
    )
    note_a = _note_op("note_group_a")
    note_b = _note_op("note_group_b")
    peer_note = _peer_note_op("note_peer", "web-visitor-alice")

    result1, result2, peer_result = await asyncio.gather(
        updater.submit(
            MemoryUpdateRequest(
                operations=ResolvedOperations(
                    upsert_operations=[note_a],
                    delete_file_contents=[],
                    errors=[],
                ),
                messages=[Message(id="m1", role="user", parts=[TextPart("note A")])],
                ctx=_ctx(),
            )
        ),
        updater.submit(
            MemoryUpdateRequest(
                operations=ResolvedOperations(
                    upsert_operations=[note_b],
                    delete_file_contents=[],
                    errors=[],
                ),
                messages=[Message(id="m2", role="user", parts=[TextPart("note B")])],
                ctx=_ctx(),
            )
        ),
        updater.submit(
            MemoryUpdateRequest(
                operations=ResolvedOperations(
                    upsert_operations=[peer_note],
                    delete_file_contents=[],
                    errors=[],
                ),
                messages=[Message(id="m3", role="user", parts=[TextPart("peer note")])],
                ctx=_ctx(),
            )
        ),
    )

    assert result1 is not result2
    assert result1.request_count == 1
    assert result2.request_count == 1
    assert result1.metadata["flush_reason"] == "count"
    assert result1.metadata["batch_request_count"] == 2
    assert result1.metadata["merge_group"] == "peer=self,memory_type=notes"
    assert result1.apply_result.written_uris == [note_a.uris[0]]
    assert result2.apply_result.written_uris == [note_b.uris[0]]

    assert peer_result is not result1
    assert peer_result.request_count == 1
    assert peer_result.metadata["flush_reason"] == "time"
    assert peer_result.metadata["merge_group"] == "peer=web-visitor-alice,memory_type=notes"
    assert peer_result.apply_result.written_uris == [peer_note.uris[0]]


@pytest.mark.asyncio
async def test_streaming_memory_updater_submit_waits_for_all_merge_groups(monkeypatch):
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
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    self_op = _note_op("multi_self")
    peer_op = _peer_note_op("multi_peer", "web-visitor-alice")

    result = await updater.submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[self_op, peer_op],
                delete_file_contents=[],
                errors=[],
            ),
            messages=[Message(id="m1", role="user", parts=[TextPart("multi group")])],
            ctx=_ctx(),
        )
    )

    assert result.metadata["combined_result"] is True
    assert result.request_count == 1
    assert result.metadata["batch_request_count"] == 2
    assert sorted(result.apply_result.written_uris) == sorted([self_op.uris[0], peer_op.uris[0]])
    assert self_op.uris[0] in fs.files
    assert peer_op.uris[0] in fs.files


@pytest.mark.asyncio
async def test_streaming_memory_updater_applies_cross_group_links_after_all_groups(monkeypatch):
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
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    self_op = _note_op("linked_self")
    peer_op = _peer_note_op("linked_peer", "web-visitor-alice")
    link = StoredLink(
        from_uri=self_op.uris[0],
        to_uri=peer_op.uris[0],
        link_type="related_to",
        weight=0.8,
        match_text="linked",
    )

    result = await updater.submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[self_op, peer_op],
                delete_file_contents=[],
                errors=[],
                resolved_links=[link],
            ),
            messages=[Message(id="m1", role="user", parts=[TextPart("cross group link")])],
            ctx=_ctx(),
        )
    )

    self_file = MemoryFileUtils.read(fs.files[self_op.uris[0]], uri=self_op.uris[0])
    peer_file = MemoryFileUtils.read(fs.files[peer_op.uris[0]], uri=peer_op.uris[0])

    assert len(result.operations.resolved_links) == 1
    assert self_file.links[0]["to_uri"] == peer_op.uris[0]
    assert peer_file.backlinks[0]["from_uri"] == self_op.uris[0]


def test_classify_memory_merge_mode_forces_cross_extraction_merge():
    op1 = _note_op_with_source("note_a", "extract_a")
    op2 = _note_op_with_source("note_b", "extract_b")

    fast_path, reason = classify_memory_merge_mode([op1, op2], schema=_registry().get("notes"))

    assert fast_path is False
    assert reason == "cross_extraction_batch"


def test_classify_memory_merge_mode_treats_noop_str_patch_as_unchanged():
    old_file = MemoryFile(
        uri="viking://user/u/memories/notes/note.md",
        content="old content",
        memory_type="notes",
        extra_fields={"note_name": "note"},
    )
    op = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="notes",
        uris=["viking://user/u/memories/notes/note.md"],
        memory_fields={
            "note_name": "note",
            "content": StrPatch(
                blocks=[SearchReplaceBlock(search="old content", replace="old content")]
            ),
        },
    )

    fast_path, reason = classify_memory_merge_mode([op], schema=_registry().get("notes"))

    assert fast_path is True
    assert reason == "single_existing_content_unchanged"


def test_classify_memory_merge_mode_detects_changed_str_patch_after_preview():
    old_file = MemoryFile(
        uri="viking://user/u/memories/notes/note.md",
        content="old content",
        memory_type="notes",
        extra_fields={"note_name": "note"},
    )
    op = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="notes",
        uris=["viking://user/u/memories/notes/note.md"],
        memory_fields={
            "note_name": "note",
            "content": StrPatch(
                blocks=[SearchReplaceBlock(search="old content", replace="new content")]
            ),
        },
    )

    fast_path, reason = classify_memory_merge_mode([op], schema=_registry().get("notes"))

    assert fast_path is False
    assert reason == "single_existing_content_changed"


@pytest.mark.asyncio
async def test_streaming_memory_updater_persists_source_extraction_id_trace_id_and_hides_from_read(
    monkeypatch,
):
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
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=0.01,
            timer_check_interval_seconds=0.01,
        ),
    )
    op = _note_op("note_source")
    result = await updater.submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[op],
                delete_file_contents=[],
                errors=[],
            ),
            messages=[Message(id="m1", role="user", parts=[TextPart("note source")])],
            ctx=_ctx(),
            metadata={"source_extraction_id": "extract_1", "trace_id": "trace_1"},
        )
    )

    assert result.apply_result.written_uris == [op.uris[0]]
    assert '"source_extraction_id": "extract_1"' in fs.files[op.uris[0]]
    assert '"last_update_trace_id": "trace_1"' in fs.files[op.uris[0]]

    from openviking.server.identity import ToolContext
    from openviking.session.memory.tools import MemoryReadTool

    read_result = await MemoryReadTool().execute(
        ToolContext(viking_fs=fs, request_ctx=_ctx(), read_file_contents={}),
        uri=op.uris[0],
    )

    assert "source_extraction_id" not in read_result
    assert "last_update_trace_id" not in read_result


def test_render_operation_after_file_content_persists_source_trace_id():
    schema = _registry().get("notes")
    op = _note_op("note_trace")
    op.source = MemoryOperationSource(extraction_id="extract_2", trace_id="trace_2")

    rendered = render_operation_after_file_content(
        op,
        schema=schema,
        extract_context=ExtractContext([]),
    )

    assert '"source_extraction_id": "extract_2"' in rendered
    assert '"last_update_trace_id": "trace_2"' in rendered


@pytest.mark.asyncio
async def test_cross_extraction_merge_preserves_existing_uri_without_explicit_delete(monkeypatch):
    existing_uri = "viking://user/u/memories/notes/existing.md"
    winner_uri = "viking://user/u/memories/notes/winner.md"
    old_file = __import__(
        "openviking.session.memory.dataclass", fromlist=["MemoryFile"]
    ).MemoryFile(
        uri=existing_uri,
        content="old",
        memory_type="notes",
        extra_fields={"note_name": "existing"},
    )
    existing_op = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="notes",
        uris=[existing_uri],
        memory_fields={
            "note_name": "existing",
            "content": {"blocks": [{"search": "old", "replace": "old updated"}]},
            "source_extraction_id": "extract_a",
        },
    )
    new_op = ResolvedOperation(
        old_memory_file_content=None,
        memory_type="notes",
        uris=[winner_uri],
        memory_fields={
            "note_name": "winner",
            "content": "merged content",
            "source_extraction_id": "extract_b",
        },
    )

    async def fake_run(self):
        return (
            ResolvedOperations(
                upsert_operations=[new_op],
                delete_file_contents=[],
                errors=[],
            ),
            [],
        )

    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.ExtractLoop.run",
        fake_run,
    )
    fs = InMemoryVikingFS({existing_uri: "old"})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )
    monkeypatch.setattr(
        "openviking.session.memory.memory_updater.get_viking_fs",
        lambda: fs,
    )

    merged = await merge_one_memory_type_operations(
        memory_type="notes",
        operations=[existing_op, new_op],
        messages=[],
        ctx=_ctx(),
        registry=_registry(),
    )

    assert [op.uris for op in merged.upsert_operations] == [[winner_uri]]
    assert merged.delete_file_contents == []


@pytest.mark.asyncio
async def test_patch_merge_uses_original_messages_for_output_language(monkeypatch):
    existing_uri = "viking://user/u/memories/notes/code.md"
    old_file = MemoryFile(
        uri=existing_uri,
        content="old",
        memory_type="notes",
        extra_fields={"memory_type": "notes", "topic": "code"},
    )
    existing_op = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_fields={"topic": "code", "content": "older"},
        memory_type="notes",
        uris=[existing_uri],
    )
    new_op = ResolvedOperation(
        old_memory_file_content=None,
        memory_fields={"topic": "code", "content": "new"},
        memory_type="notes",
        uris=["viking://user/u/memories/notes/code_new.md"],
    )
    captured_languages = []

    async def fake_run(self):
        captured_languages.append(self.context_provider.get_output_language())
        return (
            ResolvedOperations(
                upsert_operations=[existing_op],
                delete_file_contents=[],
                errors=[],
            ),
            [],
        )

    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.ExtractLoop.run",
        fake_run,
    )
    fs = InMemoryVikingFS({existing_uri: "old"})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )
    monkeypatch.setattr(
        "openviking.session.memory.memory_updater.get_viking_fs",
        lambda: fs,
    )

    await merge_one_memory_type_operations(
        memory_type="notes",
        operations=[existing_op, new_op],
        messages=[Message(id="m1", role="user", parts=[TextPart("请保持中文记忆")])],
        ctx=_ctx(),
        registry=_registry(),
    )

    assert captured_languages == ["zh-CN"]
