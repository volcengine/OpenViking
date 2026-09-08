# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.models.vlm.base import VLMResponse
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryOperationSkipCode,
    MemoryOperationSource,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    SkippedMemoryOperation,
    StoredLink,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import ExtractContext, MemoryUpdateResult
from openviking.session.memory.merge_op.base import FieldType, MergeOp, SearchReplaceBlock, StrPatch
from openviking.session.memory.streaming_memory_updater import (
    MemoryMergeGroupKey,
    MemoryMergePlanError,
    MemoryUpdateRequest,
    StreamingMemoryUpdater,
    StreamingMemoryUpdaterConfig,
    StreamingMemoryUpdateResult,
    _compact_case_proposal_context,
    build_candidate_merge_proposals,
    build_memory_merge_proposals,
    classify_memory_merge_mode,
    create_memory_merge_plan_model,
    enforce_merge_group_peer_id,
    filter_valid_links,
    get_streaming_memory_updater,
    merge_memory_operations,
    merge_one_memory_type_operations,
    operation_to_patch,
    reconstruct_memory_operations_from_plan,
    render_operation_after_file_content,
    split_memory_update_request_by_operation_limit,
    split_request_by_merge_group,
    validate_memory_merge_plan,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier


class InMemoryVikingFS:
    def __init__(self, files: dict[str, str] | None = None):
        self.files = dict(files or {})
        self.writes = []

    def _uri_to_path(self, uri: str, ctx=None) -> str:
        uri = _canonical_user_uri(uri, ctx)
        return f"/{uri.removeprefix('viking://')}"

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

    async def stat(self, uri: str, ctx=None, skip_count: bool = False):
        del skip_count
        uri = _canonical_user_uri(uri, ctx)
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return {"isDir": False}

    async def write_file(
        self,
        uri: str,
        content: str,
        ctx=None,
        lock_handle=None,
        lease_ref=None,
    ):
        del lock_handle, lease_ref
        uri = _canonical_user_uri(uri, ctx)
        self.files[uri] = content
        self.writes.append((uri, content, ctx))

    async def rm(
        self,
        uri: str,
        recursive: bool = False,
        ctx=None,
        lock_handle=None,
        lease_ref=None,
    ):
        del recursive, lock_handle, lease_ref
        uri = _canonical_user_uri(uri, ctx)
        self.files.pop(uri, None)


class RecordingPathlockClient:
    def __init__(self, events: list[tuple]):
        self.events = events

    async def pathlock_acquire_exact_batch(self, paths, timeout_secs=0.0):
        lease_number = len([event for event in self.events if event[0] == "acquire"]) + 1
        lease_ref = (
            "memory-batch-lease" if lease_number == 1 else f"memory-batch-lease-{lease_number}"
        )
        lease = {"lease_ref": lease_ref}
        self.events.append(("acquire", tuple(paths), timeout_secs))
        return lease

    async def pathlock_release(self, lease):
        self.events.append(("release", lease))


class PathlockedInMemoryVikingFS(InMemoryVikingFS):
    def __init__(self, files: dict[str, str] | None = None):
        super().__init__(files)
        self.events: list[tuple] = []
        self._async_agfs = RecordingPathlockClient(self.events)

    def _uri_to_path(self, uri: str, ctx=None) -> str:
        return "/" + _canonical_user_uri(uri, ctx).removeprefix("viking://")

    async def write_file(self, uri: str, content: str, ctx=None, lease_ref=None):
        self.events.append(("write", uri, lease_ref))
        return await super().write_file(uri, content, ctx=ctx, lease_ref=lease_ref)

    async def _delete_from_vector_store(self, uris, ctx=None):
        del ctx
        self.events.append(("vector_delete", tuple(uris)))


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


def _note_update_op(name: str) -> ResolvedOperation:
    uri = f"viking://user/u/memories/notes/{name}.md"
    old_file = MemoryFile(
        uri=uri,
        content=f"old {name}",
        memory_type="notes",
        extra_fields={"note_name": name},
    )
    return ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="notes",
        uris=[uri],
        memory_fields={
            "note_name": name,
            "content": StrPatch(
                blocks=[
                    SearchReplaceBlock(
                        search=f"old {name}",
                        replace=f"new {name}",
                    )
                ]
            ),
        },
    )


def _note_delete_file(name: str) -> MemoryFile:
    return MemoryFile(
        uri=f"viking://user/u/memories/notes/{name}.md",
        content=f"delete {name}",
        memory_type="notes",
        extra_fields={"note_name": name},
    )


def _peer_note_op(name: str, peer_id: str) -> ResolvedOperation:
    op = _note_op(name)
    op.memory_fields["peer_id"] = peer_id
    op.uris = [f"viking://user/u/peers/{peer_id}/memories/notes/{name}.md"]
    return op


def test_streaming_memory_updater_serializes_case_batches():
    updater = StreamingMemoryUpdater(
        registry=_registry(),
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
        ),
    )

    case_batcher = updater._create_group_batcher(
        MemoryMergeGroupKey(peer_id=None, memory_type="cases")
    )
    note_batcher = updater._create_group_batcher(
        MemoryMergeGroupKey(peer_id=None, memory_type="notes")
    )

    assert case_batcher.config.max_items_per_batch == 1
    assert note_batcher.config.max_items_per_batch == 8


def test_streaming_memory_updater_serial_case_limit_is_independent_of_global_limit():
    updater = StreamingMemoryUpdater(
        registry=_registry(),
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=4,
        ),
    )

    assert (
        updater._operation_limit_for_group(MemoryMergeGroupKey(peer_id=None, memory_type="cases"))
        == 1
    )


class _FakeMergeVLM:
    def __init__(self, responder=None):
        self.responder = responder
        self.calls = []

    async def get_completion_async(self, **kwargs):
        self.calls.append(kwargs)
        if self.responder is not None:
            return self.responder(kwargs["messages"])
        content = "\n".join(str(message.get("content") or "") for message in kwargs["messages"])
        proposal_ids = re.findall(r"proposal_id=([^\]\s]+)", content)
        return json.dumps(
            {
                "groups": [
                    {
                        "proposal_ids": [proposal_id],
                        "canonical_proposal_id": proposal_id,
                        "field_operations": {},
                    }
                    for proposal_id in proposal_ids
                ],
                "delete_proposal_ids": [],
            }
        )


def _install_fake_merge_vlm(monkeypatch, *, responder=None):
    fake_vlm = _FakeMergeVLM(responder=responder)
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_openviking_config",
        lambda: SimpleNamespace(
            vlm=SimpleNamespace(get_vlm_instance=lambda: fake_vlm),
        ),
    )
    return fake_vlm


@pytest.mark.asyncio
async def test_operation_to_patch_omits_raw_operation_metadata():
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

    patch = await operation_to_patch(op, schema=schema, extract_context=ExtractContext([]))

    assert patch.metadata == {}
    assert patch.after_file.content == "new content"


@pytest.mark.asyncio
async def test_replacement_reacquires_persisted_relation_locks_before_writes(monkeypatch):
    deleted_uri = "viking://user/u/memories/notes/deleted.md"
    neighbor_uri = "viking://user/u/memories/notes/neighbor.md"
    replacement = _note_op("replacement")
    deleted_file = MemoryFile(
        uri=deleted_uri,
        content="deleted content",
        memory_type="notes",
        extra_fields={"note_name": "deleted"},
        links=[
            {
                "from_uri": deleted_uri,
                "to_uri": neighbor_uri,
                "link_type": "related_to",
            }
        ],
    )
    neighbor_file = MemoryFile(
        uri=neighbor_uri,
        content="neighbor content",
        memory_type="notes",
        extra_fields={"note_name": "neighbor"},
    )
    fs = PathlockedInMemoryVikingFS(
        {
            deleted_uri: MemoryFileUtils.write(deleted_file),
            neighbor_uri: MemoryFileUtils.write(neighbor_file),
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
    operations = ResolvedOperations(
        upsert_operations=[replacement],
        delete_file_contents=[deleted_file.model_copy(update={"links": []})],
        errors=[],
        delete_replacements={deleted_uri: replacement.uris[0]},
    )
    messages = [Message(id="m1", role="user", parts=[TextPart("replace note")])]

    await StreamingMemoryUpdater(registry=_registry())._apply_operations(
        operations=operations,
        request=MemoryUpdateRequest(operations=operations, messages=messages, ctx=_ctx()),
        messages=messages,
    )

    acquires = [event for event in fs.events if event[0] == "acquire"]
    writes = [event for event in fs.events if event[0] == "write"]
    assert len(acquires) == 2
    assert "/user/u/memories/notes/neighbor.md" not in acquires[0][1]
    assert "/user/u/memories/notes/neighbor.md" in acquires[1][1]
    assert writes
    assert all(event[2] == {"lease_ref": "memory-batch-lease-2"} for event in writes)
    assert fs.events.index(acquires[1]) < min(fs.events.index(event) for event in writes)


@pytest.mark.asyncio
async def test_operation_to_patch_skips_failed_field_preview_update():
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

    patch = await operation_to_patch(op, schema=schema, extract_context=ExtractContext([]))

    assert patch.after_file.content == "new content"
    assert patch.after_file.extra_fields["summary"] == "old summary"
    assert isinstance(op.memory_fields["summary"], StrPatch)


@pytest.mark.asyncio
async def test_operation_to_patch_preserves_hidden_feedback_stats_metadata():
    schema = _registry().get("notes")
    old_file = MemoryFile(
        uri="viking://user/u/memories/notes/note.md",
        content="old content",
        memory_type="notes",
        extra_fields={
            "note_name": "note",
            "feedback_stats": {
                "injected_count": 3,
                "positive_count": 1,
                "negative_count": 1,
            },
        },
    )
    op = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_type="notes",
        uris=["viking://user/u/memories/notes/note.md"],
        memory_fields={
            "note_name": "note",
            "content": "new content",
        },
    )

    patch = await operation_to_patch(op, schema=schema, extract_context=ExtractContext([]))

    assert patch.after_file.content == "new content"
    assert patch.after_file.extra_fields["feedback_stats"] == {
        "injected_count": 3,
        "positive_count": 1,
        "negative_count": 1,
    }


@pytest.mark.asyncio
async def test_streaming_memory_updater_submit_applies_fast_path(monkeypatch):
    fs = PathlockedInMemoryVikingFS({})
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
    written_uri = "viking://user/u/memories/cases/重复预订处理.md"
    assert result.apply_result.written_uris == [written_uri]
    assert fs.writes
    _, written_content, _ = fs.writes[0]
    assert "重复预订处理" in written_content
    lease = {"lease_ref": "memory-batch-lease"}
    assert fs.events[0] == (
        "acquire",
        (
            "/user/u/memories/cases/.overview.md",
            "/user/u/memories/cases/重复预订处理.md",
        ),
        300.0,
    )
    assert ("write", written_uri, lease) in fs.events
    assert fs.events[-1] == ("release", lease)


@pytest.mark.asyncio
async def test_streaming_experience_archive_deletes_vectors_after_file_lock_release(monkeypatch):
    uri = "viking://user/u/memories/experiences/retired.md"
    case_uri = "viking://user/u/memories/cases/late_case.md"
    link = StoredLink(from_uri=case_uri, to_uri=uri, link_type="related_to").model_dump()
    old_file = MemoryFile(
        uri=uri,
        content="full retained body",
        memory_type="experiences",
        extra_fields={
            "memory_type": "experiences",
            "experience_name": "retired",
            "status": "promoted",
            "version": 7,
        },
    )
    current_file = old_file.model_copy(deep=True)
    current_file.backlinks = [link]
    case_file = MemoryFile(
        uri=case_uri,
        content="case body",
        links=[link],
        memory_type="cases",
        extra_fields={"memory_type": "cases", "case_name": "late_case", "version": 3},
    )
    fs = PathlockedInMemoryVikingFS(
        {
            uri: MemoryFileUtils.write(current_file),
            case_uri: MemoryFileUtils.write(case_file),
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
    registry = _registry()
    registry.register(
        MemoryTypeSchema(
            memory_type="experiences",
            description="experience memory",
            directory="viking://user/{{ user_space }}/memories/experiences",
            filename_template="{{ experience_name }}.md",
            content_template="{{ content }}",
            operation_mode="upsert",
            fields=[
                MemoryField(
                    name="experience_name",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.IMMUTABLE,
                ),
                MemoryField(
                    name="status",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.REPLACE,
                ),
                MemoryField(
                    name="content",
                    field_type=FieldType.STRING,
                    merge_op=MergeOp.REPLACE,
                ),
            ],
        )
    )
    operations = ResolvedOperations(
        upsert_operations=[],
        delete_file_contents=[old_file],
        errors=[],
    )
    request = MemoryUpdateRequest(
        operations=operations,
        messages=[],
        ctx=_ctx(),
    )

    result = await StreamingMemoryUpdater(registry=registry)._apply_operations(
        operations=operations,
        request=request,
        messages=[],
    )

    archived = MemoryFileUtils.read(fs.files[uri], uri=uri)
    assert archived.plain_content() == "full retained body"
    assert archived.extra_fields["status"] == "archived"
    assert archived.extra_fields["version"] == 8
    assert result.deleted_uris == []
    assert result.archived_uris == [uri]
    updated_case = MemoryFileUtils.read(fs.files[case_uri], uri=case_uri)
    assert all(item.get("to_uri") != uri for item in updated_case.links)
    acquire_events = [event for event in fs.events if event[0] == "acquire"]
    assert acquire_events == [
        (
            "acquire",
            (
                "/user/u/memories/experiences/.overview.md",
                "/user/u/memories/experiences/retired.md",
            ),
            300.0,
        ),
        (
            "acquire",
            (
                "/user/u/memories/cases/late_case.md",
                "/user/u/memories/experiences/.overview.md",
                "/user/u/memories/experiences/retired.md",
            ),
            300.0,
        ),
    ]
    release_index = max(index for index, event in enumerate(fs.events) if event[0] == "release")
    vector_delete_index = next(
        index for index, event in enumerate(fs.events) if event[0] == "vector_delete"
    )
    assert release_index < vector_delete_index


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
@pytest.mark.parametrize(
    ("status", "expected_link_count"),
    [("archived", 0), ("draft", 0), ("degraded", 0), ("promoted", 1)],
)
async def test_filter_valid_links_only_keeps_promoted_experiences_with_cached_reads(
    monkeypatch,
    status,
    expected_link_count,
):
    case_uri = "viking://user/u/memories/cases/report.md"
    experience_uri = "viking://user/u/memories/experiences/retired.md"

    class CountingFS(InMemoryVikingFS):
        def __init__(self):
            super().__init__(
                {
                    case_uri: MemoryFileUtils.write(
                        MemoryFile(
                            uri=case_uri,
                            content="case",
                            memory_type="cases",
                            extra_fields={"memory_type": "cases"},
                        )
                    ),
                    experience_uri: MemoryFileUtils.write(
                        MemoryFile(
                            uri=experience_uri,
                            content=f"{status} experience",
                            memory_type="experiences",
                            extra_fields={
                                "memory_type": "experiences",
                                "status": status,
                            },
                        )
                    ),
                }
            )
            self.read_counts: dict[str, int] = {}

        async def read_file(self, uri: str, ctx=None):
            canonical_uri = _canonical_user_uri(uri, ctx)
            self.read_counts[canonical_uri] = self.read_counts.get(canonical_uri, 0) + 1
            return await super().read_file(uri, ctx=ctx)

    fs = CountingFS()
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )
    link = StoredLink(
        from_uri=case_uri,
        to_uri=experience_uri,
        link_type="related_to",
        weight=1.0,
    )

    valid_links = await filter_valid_links(
        [link],
        upsert_operations=[],
        delete_file_contents=[],
        ctx=_ctx(),
    )

    assert len(valid_links) == expected_link_count
    assert fs.read_counts == {case_uri: 1, experience_uri: 1}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stored_status", "pending_status", "expected_link_count"),
    [
        (None, "promoted", 1),
        (None, "draft", 0),
        ("draft", "promoted", 1),
        ("degraded", "promoted", 1),
        ("promoted", "draft", 0),
        ("promoted", "archived", 0),
    ],
)
async def test_filter_valid_links_pre_apply_still_uses_pending_upserts(
    monkeypatch, stored_status, pending_status, expected_link_count
):
    case_uri = "viking://user/u/memories/cases/report.md"
    experience_uri = "viking://user/u/memories/experiences/rule.md"
    stored_experience = (
        MemoryFile(
            uri=experience_uri,
            memory_type="experiences",
            content="experience",
            extra_fields={"status": stored_status},
        )
        if stored_status is not None
        else None
    )
    fs = InMemoryVikingFS(
        {experience_uri: MemoryFileUtils.write(stored_experience)}
        if stored_experience is not None
        else {}
    )
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs", lambda: fs
    )
    link = StoredLink(from_uri=case_uri, to_uri=experience_uri, link_type="related_to")
    pending_operations = [
        ResolvedOperation(memory_type="cases", uris=[case_uri], memory_fields={}),
        ResolvedOperation(
            memory_type="experiences",
            uris=[experience_uri],
            memory_fields={"status": pending_status},
            old_memory_file_content=stored_experience,
        ),
    ]

    valid_links = await filter_valid_links(
        [link],
        upsert_operations=pending_operations,
        delete_file_contents=[],
        ctx=_ctx(),
    )

    assert len(valid_links) == expected_link_count
    assert case_uri not in fs.files  # The same-batch Case is not written yet.
    assert fs.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_status", "persisted_status", "expected_link_count"),
    [
        ("promoted", "draft", 0),
        ("promoted", "degraded", 0),
        ("promoted", "archived", 0),
        ("promoted", None, 0),
        ("promoted", "unknown", 0),
        ("draft", "promoted", 1),
        ("degraded", "promoted", 1),
        ("archived", "promoted", 1),
        ("promoted", "promoted", 1),
    ],
)
async def test_post_group_links_recheck_persisted_experience_status_under_endpoint_lease(
    monkeypatch, operation_status, persisted_status, expected_link_count
):
    case_uri = "viking://user/u/memories/cases/report.md"
    experience_uri = "viking://user/u/memories/experiences/rule.md"
    case = MemoryFile(uri=case_uri, memory_type="cases", content="case")
    experience = MemoryFile(
        uri=experience_uri,
        memory_type="experiences",
        content="experience",
        extra_fields={"status": operation_status},
    )

    class LeaseCheckedFS(PathlockedInMemoryVikingFS):
        async def read_file(self, uri, ctx=None):
            assert self.events[0][0] == "acquire"
            assert not any(event[0] == "release" for event in self.events)
            self.events.append(("read", uri))
            return await super().read_file(uri, ctx=ctx)

    fs = LeaseCheckedFS(
        {
            case_uri: MemoryFileUtils.write(case),
            experience_uri: MemoryFileUtils.write(experience),
        }
    )
    acquire = fs._async_agfs.pathlock_acquire_exact_batch

    async def acquire_after_concurrent_status_update(paths, timeout_secs=0.0):
        # Feedback wins the lock after group application and before link repair.
        current = experience.model_copy(deep=True)
        if persisted_status is None:
            current.extra_fields.pop("status", None)
        else:
            current.extra_fields["status"] = persisted_status
        fs.files[experience_uri] = MemoryFileUtils.write(current)
        return await acquire(paths, timeout_secs=timeout_secs)

    fs._async_agfs.pathlock_acquire_exact_batch = acquire_after_concurrent_status_update
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs", lambda: fs
    )
    link = StoredLink(from_uri=case_uri, to_uri=experience_uri, link_type="related_to")
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(memory_type="cases", uris=[case_uri], memory_fields={}),
            ResolvedOperation(
                memory_type="experiences",
                uris=[experience_uri],
                old_memory_file_content=experience,
                memory_fields={"status": operation_status},
            ),
        ],
        delete_file_contents=[],
        errors=[],
    )
    result = StreamingMemoryUpdateResult(
        operations=operations, apply_result=MemoryUpdateResult(), request_count=1
    )
    updater = StreamingMemoryUpdater()
    await updater._apply_post_group_links(
        MemoryUpdateRequest(
            operations=operations.model_copy(update={"resolved_links": [link]}),
            messages=[],
            ctx=_ctx(),
        ),
        result,
    )

    persisted_case = MemoryFileUtils.read(fs.files[case_uri], uri=case_uri)
    persisted_experience = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
    assert len(persisted_case.links) == expected_link_count
    assert len(persisted_experience.backlinks) == expected_link_count
    assert persisted_experience.extra_fields.get("status") == persisted_status
    assert len(result.operations.resolved_links) == expected_link_count
    assert fs.events[0][1] == tuple(
        sorted(fs._uri_to_path(uri) for uri in (case_uri, experience_uri))
    )
    assert fs.events[-1][0] == "release"
    writes = [event for event in fs.events if event[0] == "write"]
    assert len(writes) == 2 * expected_link_count
    assert all(event[2] == fs.events[-1][1] for event in writes)


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable_endpoint", ["case", "experience"])
@pytest.mark.parametrize("failure", ["missing", "read_error", "empty"])
async def test_post_group_links_do_not_trust_upserts_for_unavailable_endpoints(
    monkeypatch, unavailable_endpoint, failure
):
    case_uri = "viking://user/u/memories/cases/report.md"
    experience_uri = "viking://user/u/memories/experiences/rule.md"
    unavailable_uri = case_uri if unavailable_endpoint == "case" else experience_uri
    files = [
        MemoryFile(uri=case_uri, memory_type="cases", content="case"),
        MemoryFile(
            uri=experience_uri,
            memory_type="experiences",
            content="experience",
            extra_fields={"status": "promoted"},
        ),
    ]

    class UnavailableEndpointFS(PathlockedInMemoryVikingFS):
        async def read_file(self, uri, ctx=None):
            assert self.events[0][0] == "acquire"
            assert self.events[-1][0] != "release"
            if uri == unavailable_uri and failure == "read_error":
                raise OSError("endpoint temporarily unavailable")
            return await super().read_file(uri, ctx=ctx)

    fs = UnavailableEndpointFS({file.uri: MemoryFileUtils.write(file) for file in files})
    if failure == "missing":
        del fs.files[unavailable_uri]
    elif failure == "empty":
        fs.files[unavailable_uri] = ""
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs", lambda: fs
    )
    link = StoredLink(from_uri=case_uri, to_uri=experience_uri, link_type="related_to")
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_type=file.memory_type,
                uris=[file.uri],
                memory_fields=dict(file.extra_fields),
            )
            for file in files
        ],
        delete_file_contents=[],
        errors=[],
    )
    apply_result = MemoryUpdateResult()
    apply_result.add_error(unavailable_uri, OSError("group endpoint write failed"))
    result = StreamingMemoryUpdateResult(
        operations=operations, apply_result=apply_result, request_count=1
    )

    await StreamingMemoryUpdater()._apply_post_group_links(
        MemoryUpdateRequest(
            operations=operations.model_copy(update={"resolved_links": [link]}),
            messages=[],
            ctx=_ctx(),
        ),
        result,
    )

    assert fs.writes == []
    assert result.operations.resolved_links == []
    assert result.apply_result.edited_uris == []
    assert fs.events[-1][0] == "release"


def _case_experience_registry() -> MemoryTypeRegistry:
    registry = _registry()
    registry.get(
        "cases"
    ).content_template = (
        "{{ task_signature }}\n{% for link in links %}{{ link.to_uri }}\n{% endfor %}"
    )
    registry.register(
        MemoryTypeSchema(
            memory_type="experiences",
            description="experience memory",
            directory="viking://user/{{ user_space }}/memories/experiences",
            filename_template="{{ name }}.md",
            operation_mode="upsert",
            fields=[
                MemoryField(name="name", field_type=FieldType.STRING, merge_op=MergeOp.IMMUTABLE),
                MemoryField(name="status", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
            ],
        )
    )
    return registry


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["append_only", "direct_apply"])
@pytest.mark.parametrize("link_source", ["resolved_links", "memory_fields"])
@pytest.mark.parametrize("persisted_status", ["degraded", "archived", "draft", "promoted"])
async def test_case_links_use_persisted_status_after_upserts(
    monkeypatch, entry, link_source, persisted_status
):
    case_op = _case_op("status_race")
    case_uri = case_op.uris[0]
    experience_uri = "viking://user/u/memories/experiences/status_race.md"
    experience = MemoryFile(
        uri=experience_uri,
        memory_type="experiences",
        content="experience",
        extra_fields={"status": "promoted"},
    )
    fs = PathlockedInMemoryVikingFS({experience_uri: MemoryFileUtils.write(experience)})
    acquire = fs._async_agfs.pathlock_acquire_exact_batch

    async def change_status_before_publication(paths, timeout_secs=0.0):
        if any(event[0] == "release" for event in fs.events):
            current = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
            current.extra_fields["status"] = persisted_status
            fs.files[experience_uri] = MemoryFileUtils.write(current)
        return await acquire(paths, timeout_secs=timeout_secs)

    fs._async_agfs.pathlock_acquire_exact_batch = change_status_before_publication
    for module in ("memory_updater", "streaming_memory_updater"):
        monkeypatch.setattr(f"openviking.session.memory.{module}.get_viking_fs", lambda: fs)
    link = StoredLink(from_uri=case_uri, to_uri=experience_uri, link_type="related_to")
    if link_source == "memory_fields":
        case_op.memory_fields["links"] = [link.model_dump()]
    operations = ResolvedOperations(
        upsert_operations=[case_op],
        delete_file_contents=[],
        errors=[],
        resolved_links=[link] if link_source == "resolved_links" else [],
    )
    request = MemoryUpdateRequest(operations=operations, messages=[], ctx=_ctx())
    updater = StreamingMemoryUpdater(registry=_case_experience_registry())
    if entry == "append_only":
        outcome = await updater.submit(request)
        operations = outcome.operations
        result = outcome.apply_result
    else:
        result = await updater._apply_operations(
            operations=operations, request=request, messages=[]
        )

    expected = int(persisted_status == "promoted")
    assert result.errors == []
    assert result.written_uris == [case_uri]
    case = MemoryFileUtils.read(fs.files[case_uri], uri=case_uri)
    current = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
    assert len(case.links) == len(current.backlinks) == len(operations.resolved_links) == expected
    assert (experience_uri in case.content) is bool(expected)
    # The first Case write must not publish from the stale operation preview.
    first_case_write = next(content for uri, content, _ in fs.writes if uri == case_uri)
    assert MemoryFileUtils.read(first_case_write, uri=case_uri).links == []
    assert fs.events[-1][0] == "release"
    assert len([event for event in fs.events if event[0] == "acquire"]) == 2
    if expected and entry == "direct_apply":
        assert result.files_by_uri[case_uri].links == case.links
        assert experience_uri not in result.files_by_uri


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_status", [None, "draft", "promoted"])
@pytest.mark.parametrize("fail_experience_write", [False, True])
async def test_same_batch_case_links_follow_successful_experience_write(
    monkeypatch, initial_status, fail_experience_write
):
    case_op = _case_op("same_batch")
    case_uri = case_op.uris[0]
    experience_uri = "viking://user/u/memories/experiences/same_batch.md"
    old_experience = (
        MemoryFile(
            uri=experience_uri,
            memory_type="experiences",
            content="experience",
            extra_fields={"name": "same_batch", "status": initial_status},
        )
        if initial_status is not None
        else None
    )

    class FailingExperienceFS(PathlockedInMemoryVikingFS):
        async def write_file(self, uri, content, ctx=None, lease_ref=None):
            if uri == experience_uri and fail_experience_write:
                raise OSError("experience write failed")
            return await super().write_file(uri, content, ctx=ctx, lease_ref=lease_ref)

    fs = FailingExperienceFS(
        {experience_uri: MemoryFileUtils.write(old_experience)} if old_experience else {}
    )
    for module in ("memory_updater", "streaming_memory_updater"):
        monkeypatch.setattr(f"openviking.session.memory.{module}.get_viking_fs", lambda: fs)
    link = StoredLink(from_uri=case_uri, to_uri=experience_uri, link_type="related_to")
    operations = ResolvedOperations(
        upsert_operations=[
            case_op,  # Case runs first: do not publish before the EXP write succeeds.
            ResolvedOperation(
                memory_type="experiences",
                uris=[experience_uri],
                old_memory_file_content=old_experience,
                memory_fields={
                    "name": "same_batch",
                    "status": "promoted",
                    "backlinks": [link.model_dump()],
                },
            ),
        ],
        delete_file_contents=[],
        errors=[],
        resolved_links=[link],
    )
    request = MemoryUpdateRequest(operations=operations, messages=[], ctx=_ctx())
    result = await StreamingMemoryUpdater(registry=_case_experience_registry())._apply_operations(
        operations=operations, request=request, messages=[]
    )

    expected = int(not fail_experience_write)
    case = MemoryFileUtils.read(fs.files[case_uri], uri=case_uri)
    assert len(case.links) == len(operations.resolved_links) == expected
    assert bool(result.errors) == fail_experience_write
    if experience_uri in fs.files:
        current = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
        assert len(current.backlinks) == expected
    assert (experience_uri in case.content) is bool(expected)
    assert fs.events[-1][0] == "release"


@pytest.mark.asyncio
async def test_deferred_case_links_use_allocated_add_only_uri(monkeypatch):
    case_op = _case_op("numbered")
    original_uri = case_op.uris[0]
    experience_uri = "viking://user/u/memories/experiences/numbered.md"
    original_case = MemoryFile(uri=original_uri, memory_type="cases", content="do not change")
    experience = MemoryFile(
        uri=experience_uri,
        memory_type="experiences",
        content="experience",
        extra_fields={"status": "promoted"},
    )
    fs = PathlockedInMemoryVikingFS(
        {item.uri: MemoryFileUtils.write(item) for item in (original_case, experience)}
    )
    for module in ("memory_updater", "streaming_memory_updater"):
        monkeypatch.setattr(f"openviking.session.memory.{module}.get_viking_fs", lambda: fs)
    result = await StreamingMemoryUpdater(registry=_case_experience_registry()).submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[case_op],
                delete_file_contents=[],
                errors=[],
                resolved_links=[StoredLink(from_uri=original_uri, to_uri=experience_uri)],
            ),
            messages=[],
            ctx=_ctx(),
            metadata={"source_extraction_id": "numbered-case-extraction"},
        )
    )
    allocated_uri = result.apply_result.written_uris[0]
    assert allocated_uri != original_uri
    assert fs.files[original_uri] == MemoryFileUtils.write(original_case)
    assert result.operations.resolved_links[0].from_uri == allocated_uri
    assert MemoryFileUtils.read(fs.files[allocated_uri], uri=allocated_uri).links
    current = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
    assert current.backlinks[0]["from_uri"] == allocated_uri
    publication_acquire = [event for event in fs.events if event[0] == "acquire"][-1]
    assert publication_acquire[1] == tuple(
        sorted(fs._uri_to_path(uri) for uri in (allocated_uri, experience_uri))
    )
    assert result.apply_result.errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_mode", "preallocated"), [("add_only", False), ("add_only", True), ("upsert", False)]
)
@pytest.mark.parametrize("link_source", ["resolved_links", "memory_fields"])
async def test_submit_preserves_case_experience_links_across_apply_groups(
    monkeypatch, case_mode, preallocated, link_source
):
    case_op = _case_op("mixed_group")
    canonical_uri = case_op.uris[0]
    if preallocated:
        case_op.uris = [canonical_uri.removesuffix(".md") + "_2.md"]
        case_op.add_only_uri_bases = {case_op.uris[0]: canonical_uri}
    original_uri = case_op.uris[0]
    experience_uri = "viking://user/u/memories/experiences/mixed_group.md"
    old_case = MemoryFile(uri=original_uri, memory_type="cases", content="existing case")
    fs = PathlockedInMemoryVikingFS(
        {original_uri: MemoryFileUtils.write(old_case)} if case_mode == "add_only" else {}
    )
    if preallocated:
        fs.files[canonical_uri] = MemoryFileUtils.write(
            old_case.model_copy(update={"uri": canonical_uri})
        )
    for module in ("memory_updater", "streaming_memory_updater"):
        monkeypatch.setattr(f"openviking.session.memory.{module}.get_viking_fs", lambda: fs)
    registry = _case_experience_registry()
    registry.get("cases").operation_mode = case_mode
    updater = StreamingMemoryUpdater(
        registry=registry,
        config=StreamingMemoryUpdaterConfig(
            max_wait_seconds=0.01, timer_check_interval_seconds=0.01
        ),
    )

    async def merge_without_model(self, requests):
        # Exercise the real group scheduling, apply, and post-group publication
        # without involving a model in this ordering regression.
        return ResolvedOperations(
            upsert_operations=[
                op for request in requests for op in request.operations.upsert_operations
            ],
            delete_file_contents=[],
            errors=[],
            resolved_links=[],
        )

    monkeypatch.setattr(StreamingMemoryUpdater, "_merge_requests", merge_without_model)
    link = StoredLink(from_uri=original_uri, to_uri=experience_uri)
    if link_source == "memory_fields":
        case_op.memory_fields["links"] = [link.model_dump()]
    result = await updater.submit(
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[
                    case_op,
                    ResolvedOperation(
                        memory_type="experiences",
                        uris=[experience_uri],
                        memory_fields={"name": "mixed_group", "status": "promoted"},
                    ),
                ],
                delete_file_contents=[],
                errors=[],
                resolved_links=[link] if link_source == "resolved_links" else [],
            ),
            messages=[],
            ctx=_ctx(),
            metadata={"source_extraction_id": "mixed-group-extraction"},
        )
    )
    await updater.close()
    case_uri = next(uri for uri in result.apply_result.written_uris if "/cases/" in uri)
    if case_mode == "add_only":
        assert case_uri != original_uri
        assert fs.files[original_uri] == MemoryFileUtils.write(old_case)
    case = MemoryFileUtils.read(fs.files[case_uri], uri=case_uri)
    experience = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
    assert case.links[0]["to_uri"] == experience_uri
    assert experience.backlinks[0]["from_uri"] == case_uri
    assert result.operations.resolved_links[0].from_uri == case_uri
    assert experience_uri in case.content
    assert result.apply_result.errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["direct_apply", "mixed_submit"])
@pytest.mark.parametrize(("occupied", "proposal_count"), [(1, 2), (2, 3)])
async def test_allocation_remaps_each_case_once_without_following_other_proposal_names(
    monkeypatch, entry, occupied, proposal_count
):
    canonical_uri = "viking://user/u/memories/cases/overlapping.md"
    experience_uris = [
        f"viking://user/u/memories/experiences/overlapping_{ordinal}.md"
        for ordinal in range(1, proposal_count + 1)
    ]

    def numbered_uri(ordinal):
        return (
            canonical_uri if ordinal == 1 else canonical_uri.removesuffix(".md") + f"_{ordinal}.md"
        )

    files = {
        numbered_uri(ordinal): MemoryFileUtils.write(
            MemoryFile(
                uri=numbered_uri(ordinal), memory_type="cases", content=f"old case {ordinal}"
            )
        )
        for ordinal in range(1, occupied + 1)
    }
    fs = PathlockedInMemoryVikingFS(files)
    for module in ("memory_updater", "streaming_memory_updater"):
        monkeypatch.setattr(f"openviking.session.memory.{module}.get_viking_fs", lambda: fs)
    case_ops = []
    for ordinal in range(1, proposal_count + 1):
        op = _case_op(f"overlapping_{ordinal}")
        op.uris = [numbered_uri(ordinal)]
        op.add_only_uri_bases = {op.uris[0]: canonical_uri}
        case_ops.append(op)
    operations = ResolvedOperations(
        upsert_operations=case_ops
        + [
            ResolvedOperation(
                memory_type="experiences",
                uris=[experience_uri],
                memory_fields={"name": f"overlapping_{ordinal}", "status": "promoted"},
            )
            for ordinal, experience_uri in enumerate(experience_uris, start=1)
        ],
        delete_file_contents=[],
        errors=[],
        resolved_links=[
            StoredLink(from_uri=op.uris[0], to_uri=experience_uri)
            for op, experience_uri in zip(case_ops, experience_uris, strict=True)
        ],
    )
    request = MemoryUpdateRequest(
        operations=operations,
        messages=[],
        ctx=_ctx(),
        metadata={"source_extraction_id": "overlapping-allocation"},
    )
    updater = StreamingMemoryUpdater(
        registry=_case_experience_registry(),
        config=StreamingMemoryUpdaterConfig(
            max_wait_seconds=0.01, timer_check_interval_seconds=0.01
        ),
    )
    if entry == "mixed_submit":

        async def merge_without_model(self, requests):
            return ResolvedOperations(
                upsert_operations=[
                    op for item in requests for op in item.operations.upsert_operations
                ],
                delete_file_contents=[],
                errors=[],
            )

        monkeypatch.setattr(StreamingMemoryUpdater, "_merge_requests", merge_without_model)
        outcome = await updater.submit(request)
        operations = outcome.operations
        result = outcome.apply_result
        await updater.close()
    else:
        result = await updater._apply_operations(
            operations=operations, request=request, messages=[]
        )

    expected_case_uris = {
        numbered_uri(ordinal) for ordinal in range(occupied + 1, occupied + proposal_count + 1)
    }
    assert set(result.written_uris) == expected_case_uris | set(experience_uris)
    expected_relations = {
        (numbered_uri(occupied + ordinal), experience_uri)
        for ordinal, experience_uri in enumerate(experience_uris, start=1)
    }
    assert {
        (link.from_uri, link.to_uri) for link in operations.resolved_links
    } == expected_relations
    for case_uri, experience_uri in expected_relations:
        experience = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
        assert [link["from_uri"] for link in experience.backlinks] == [case_uri]
        case = MemoryFileUtils.read(fs.files[case_uri], uri=case_uri)
        assert [link["to_uri"] for link in case.links] == [experience_uri]
    for uri, content in files.items():
        assert fs.files[uri] == content
    assert result.errors == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_endpoint", ["case", "experience"])
async def test_case_link_publication_reports_partial_failure_without_dangling_forward_link(
    monkeypatch, failing_endpoint
):
    case_uri = "viking://user/u/memories/cases/publication.md"
    experience_uri = "viking://user/u/memories/experiences/publication.md"
    failing_uri = case_uri if failing_endpoint == "case" else experience_uri

    class FailingLinkFS(PathlockedInMemoryVikingFS):
        async def write_file(self, uri, content, ctx=None, lease_ref=None):
            if uri == failing_uri:
                raise OSError("link publication failed")
            return await super().write_file(uri, content, ctx=ctx, lease_ref=lease_ref)

    fs = FailingLinkFS(
        {
            item.uri: MemoryFileUtils.write(item)
            for item in (
                MemoryFile(uri=case_uri, memory_type="cases", content="case"),
                MemoryFile(
                    uri=experience_uri,
                    memory_type="experiences",
                    content="experience",
                    extra_fields={"status": "promoted"},
                ),
            )
        }
    )
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs", lambda: fs
    )
    result = StreamingMemoryUpdateResult(
        operations=ResolvedOperations(upsert_operations=[], delete_file_contents=[], errors=[]),
        apply_result=MemoryUpdateResult(),
        request_count=1,
    )
    await StreamingMemoryUpdater(registry=_case_experience_registry())._apply_post_group_links(
        MemoryUpdateRequest(
            operations=result.operations.model_copy(
                update={"resolved_links": [StoredLink(from_uri=case_uri, to_uri=experience_uri)]}
            ),
            messages=[],
            ctx=_ctx(),
        ),
        result,
    )
    assert MemoryFileUtils.read(fs.files[case_uri], uri=case_uri).links == []
    experience = MemoryFileUtils.read(fs.files[experience_uri], uri=experience_uri)
    assert len(experience.backlinks) == int(failing_endpoint == "case")
    assert result.operations.resolved_links == []
    assert result.apply_result.errors[0][0] == failing_uri
    assert fs.events[-1][0] == "release"


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
    _install_fake_merge_vlm(monkeypatch)

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


@pytest.mark.asyncio
async def test_streaming_memory_updater_continues_case_queue_after_one_merge_failure(monkeypatch):
    registry = _registry()
    registry.get("cases").operation_mode = "upsert"
    bad_case_name = "bad_case"
    merge_attempts: list[str] = []
    applied_uris: list[str] = []

    async def fake_merge_requests(self, requests):
        del self
        assert len(requests) == 1
        operation = requests[0].operations.upsert_operations[0]
        case_name = operation.memory_fields["case_name"]
        merge_attempts.append(case_name)
        if case_name == bad_case_name:
            raise MemoryMergePlanError("synthetic bad case merge")
        return ResolvedOperations(
            upsert_operations=[operation],
            delete_file_contents=[],
            errors=[],
        )

    async def fake_apply_operations(self, *, operations, request, messages):
        del self, request, messages
        result = MemoryUpdateResult()
        for operation in operations.upsert_operations:
            for uri in operation.uris or []:
                applied_uris.append(uri)
                result.add_written(uri)
        return result

    monkeypatch.setattr(StreamingMemoryUpdater, "_merge_requests", fake_merge_requests)
    monkeypatch.setattr(StreamingMemoryUpdater, "_apply_operations", fake_apply_operations)

    updater = StreamingMemoryUpdater(
        registry=registry,
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=1.0,
            timer_check_interval_seconds=0.01,
        ),
    )
    names = [f"good_case_{index}" for index in range(8)]
    names.insert(6, bad_case_name)
    requests = [
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[_case_op(name)],
                delete_file_contents=[],
                errors=[],
            ),
            messages=[Message(id=f"m-{name}", role="user", parts=[TextPart(name)])],
            ctx=_ctx(),
        )
        for name in names
    ]

    outcomes = await asyncio.gather(
        *(updater.submit(request) for request in requests),
        return_exceptions=True,
    )
    await updater.close()

    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    successes = [
        outcome for outcome in outcomes if isinstance(outcome, StreamingMemoryUpdateResult)
    ]
    assert merge_attempts == names
    assert len(failures) == 1
    assert str(failures[0]) == "synthetic bad case merge"
    assert len(successes) == 8

    bad_uri = _case_op(bad_case_name).uris[0]
    good_uris = {_case_op(name).uris[0] for name in names if name != bad_case_name}
    assert bad_uri not in applied_uris
    assert set(applied_uris) == good_uris
    assert len(applied_uris) == len(good_uris)
    assert all(
        outcome.apply_result.written_uris
        == [requests[index].operations.upsert_operations[0].uris[0]]
        for index, outcome in enumerate(outcomes)
        if isinstance(outcome, StreamingMemoryUpdateResult)
    )


@pytest.mark.asyncio
async def test_streaming_memory_updater_bisects_merge_failure_before_apply(monkeypatch):
    bad_note_name = "bad_note"
    merge_attempts: list[list[str]] = []
    applied_uris: list[str] = []

    async def fake_merge_requests(self, requests):
        del self
        operations = [
            operation for request in requests for operation in request.operations.upsert_operations
        ]
        names = [operation.memory_fields["note_name"] for operation in operations]
        merge_attempts.append(names)
        if bad_note_name in names:
            raise MemoryMergePlanError("synthetic bad note merge")
        return ResolvedOperations(
            upsert_operations=operations,
            delete_file_contents=[],
            errors=[],
        )

    async def fake_apply_operations(self, *, operations, request, messages):
        del self, request, messages
        # Bisection must finish before the first write-capable apply begins.
        assert [bad_note_name] in merge_attempts
        result = MemoryUpdateResult()
        for operation in operations.upsert_operations:
            for uri in operation.uris or []:
                applied_uris.append(uri)
                result.add_written(uri)
        return result

    monkeypatch.setattr(StreamingMemoryUpdater, "_merge_requests", fake_merge_requests)
    monkeypatch.setattr(StreamingMemoryUpdater, "_apply_operations", fake_apply_operations)

    updater = StreamingMemoryUpdater(
        registry=_registry(),
        config=StreamingMemoryUpdaterConfig(
            max_operations_per_update=8,
            max_wait_seconds=1.0,
            timer_check_interval_seconds=0.01,
        ),
    )
    names = [f"good_note_{index}" for index in range(8)]
    names.insert(6, bad_note_name)
    requests = [
        MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[_note_op(name)],
                delete_file_contents=[],
                errors=[],
            ),
            messages=[Message(id=f"m-{name}", role="user", parts=[TextPart(name)])],
            ctx=_ctx(),
        )
        for name in names
    ]

    outcomes = await asyncio.gather(
        *(updater.submit(request) for request in requests),
        return_exceptions=True,
    )
    await updater.close()

    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    successes = [
        outcome for outcome in outcomes if isinstance(outcome, StreamingMemoryUpdateResult)
    ]
    assert merge_attempts[0] == names[:8]
    assert [bad_note_name] in merge_attempts
    assert names[8:] in merge_attempts
    assert len(failures) == 1
    assert str(failures[0]) == "synthetic bad note merge"
    assert len(successes) == 8

    bad_uri = _note_op(bad_note_name).uris[0]
    good_uris = {_note_op(name).uris[0] for name in names if name != bad_note_name}
    assert bad_uri not in applied_uris
    assert set(applied_uris) == good_uris
    assert len(applied_uris) == len(good_uris)


@pytest.mark.asyncio
async def test_merge_requests_skips_patch_merge_for_same_session(monkeypatch):
    merge_mock = AsyncMock()
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.merge_memory_operations",
        merge_mock,
    )
    updater = StreamingMemoryUpdater(registry=_registry())

    def make_request(suffix: str, extraction_id: str) -> MemoryUpdateRequest:
        return MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[
                    _note_op(f"add_{suffix}"),
                    _note_update_op(f"update_{suffix}"),
                ],
                delete_file_contents=[_note_delete_file(f"delete_{suffix}")],
                errors=[],
            ),
            messages=[],
            ctx=_ctx(),
            metadata={
                "session_id": "same-session",
                "source_extraction_id": extraction_id,
            },
        )

    merged = await updater._merge_requests(
        [
            make_request("a", "extract-a"),
            make_request("b", "extract-b"),
        ]
    )

    merge_mock.assert_not_awaited()
    assert len(merged.upsert_operations) == 4
    assert len(merged.delete_file_contents) == 2


@pytest.mark.asyncio
async def test_merge_requests_prepares_same_session_case_without_merging_notes():
    updater = StreamingMemoryUpdater(registry=_registry())
    request = MemoryUpdateRequest(
        operations=ResolvedOperations(
            upsert_operations=[_case_op("prepared_case"), _note_op("direct_note")],
            delete_file_contents=[],
            errors=[],
        ),
        messages=[],
        ctx=_ctx(),
        metadata={
            "session_id": "same-session",
            "source_extraction_id": "extract-case",
        },
    )
    request.operations.upsert_operations[0].source = MemoryOperationSource(
        session_id="same-session",
        extraction_id="extract-case",
    )

    merged = await updater._merge_requests([request])

    operations_by_type = {
        operation.memory_type: operation for operation in merged.upsert_operations
    }
    case_fields = operations_by_type["cases"].memory_fields
    assert case_fields["case_status"] == "draft"
    assert case_fields["source_count"] == 1
    assert case_fields["last_compacted_source_count"] == 0
    assert case_fields["last_compacted_version"] == 0
    assert "case_identity" in case_fields
    assert operations_by_type["notes"].memory_fields == _note_op("direct_note").memory_fields


@pytest.mark.asyncio
async def test_merge_requests_merges_cross_session_operation_kinds_in_parallel(monkeypatch):
    entered: set[str] = set()
    all_entered = asyncio.Event()
    release = asyncio.Event()

    async def fake_merge_memory_operations(**kwargs):
        operations = kwargs["operations"]
        if operations.delete_file_contents:
            kind = "delete"
            assert not operations.upsert_operations
        elif all(op.old_memory_file_content is None for op in operations.upsert_operations):
            kind = "add"
        else:
            kind = "update"
            assert all(
                op.old_memory_file_content is not None for op in operations.upsert_operations
            )
        assert kwargs["force_merge"] is True
        entered.add(kind)
        if len(entered) == 3:
            all_entered.set()
        await release.wait()
        return operations

    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.merge_memory_operations",
        fake_merge_memory_operations,
    )
    updater = StreamingMemoryUpdater(registry=_registry())

    def make_request(suffix: str, session_id: str) -> MemoryUpdateRequest:
        return MemoryUpdateRequest(
            operations=ResolvedOperations(
                upsert_operations=[
                    _note_op(f"add_{suffix}"),
                    _note_update_op(f"update_{suffix}"),
                ],
                delete_file_contents=[_note_delete_file(f"delete_{suffix}")],
                errors=[],
            ),
            messages=[],
            ctx=_ctx(),
            metadata={"session_id": session_id},
        )

    merge_task = asyncio.create_task(
        updater._merge_requests(
            [
                make_request("a", "session-a"),
                make_request("b", "session-b"),
            ]
        )
    )
    await asyncio.wait_for(all_entered.wait(), timeout=5)
    assert not merge_task.done()
    release.set()
    merged = await asyncio.wait_for(merge_task, timeout=5)

    assert entered == {"add", "update", "delete"}
    assert len(merged.upsert_operations) == 4
    assert len(merged.delete_file_contents) == 2


@pytest.mark.asyncio
async def test_merge_requests_rejects_uri_conflicts_between_operation_kinds():
    updater = StreamingMemoryUpdater(registry=_registry())
    request = MemoryUpdateRequest(
        operations=ResolvedOperations(
            upsert_operations=[_note_op("conflict")],
            delete_file_contents=[_note_delete_file("conflict")],
            errors=[],
        ),
        messages=[],
        ctx=_ctx(),
        metadata={"session_id": "session-a"},
    )

    merged = await updater._merge_requests([request])

    assert merged.upsert_operations == []
    assert merged.delete_file_contents == []
    assert "Conflicting add/update/delete results" in merged.errors[0]


def test_scope_memory_update_result_to_submitter_filters_shared_batch_by_source():
    from openviking.session.memory.streaming_memory_updater import (
        scope_memory_update_result_to_submitter,
    )

    op_a = _note_op_with_source("scoped_a", "extract_a")
    op_b = _note_op_with_source("scoped_b", "extract_b")
    apply_result = MemoryUpdateResult()
    apply_result.add_written(op_a.uris[0])
    apply_result.add_written(op_b.uris[0])
    apply_result.add_skipped(
        SkippedMemoryOperation(
            memory_type="preferences",
            reason_code=MemoryOperationSkipCode.PEER_NOT_ALLOWED,
            reason="Target peer is outside the allowed memory scope",
            source=MemoryOperationSource(
                extraction_id="extract_a",
                session_id="session_a",
            ),
        )
    )
    apply_result.add_skipped(
        SkippedMemoryOperation(
            memory_type="preferences",
            reason_code=MemoryOperationSkipCode.PEER_MEMORY_DISABLED,
            reason="Peer memory writes are disabled",
            source=MemoryOperationSource(
                extraction_id="extract_b",
                session_id="session_a",
            ),
        )
    )
    batch_result = StreamingMemoryUpdateResult(
        operations=ResolvedOperations(
            upsert_operations=[op_a, op_b],
            delete_file_contents=[],
            errors=[],
            link_replacements={
                "viking://user/u/memories/cases/old_a.md": op_a.uris[0],
                "viking://user/u/memories/cases/old_b.md": op_b.uris[0],
            },
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
    assert len(scoped.apply_result.skipped_operations) == 1
    assert scoped.apply_result.skipped_operations[0].reason_code == (
        MemoryOperationSkipCode.PEER_NOT_ALLOWED
    )
    assert scoped.operations.upsert_operations == [op_a]
    assert scoped.operations.link_replacements == {
        "viking://user/u/memories/cases/old_a.md": op_a.uris[0]
    }
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


def test_split_request_keeps_unresolved_upserts_separate_from_delete_groups():
    replacement = _note_op("replacement")
    unresolved = _note_op("skipped")
    unresolved.uris = []
    old_file = _note_delete_file("old")
    request = MemoryUpdateRequest(
        operations=ResolvedOperations(
            upsert_operations=[replacement, unresolved],
            delete_file_contents=[old_file],
            errors=[],
            delete_replacements={old_file.uri: replacement.uris[0]},
        ),
        messages=[],
        ctx=_ctx(),
    )

    grouped = split_request_by_merge_group(request)

    assert len(grouped) == 2
    replacement_key, replacement_request = grouped[0]
    assert replacement_key == MemoryMergeGroupKey(peer_id=None, memory_type="notes")
    assert replacement_request.operations.upsert_operations == [replacement]
    assert replacement_request.operations.delete_file_contents == [old_file]
    assert replacement_request.operations.delete_replacements == {old_file.uri: replacement.uris[0]}

    unresolved_key, unresolved_request = grouped[1]
    assert unresolved_key == MemoryMergeGroupKey(peer_id=None, memory_type="")
    assert unresolved_request.operations.upsert_operations == [unresolved]
    assert unresolved_request.operations.delete_file_contents == []
    assert unresolved_request.operations.delete_replacements == {}


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


def test_split_memory_update_request_enforces_operation_hard_limit():
    operations = [_note_op(f"note_{index}") for index in range(9)]
    request = MemoryUpdateRequest(
        operations=ResolvedOperations(
            upsert_operations=operations,
            delete_file_contents=[],
            errors=[],
        ),
        messages=[],
        ctx=_ctx(),
    )

    chunks = split_memory_update_request_by_operation_limit(request, 8)

    assert [len(chunk.operations.upsert_operations) for chunk in chunks] == [8, 1]
    assert [op for chunk in chunks for op in chunk.operations.upsert_operations] == operations


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
    _install_fake_merge_vlm(monkeypatch)

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
    fs = PathlockedInMemoryVikingFS({})
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
    post_link_acquire = [event for event in fs.events if event[0] == "acquire"][-1]
    assert post_link_acquire[1] == (
        "/user/u/memories/notes/linked_self.md",
        "/user/u/peers/web-visitor-alice/memories/notes/linked_peer.md",
    )
    post_link_events = fs.events[fs.events.index(post_link_acquire) :]
    post_link_lease = post_link_events[-1][1]
    assert post_link_events[-1] == ("release", post_link_lease)
    assert {event[1] for event in post_link_events if event[0] == "write"} == {
        self_op.uris[0],
        peer_op.uris[0],
    }
    assert all(event[2] == post_link_lease for event in post_link_events if event[0] == "write")


async def test_classify_memory_merge_mode_forces_cross_extraction_merge():
    op1 = _note_op_with_source("note_a", "extract_a")
    op2 = _note_op_with_source("note_b", "extract_b")

    fast_path, reason = await classify_memory_merge_mode(
        [op1, op2], schema=_registry().get("notes")
    )

    assert fast_path is False
    assert reason == "cross_extraction_batch"


async def test_classify_memory_merge_mode_treats_noop_str_patch_as_unchanged():
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

    fast_path, reason = await classify_memory_merge_mode([op], schema=_registry().get("notes"))

    assert fast_path is True
    assert reason == "single_existing_content_unchanged"


async def test_classify_memory_merge_mode_detects_changed_str_patch_after_preview():
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

    fast_path, reason = await classify_memory_merge_mode([op], schema=_registry().get("notes"))

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


async def test_render_operation_after_file_content_persists_source_trace_id():
    schema = _registry().get("notes")
    op = _note_op("note_trace")
    op.source = MemoryOperationSource(extraction_id="extract_2", trace_id="trace_2")

    rendered = await render_operation_after_file_content(
        op,
        schema=schema,
        extract_context=ExtractContext([]),
    )

    assert '"source_extraction_id": "extract_2"' in rendered
    assert '"last_update_trace_id": "trace_2"' in rendered


@pytest.mark.asyncio
async def test_render_case_operation_strips_proposed_identity_from_new_file():
    schema = _registry().get("cases")
    op = _case_op("case_transient_identity")
    op.memory_fields["case_identity"] = '{"goal":"stored"}'
    op.memory_fields["_proposed_case_identity"] = '{"goal":"proposed"}'

    rendered = await render_operation_after_file_content(
        op,
        schema=schema,
        extract_context=ExtractContext([]),
    )
    parsed = MemoryFileUtils.read(rendered, uri=op.uris[0])

    assert parsed.extra_fields["case_identity"] == '{"goal":"stored"}'
    assert "_proposed_case_identity" not in parsed.extra_fields


@pytest.mark.asyncio
async def test_render_case_operation_cleans_legacy_proposed_identity():
    schema = _registry().get("cases")
    op = _case_op("case_legacy_transient_identity")
    op.old_memory_file_content = MemoryFile(
        uri=op.uris[0],
        content="legacy case",
        memory_type="cases",
        extra_fields={
            "case_name": "case_legacy_transient_identity",
            "case_identity": '{"goal":"stored"}',
            "_proposed_case_identity": '{"goal":"stale"}',
        },
    )
    op.memory_fields = {
        "case_name": "case_legacy_transient_identity",
        "case_identity": '{"goal":"stored"}',
    }

    rendered = await render_operation_after_file_content(
        op,
        schema=schema,
        extract_context=ExtractContext([]),
    )
    parsed = MemoryFileUtils.read(rendered, uri=op.uris[0])

    assert parsed.extra_fields["case_identity"] == '{"goal":"stored"}'
    assert "_proposed_case_identity" not in parsed.extra_fields


@pytest.mark.asyncio
async def test_cross_extraction_merge_deletes_existing_loser_from_validated_group(monkeypatch):
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

    _install_fake_merge_vlm(
        monkeypatch,
        responder=lambda messages: json.dumps(
            {
                "groups": [
                    {
                        "proposal_ids": ["extract_a:0", "extract_b:1"],
                        "canonical_proposal_id": "extract_b:1",
                        "field_operations": {},
                    }
                ],
                "delete_proposal_ids": [],
            }
        ),
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
    assert [file.uri for file in merged.delete_file_contents] == [existing_uri]
    assert merged.delete_replacements == {existing_uri: winner_uri}


@pytest.mark.asyncio
async def test_force_merge_sends_delete_only_group_through_patch_merge(monkeypatch):
    delete_file = _note_delete_file("obsolete")
    replacement_uri = "viking://user/u/memories/notes/replacement.md"
    fake_vlm = _install_fake_merge_vlm(
        monkeypatch,
        responder=lambda messages: json.dumps(
            {
                "groups": [],
                "delete_proposal_ids": ["batch:delete:0"],
            }
        ),
    )
    fs = InMemoryVikingFS({delete_file.uri: delete_file.content})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )

    merged = await merge_memory_operations(
        operations=ResolvedOperations(
            upsert_operations=[],
            delete_file_contents=[delete_file],
            errors=[],
            delete_replacements={delete_file.uri: replacement_uri},
        ),
        messages=[],
        ctx=_ctx(),
        registry=_registry(),
        force_merge=True,
    )

    assert len(fake_vlm.calls) == 1
    assert merged.delete_file_contents == [delete_file]
    assert merged.delete_replacements == {delete_file.uri: replacement_uri}


@pytest.mark.asyncio
async def test_force_merge_does_not_drop_add_only_delete():
    delete_file = MemoryFile(
        uri="viking://user/u/memories/cases/obsolete.md",
        content="obsolete",
        memory_type="cases",
        extra_fields={"case_name": "obsolete"},
    )

    merged = await merge_one_memory_type_operations(
        memory_type="cases",
        operations=[],
        delete_files=[delete_file],
        messages=[],
        ctx=_ctx(),
        registry=_registry(),
        force_merge=True,
    )

    assert merged.delete_file_contents == [delete_file]


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
    captured_system_prompts = []

    def respond(messages):
        captured_system_prompts.append(messages[0]["content"])
        return json.dumps(
            {
                "groups": [
                    {
                        "proposal_ids": ["batch:0", "batch:1"],
                        "canonical_proposal_id": "batch:0",
                        "field_operations": {},
                    }
                ],
                "delete_proposal_ids": [],
            }
        )

    _install_fake_merge_vlm(monkeypatch, responder=respond)
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

    assert len(captured_system_prompts) == 1
    assert "All memory content must be written in zh-CN." in captured_system_prompts[0]


@pytest.mark.asyncio
async def test_patch_merge_reconstructs_canonical_with_existing_merge_op(monkeypatch):
    _install_fake_merge_vlm(
        monkeypatch,
        responder=lambda messages: json.dumps(
            {
                "groups": [
                    {
                        "proposal_ids": ["batch:0", "batch:1"],
                        "canonical_proposal_id": "batch:0",
                        "field_operations": {"content": "merged content"},
                    }
                ],
                "delete_proposal_ids": [],
            }
        ),
    )
    fs = InMemoryVikingFS({})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )

    merged = await merge_one_memory_type_operations(
        memory_type="notes",
        operations=[_note_op("note_a"), _note_op("note_b")],
        messages=[],
        ctx=_ctx(),
        registry=_registry(),
    )

    assert len(merged.upsert_operations) == 1
    assert merged.upsert_operations[0].uris == ["viking://user/u/memories/notes/note_a.md"]
    assert merged.upsert_operations[0].memory_fields["content"] == "merged content"


@pytest.mark.asyncio
async def test_merge_plan_can_select_existing_candidate_as_canonical():
    schema = _registry().get("notes")
    proposals = await build_memory_merge_proposals(
        operations=[_note_op_with_source("new_note", "extract_1")],
        delete_files=[],
        schema=schema,
        extract_context=ExtractContext([]),
    )
    candidate_uri = "viking://user/u/memories/notes/existing_note.md"
    candidate_file = MemoryFile(
        uri=candidate_uri,
        content="existing content",
        memory_type="notes",
        extra_fields={"note_name": "existing_note"},
    )
    candidates = build_candidate_merge_proposals({"candidate:existing": candidate_file})
    all_proposals = {proposal.proposal_id: proposal for proposal in [*proposals, *candidates]}
    plan = create_memory_merge_plan_model(schema).model_validate(
        {
            "groups": [
                {
                    "proposal_ids": ["extract_1:0", "candidate:existing"],
                    "canonical_proposal_id": "candidate:existing",
                    "field_operations": {"content": "merged content"},
                }
            ],
            "delete_proposal_ids": [],
        },
        strict=True,
    )

    validate_memory_merge_plan(
        plan,
        required_proposals=proposals,
        all_proposals=all_proposals,
    )
    merged = await reconstruct_memory_operations_from_plan(
        plan,
        required_proposals=proposals,
        all_proposals=all_proposals,
        schema=schema,
    )

    assert len(merged.upsert_operations) == 1
    assert merged.upsert_operations[0].uris == [candidate_uri]
    assert merged.upsert_operations[0].old_memory_file_content == candidate_file
    assert merged.upsert_operations[0].memory_fields["content"] == "merged content"
    assert merged.upsert_operations[0].memory_fields["source_extraction_id"] == "extract_1"


def test_stored_case_candidate_ignores_legacy_proposed_identity():
    canonical_identity = {
        "goal": "prepare a reusable report",
        "subject": "report document",
        "action_pattern": "analyze source data and write report",
        "success_boundary": "report is complete and usable",
        "context_constraints": ["source data is available"],
    }
    stale_identity = {
        "goal": "prepare a one-off spreadsheet",
        "subject": "spreadsheet",
        "action_pattern": "filter exact rows",
        "success_boundary": "specific workbook is saved",
        "context_constraints": ["use a fixed date"],
    }
    candidate_file = MemoryFile(
        uri="viking://user/u/memories/cases/report.md",
        content="Reusable report task.",
        memory_type="cases",
        extra_fields={
            "case_name": "report",
            "case_identity": json.dumps(canonical_identity),
            "_proposed_case_identity": json.dumps(stale_identity),
        },
    )
    proposal = build_candidate_merge_proposals({"candidate:existing": candidate_file})[0]

    context = _compact_case_proposal_context(proposal)

    assert context["case_identity"]["goal"] == canonical_identity["goal"]
    assert context["case_identity"]["subject"] == canonical_identity["subject"]


@pytest.mark.asyncio
async def test_patch_merge_rejects_missing_proposal_without_raw_fallback(monkeypatch):
    _install_fake_merge_vlm(
        monkeypatch,
        responder=lambda messages: json.dumps(
            {
                "groups": [
                    {
                        "proposal_ids": ["batch:0"],
                        "canonical_proposal_id": "batch:0",
                        "field_operations": {},
                    }
                ],
                "delete_proposal_ids": [],
            }
        ),
    )
    fs = InMemoryVikingFS({})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )

    with pytest.raises(MemoryMergePlanError, match="exactly once"):
        await merge_one_memory_type_operations(
            memory_type="notes",
            operations=[_note_op("note_a"), _note_op("note_b")],
            messages=[],
            ctx=_ctx(),
            registry=_registry(),
        )


@pytest.mark.asyncio
async def test_patch_merge_repairs_invalid_json_once(monkeypatch):
    valid_plan = json.dumps(
        {
            "groups": [
                {
                    "proposal_ids": ["batch:0"],
                    "canonical_proposal_id": "batch:0",
                    "field_operations": {},
                },
                {
                    "proposal_ids": ["batch:1"],
                    "canonical_proposal_id": "batch:1",
                    "field_operations": {},
                },
            ],
            "delete_proposal_ids": [],
        }
    )
    malformed_plan = valid_plan.replace(
        '"proposal_ids": ["batch:0"], "canonical_proposal_id"',
        '"proposal_ids": ["batch:0"] "canonical_proposal_id"',
        1,
    )
    responses = iter([malformed_plan, valid_plan])
    fake_vlm = _install_fake_merge_vlm(
        monkeypatch,
        responder=lambda messages: next(responses),
    )
    fs = InMemoryVikingFS({})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )

    merged = await merge_one_memory_type_operations(
        memory_type="notes",
        operations=[_note_op("note_a"), _note_op("note_b")],
        messages=[],
        ctx=_ctx(),
        registry=_registry(),
    )

    assert len(merged.upsert_operations) == 2
    assert len(fake_vlm.calls) == 2
    repair_messages = fake_vlm.calls[1]["messages"]
    assert "Repair JSON syntax only" in repair_messages[0]["content"]
    assert repair_messages[1] == {"role": "assistant", "content": malformed_plan}
    assert "Invalid complete JSON" in repair_messages[2]["content"]


@pytest.mark.asyncio
async def test_patch_merge_rejects_json_that_remains_invalid_after_one_repair(monkeypatch):
    malformed_plan = '{"groups":[{"proposal_ids":["batch:0"] "field_operations":{}}]}'
    fake_vlm = _install_fake_merge_vlm(
        monkeypatch,
        responder=lambda messages: malformed_plan,
    )
    fs = InMemoryVikingFS({})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )

    with pytest.raises(MemoryMergePlanError, match="JSON repair failed"):
        await merge_one_memory_type_operations(
            memory_type="notes",
            operations=[_note_op("note_a"), _note_op("note_b")],
            messages=[],
            ctx=_ctx(),
            registry=_registry(),
        )

    assert len(fake_vlm.calls) == 2


@pytest.mark.asyncio
async def test_patch_merge_rejects_truncated_output(monkeypatch):
    response = VLMResponse(
        content='{"groups":[],"delete_proposal_ids":[]}',
        finish_reason="length",
    )
    _install_fake_merge_vlm(monkeypatch, responder=lambda messages: response)
    fs = InMemoryVikingFS({})
    fs.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.get_viking_fs",
        lambda: fs,
    )

    with pytest.raises(MemoryMergePlanError, match="output truncated"):
        await merge_one_memory_type_operations(
            memory_type="notes",
            operations=[_note_op("note_a"), _note_op("note_b")],
            messages=[],
            ctx=_ctx(),
            registry=_registry(),
        )


@pytest.mark.asyncio
async def test_merge_memory_operations_propagates_merge_failure_without_fallback(monkeypatch):
    async def fail_merge(**kwargs):
        raise MemoryMergePlanError("merge failed")

    monkeypatch.setattr(
        "openviking.session.memory.streaming_memory_updater.merge_one_memory_type_operations",
        fail_merge,
    )

    with pytest.raises(MemoryMergePlanError, match="merge failed"):
        await merge_memory_operations(
            operations=ResolvedOperations(
                upsert_operations=[_note_op("note_a"), _note_op("note_b")],
                delete_file_contents=[],
                errors=[],
            ),
            messages=[],
            ctx=_ctx(),
            registry=_registry(),
            strict_extract_errors=False,
        )
