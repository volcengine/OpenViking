# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Compile can delete source records, but cannot recreate missing message evidence."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.service.memory_compile import MemoryCompileRunner
from openviking.session.memory.consolidation_context_provider import (
    ConsolidationExtractContextProvider,
    build_consolidation_isolation_handler,
)
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.extraction_output_protocol import create_extraction_output_protocol
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry, get_default_registry
from openviking.session.memory.memory_updater import MemoryUpdater, MemoryUpdateResult
from openviking.session.memory.merge_op import MergeOp
from openviking.session.memory.schema_model_generator import SchemaModelGenerator
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.template_utils import TemplateUtils
from openviking_cli.exceptions import NotFoundError
from tests.session.memory.test_consolidation_compile import _ctx
from tests.session.memory.test_consolidation_compile import language_config as _language_config
from tests.session.memory.test_extraction_output_protocol import _bind, _context, _preference_schema

language_config = _language_config


@pytest.mark.parametrize(
    "template,expected",
    [
        ("{{ extract_context.get_year(ranges) }}", True),
        ("{{ extract_context ['get_event_content'](ranges, summary) }}", True),
        ("{% set read = extract_context.read_message_ranges %}{{ read(ranges) }}", True),
        ("{{ extract_context.messages[0] }}", True),
        ("{{ extract_context.get_source_archive_suffix() }}", False),
        ("extract_context.get_year(ranges)", False),
        ("{# {{ extract_context.get_year(ranges) }} #}{{ name }}", False),
        ("{{ 'extract_context.get_year(ranges)' }}", False),
        ("{{ unrelated.get_year(ranges) }}", False),
        ("{{ extract_context[method](ranges) }}", True),
        ("{% set ctx = extract_context %}{{ ctx.get_year(ranges) }}", True),
        ("{{ (extract_context|attr('get_session_timestamp'))() }}", True),
    ],
)
def test_message_dependency_uses_jinja_references(template, expected):
    assert TemplateUtils.requires_messages(template) is expected


@pytest.mark.parametrize(
    "field",
    [
        "directory",
        "filename_template",
        "content_template",
        "embedding_template",
        "overview_template",
    ],
)
def test_message_dependency_covers_rendered_templates_not_descriptions(field):
    schema = _preference_schema(operation_mode="add_only")
    schema.description = "extract_context.get_event_content(ranges)"
    schema.fields[0].description = "extract_context.get_session_timestamp()"
    assert not schema.requires_messages()
    setattr(schema, field, "{{ extract_context.get_session_timestamp() }}")
    assert schema.requires_messages()


def _entity_schema():
    schema = _preference_schema()
    schema.memory_type = "entities"
    schema.directory = "viking://user/{{ user_space }}/memories/entities"
    return schema


def _compile_context(names, *, files=(), extra_schemas=()):
    schemas = [get_default_registry().get(name) for name in names] + list(extra_schemas)
    context = _context(schemas, files=list(files))
    context.consolidation = True
    context.operations_model = SchemaModelGenerator(
        schemas, consolidation=True
    ).create_structured_operations_model()
    return context


def _source(name):
    return MemoryFile(
        uri=f"viking://user/u1/memories/{name}/source.md",
        memory_type=name,
        content="Alice is a teacher.",
        extra_fields={"summary": "Alice is a teacher."},
    )


@pytest.mark.parametrize("output_format", ["python", "json"])
@pytest.mark.parametrize("name", ["events", "trajectories"])
@pytest.mark.parametrize("mixed", [False, True])
def test_compile_can_delete_add_only_records(output_format, name, mixed):
    source = _source(name)
    context = _compile_context(
        [name], files=[source], extra_schemas=[_entity_schema()] if mixed else []
    )
    protocol = create_extraction_output_protocol(output_format)
    _bind(protocol, context)
    program = (
        f"{name}_1.delete()\nsdk.commit()"
        if output_format == "python"
        else json.dumps({"delete_ids": [{"delete_page_id": 1, "replacement_page_id": None}]})
    )
    operations, error = protocol.parse(program, context)
    assert error is None
    assert operations.delete_ids[0].delete_page_id == 1
    assert "do not modify or delete existing" not in protocol.render_contract(context)


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["python", "json"])
@pytest.mark.parametrize("name", ["events", "trajectories"])
async def test_compile_resolves_cross_type_new_replacement(output_format, name, language_config):
    source = _source(name)
    context = _compile_context([name], files=[source], extra_schemas=[_entity_schema()])
    protocol = create_extraction_output_protocol(output_format)
    _bind(protocol, context)
    program = (
        'new = sdk.create_entities(topic="alice", content="Alice is a teacher.", score=0)\n'
        f"{name}_1.delete(replacement=new)\nsdk.commit()"
        if output_format == "python"
        else json.dumps(
            {
                "entities": [
                    {"page_id": 100, "topic": "alice", "content": "Alice is a teacher.", "score": 0}
                ],
                "delete_ids": [{"delete_page_id": 1, "replacement_page_id": 100}],
            }
        )
    )
    operations, error = protocol.parse(program, context)
    assert error is None
    registry = MemoryTypeRegistry(load_schemas=False)
    for schema in context.schemas:
        registry.register(schema)
    provider = ConsolidationExtractContextProvider(
        memory_types=[schema.memory_type for schema in context.schemas],
        memory_registry=registry,
        ctx=_ctx(),
        output_language="en",
    )
    extract_context = provider.get_extract_context()
    extract_context.page_id_map = context.page_id_map
    provider.read_file_contents.update(context.read_file_contents)
    handler = build_consolidation_isolation_handler(
        _ctx(), extract_context, memory_types=provider.memory_types, peer_id=None
    )
    handler.prepare_messages()
    provider.bind_isolation_handler(handler)
    loop = ExtractLoop(
        vlm=SimpleNamespace(model="test"),
        viking_fs=SimpleNamespace(),
        ctx=_ctx(),
        context_provider=provider,
        isolation_handler=handler,
    )
    loop._extract_context = extract_context
    resolved, _ = await loop.resolve_operations(operations)
    assert resolved.errors == []
    assert resolved.delete_file_contents == [source]
    target_uri = "viking://user/u1/memories/entities/alice.md"
    assert resolved.delete_replacements == {source.uri: target_uri}

    store = {source.uri: MemoryFileUtils.write(source)}
    actions = []

    async def read_file(uri, **kwargs):
        if uri not in store:
            raise NotFoundError(uri)
        return store[uri]

    async def write_file(uri, content, **kwargs):
        store[uri] = content
        actions.append(("write", uri))

    async def rm(uri, **kwargs):
        assert target_uri in store  # Replacement is persisted before the source is removed.
        del store[uri]
        actions.append(("delete", uri))

    fs = SimpleNamespace(
        read_file=AsyncMock(side_effect=read_file),
        write_file=AsyncMock(side_effect=write_file),
        rm=AsyncMock(side_effect=rm),
    )
    updater = MemoryUpdater(registry=registry)
    updater._get_viking_fs = Mock(return_value=fs)
    updater._sync_resource_refs_for_result = AsyncMock()
    updater._vectorize_memories = AsyncMock()
    updater.generate_overview = AsyncMock()
    result = await updater.apply_operations(resolved, _ctx(), extract_context, handler)
    assert result.errors == []
    assert result.deleted_uris == [source.uri]
    assert target_uri in store and source.uri not in store
    assert actions.index(("write", target_uri)) < actions.index(("delete", source.uri))


@pytest.mark.parametrize("output_format", ["python", "json"])
@pytest.mark.parametrize("mode", ["add_only", "upsert"])
def test_rejected_creation_does_not_leave_a_delete(output_format, mode):
    dependent = _preference_schema(operation_mode=mode)
    dependent.content_template = "{{ extract_context.get_event_content(ranges, content) }}"
    context = _compile_context(["events"], files=[_source("events")], extra_schemas=[dependent])
    protocol = create_extraction_output_protocol(output_format)
    _bind(protocol, context)
    program = (
        "events_1.delete()\n"
        'bad = sdk.create_preferences(topic="bad", content="lost evidence", score=0)\n'
        "sdk.commit()"
        if output_format == "python"
        else json.dumps(
            {
                "preferences": [
                    {"page_id": 100, "topic": "bad", "content": "lost evidence", "score": 0}
                ],
                "delete_ids": [{"delete_page_id": 1, "replacement_page_id": 100}],
            }
        )
    )
    operations, error = protocol.parse(program, context)
    assert operations is None  # Not a partial candidate with a surviving source deletion.
    assert "Cannot create preferences without original messages" in error


@pytest.mark.parametrize("output_format", ["python", "json"])
def test_compile_can_create_message_independent_add_only(output_format):
    schema = _preference_schema(operation_mode="add_only")
    schema.filename_template = "{{ topic }}{{ extract_context.get_source_archive_suffix() }}.md"
    context = _compile_context([], extra_schemas=[schema])
    protocol = create_extraction_output_protocol(output_format)
    program = (
        'new = sdk.create_preferences(topic="editor", content="Vim", score=0)\nsdk.commit()'
        if output_format == "python"
        else json.dumps(
            {"preferences": [{"page_id": 100, "topic": "editor", "content": "Vim", "score": 0}]}
        )
    )
    operations, error = protocol.parse(program, context)
    assert error is None
    assert operations.preferences[0].topic == "editor"


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["python", "json"])
@pytest.mark.parametrize("name", ["events", "trajectories"])
async def test_compile_without_instruction_keeps_delete_through_real_loop(
    monkeypatch,
    language_config,
    output_format,
    name,
):
    language_config.output_language_override = "en"
    language_config.memory.extraction_output_format = output_format
    source = _source(name)
    program = (
        f"{name}_1.delete()\nsdk.commit()"
        if output_format == "python"
        else json.dumps({"delete_ids": [{"delete_page_id": 1, "replacement_page_id": None}]})
    )
    vlm = SimpleNamespace(model="test", get_completion_async=AsyncMock(return_value=program))
    language_config.vlm = SimpleNamespace(get_vlm_instance=lambda: vlm)

    async def prefetch(provider):
        provider.read_file_contents[source.uri] = source
        provider.get_extract_context().page_id_map.get_page_id(source.uri)
        return []

    monkeypatch.setattr(ConsolidationExtractContextProvider, "prefetch", prefetch)
    fs = SimpleNamespace()
    apply = AsyncMock(return_value=MemoryUpdateResult())
    monkeypatch.setattr("openviking.service.memory_compile.MemoryUpdater.apply_operations", apply)
    monkeypatch.setattr(
        "openviking.service.memory_compile.acquire_memory_operation_lease",
        AsyncMock(return_value=None),
    )
    runner = MemoryCompileRunner(SimpleNamespace(_ensure_initialized=lambda: fs))
    result = await runner._consolidate(
        target=f"viking://user/u1/memories/{name}",
        memory_type=name,
        peer_id=None,
        instruction=None,
        ctx=_ctx(),
    )
    assert result["errors"] == []
    apply.assert_awaited_once()
    assert apply.await_args.args[0].delete_file_contents == [source]
    vlm.get_completion_async.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("output_format", ["python", "json"])
@pytest.mark.parametrize("rename", [False, True])
async def test_existing_message_dependent_upsert_updates_but_cannot_create_new_uri(
    output_format,
    rename,
    language_config,
):
    schema = _preference_schema()
    schema.fields[0].merge_op = MergeOp.REPLACE
    schema.content_template = "{{ extract_context.get_event_content(ranges, content) }}"
    source = MemoryFile(
        uri="viking://user/u1/memories/preferences/editor.md",
        memory_type="preferences",
        content="Vim",
        extra_fields={"topic": "editor", "score": 0},
    )
    context = _compile_context([], files=[source], extra_schemas=[schema])
    protocol = create_extraction_output_protocol(output_format)
    _bind(protocol, context)
    topic = "new-editor" if rename else "editor"
    program = (
        f'preferences_1.update(topic="{topic}", content="Neovim")\nsdk.commit()'
        if output_format == "python"
        else json.dumps({"preferences": [{"page_id": 1, "topic": topic, "content": "Neovim"}]})
    )
    operations, error = protocol.parse(program, context)
    assert error is None
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(schema)
    provider = ConsolidationExtractContextProvider(
        memory_types=["preferences"], memory_registry=registry, ctx=_ctx(), output_language="en"
    )
    extract_context = provider.get_extract_context()
    extract_context.page_id_map = context.page_id_map
    provider.read_file_contents.update(context.read_file_contents)
    handler = build_consolidation_isolation_handler(
        _ctx(), extract_context, memory_types=["preferences"], peer_id=None
    )
    handler.prepare_messages()
    provider.bind_isolation_handler(handler)
    loop = ExtractLoop(
        vlm=SimpleNamespace(model="test"),
        viking_fs=SimpleNamespace(),
        ctx=_ctx(),
        context_provider=provider,
        isolation_handler=handler,
    )
    loop._extract_context = extract_context
    if rename:
        with pytest.raises(ValueError, match="Cannot create preferences without original messages"):
            await loop.resolve_operations(operations)
    else:
        resolved, _ = await loop.resolve_operations(operations)
        assert resolved.upsert_operations[0].uris == [source.uri]
        assert resolved.upsert_operations[0].memory_fields["content"] == "Neovim"


@pytest.mark.parametrize("output_format", ["python", "json"])
def test_session_extraction_keeps_message_dependent_creation(output_format):
    schema = _preference_schema(operation_mode="add_only")
    schema.content_template = "{{ extract_context.get_event_content(ranges, content) }}"
    context = _context([schema])  # Ordinary session extraction, not consolidation.
    protocol = create_extraction_output_protocol(output_format)
    program = (
        'new = sdk.create_preferences(topic="editor", content="Vim", score=0)\nsdk.commit()'
        if output_format == "python"
        else json.dumps(
            {"preferences": [{"page_id": 100, "topic": "editor", "content": "Vim", "score": 0}]}
        )
    )
    operations, error = protocol.parse(program, context)
    assert error is None
    assert operations.preferences[0].topic == "editor"
