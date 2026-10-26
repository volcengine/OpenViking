# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from test_extraction_output_protocol import _bind, _context

from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.extract_loop import ExtractLoop
from openviking.session.memory.extraction_output_protocol import create_extraction_output_protocol
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import MemoryUpdater
from openviking.session.memory.merge_op import (
    FieldType,
    MergeOp,
    MergeOpFactory,
    PatchParseError,
    ReplaceOp,
    SearchReplaceBlock,
    StrPatch,
)
from openviking.session.memory.schema_model_generator import SchemaModelGenerator
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils

URI = "viking://user/alice/memories/notes/example.md"
OLD = "before\nold line\nafter"


def schema():
    return MemoryTypeSchema(
        memory_type="notes",
        directory="viking://user/{{ user_space }}/memories/notes",
        filename_template="{{ key }}.md",
        fields=[
            MemoryField(name="key", field_type=FieldType.STRING, merge_op=MergeOp.IMMUTABLE),
            MemoryField(name="content", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
            MemoryField(name="title", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
            MemoryField(name="count", field_type=FieldType.INT64, merge_op=MergeOp.REPLACE),
        ],
    )


def bound():
    memory = MemoryFile(
        uri=URI,
        memory_type="notes",
        content=OLD,
        extra_fields={"key": "example", "title": "old title", "count": 1},
    )
    context = _context([schema()], files=[memory])
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)
    return protocol, context, memory


def resolved(parsed, memory):
    return ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                memory_type="notes",
                uris=[URI],
                page_id=1,
                old_memory_file_content=memory,
                memory_fields={k: v for k, v in dict(parsed.notes[0]).items() if k != "page_id"},
            )
        ],
        delete_file_contents=[],
        errors=[],
    )


def validator(memory):
    loop = object.__new__(ExtractLoop)
    loop.context_provider = SimpleNamespace(read_file_contents={URI: memory})
    return loop


def test_replace_schema_and_prompt_still_request_full_strings():
    flat = SchemaModelGenerator([schema()]).create_flat_data_model(schema())
    advertised = flat.model_json_schema()
    assert advertised["properties"]["content"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert "StrPatch" not in json.dumps(advertised)
    assert "blocks" not in json.dumps(advertised)
    protocol, context, _ = bound()
    contract = protocol.render_contract(context)
    assert "obj.content.update(" in contract
    assert "obj.content.edit(" not in contract and "obj.content.drop(" not in contract
    assert "For fields marked [replace], use obj.field.update(" in protocol.render_patch_repair([])
    parsed, error = protocol.parse('notes_1.content.update("complete new value")', context)
    assert error is None and parsed.notes[0].content == "complete new value"


@pytest.mark.asyncio
@pytest.mark.parametrize("dictionary", [False, True])
async def test_replace_materializes_edits_without_dropping_unchanged_text(dictionary):
    patch = StrPatch(blocks=[SearchReplaceBlock(search="old line", replace="new line")])
    value = patch.model_dump() if dictionary else patch
    assert await ReplaceOp().apply(OLD, value) == "before\nnew line\nafter"


@pytest.mark.asyncio
async def test_replace_supports_chained_edits_and_deletes():
    protocol, context, memory = bound()
    parsed, error = protocol.parse(
        'notes_1.content.edit(search="old line", replace="new line").drop(text="before")', context
    )
    assert error is None
    operations = resolved(parsed, memory)
    assert await validator(memory)._validate_patch_operations(operations) == []
    result = await ReplaceOp().apply(OLD, operations.upsert_operations[0].memory_fields["content"])
    assert result == "new line\nafter"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,search", [("content", "does not exist"), ("title", "does not exist")]
)
async def test_bad_search_reaches_patch_repair_not_dsl_parse_error(field, search):
    protocol, context, memory = bound()
    parsed, error = protocol.parse(
        f'notes_1.{field}.edit(search={search!r}, replace="new")', context
    )
    assert error is None
    errors = await validator(memory)._validate_patch_operations(resolved(parsed, memory))
    assert len(errors) == 1
    assert errors[0]["field"] == field and errors[0]["reason"] == "not_found"


@pytest.mark.asyncio
async def test_metadata_field_validates_against_its_own_value():
    protocol, context, memory = bound()
    parsed, error = protocol.parse(
        'notes_1.title.edit(search="old title", replace="new title")', context
    )
    assert error is None
    assert await validator(memory)._validate_patch_operations(resolved(parsed, memory)) == []
    assert await ReplaceOp().apply("old title", parsed.notes[0].title) == "new title"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["content", "title"])
@pytest.mark.parametrize("protocol_name", ["python", "json"])
async def test_runtime_uses_latest_stored_content_not_the_compiler_snapshot(field, protocol_name):
    protocol, context, memory = bound()
    search = "old line" if field == "content" else "old title"
    replacement = "new line" if field == "content" else "new title"
    if protocol_name == "python":
        code = f"notes_1.{field}.edit(search={search!r}, replace={replacement!r})"
    else:
        protocol = create_extraction_output_protocol("json")
        code = json.dumps(
            {
                "notes": [
                    {
                        "page_id": 1,
                        "key": "example",
                        field: {"blocks": [{"search": search, "replace": replacement}]},
                    }
                ]
            }
        )
    parsed, error = protocol.parse(code, context)
    assert error is None
    loop = validator(memory)
    loop.ctx = None
    loop._extract_context = context
    loop.context_provider.get_memory_schemas = lambda ctx: [schema()]
    loop._isolation_handler = SimpleNamespace(
        get_read_scope=lambda: context.role_scope,
        fill_identity_fields=lambda *args, **kwargs: None,
    )
    operations, links = await loop.resolve_operations(parsed)
    assert not operations.errors and not links
    assert await loop._validate_patch_operations(operations) == []
    latest = memory.model_copy(
        update={
            "content": OLD + "\nconcurrent addition",
            "extra_fields": {**memory.extra_fields, "title": "old title + concurrent addition"},
        }
    )
    stored = {URI: MemoryFileUtils.write(latest)}

    async def write(uri, content, **kwargs):
        stored[uri] = content

    fs = SimpleNamespace(
        read_file=AsyncMock(side_effect=lambda uri, **kw: stored[uri]), write_file=write
    )
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(schema())
    updater = MemoryUpdater(registry=registry)
    updater._viking_fs = fs
    await updater._apply_upsert(operations.upsert_operations[0], None)
    result = MemoryFileUtils.read(stored[URI], uri=URI)
    expected_content = OLD.replace(search, replacement) if field == "content" else OLD
    expected_title = "new title" if field == "title" else "old title"
    assert result.content == expected_content + "\nconcurrent addition"
    assert result.extra_fields["title"] == expected_title + " + concurrent addition"
    assert "blocks" not in stored[URI]


@pytest.mark.asyncio
async def test_replace_edit_without_old_match_does_not_invent_content():
    with pytest.raises(PatchParseError):
        await ReplaceOp().apply(
            None, StrPatch(blocks=[SearchReplaceBlock(search="old", replace="new")])
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "", "new"])
async def test_full_replacement_semantics_are_unchanged(value):
    assert await ReplaceOp().apply("old", value) == ("old" if value in (None, "") else value)


@pytest.mark.parametrize(
    "field_type,value", [(FieldType.INT64, 2), (FieldType.FLOAT32, 1.5), (FieldType.BOOL, False)]
)
@pytest.mark.asyncio
async def test_non_string_replace_keeps_scalar_schema_and_behavior(field_type, value):
    op = MergeOpFactory.create(MergeOp.REPLACE, field_type)
    assert op.get_output_schema_type(field_type) is type(value)
    assert await op.apply(1, value) == value


def test_new_object_and_non_string_fields_still_reject_edits():
    protocol, context, _ = bound()
    for code in (
        'fresh = sdk.create_notes(key="fresh", content="old")\nfresh.content.edit(search="old", replace="new")',
        'notes_1.count.edit(search="1", replace="2")',
    ):
        parsed, error = protocol.parse(code, context)
        assert parsed is None and error is not None


def test_immutable_identity_remains_unchanged():
    protocol, context, _ = bound()
    parsed, error = protocol.parse('notes_1.key.edit(search="example", replace="other")', context)
    assert error is None and parsed.notes == []


@pytest.mark.asyncio
async def test_ambiguous_search_is_repairable():
    protocol, context, memory = bound()
    memory.content = "old line\nold line"
    parsed, error = protocol.parse(
        'notes_1.content.edit(search="old line", replace="new line")', context
    )
    assert error is None
    errors = await validator(memory)._validate_patch_operations(resolved(parsed, memory))
    assert len(errors) == 1
    assert errors[0]["reason"] == "non_unique"
    repaired, error = protocol.parse('notes_1.content.update("new line\\nold line")', context)
    assert error is None
    assert await validator(memory)._validate_patch_operations(resolved(repaired, memory)) == []


@pytest.mark.asyncio
async def test_replace_rejects_malformed_blocks_instead_of_persisting_their_repr():
    with pytest.raises(ValueError):
        await ReplaceOp().apply(OLD, {"blocks": [{"search": "old line"}]})


@pytest.mark.asyncio
async def test_replace_identity_edit_resolves_to_the_edited_uri():
    _, _, memory = bound()
    renamed_schema = schema()
    renamed_schema.fields[0].merge_op = MergeOp.REPLACE
    context = _context([renamed_schema], files=[memory])
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)
    parsed, error = protocol.parse('notes_1.key.edit(search="example", replace="renamed")', context)
    assert error is None
    loop = validator(memory)
    loop._extract_context = None
    operations = resolved(parsed, memory)
    assert await loop._validate_patch_operations(operations) == []
    assert await loop._updated_uri_for_existing_operation(
        operations.upsert_operations[0], schema=renamed_schema, source_uri=URI
    ) == URI.replace("example.md", "renamed.md")


@pytest.mark.asyncio
@pytest.mark.parametrize("other_title", ["missing title", "unrelated", None, 42])
async def test_metadata_patch_diagnostic_searches_the_same_field(other_title):
    protocol, context, memory = bound()
    parsed, error = protocol.parse(
        'notes_1.title.edit(search="missing title", replace="new title")', context
    )
    assert error is None
    loop = validator(memory)
    other_uri = URI.replace("example.md", "other.md")
    loop.context_provider.read_file_contents[other_uri] = MemoryFile(
        uri=other_uri,
        content="missing title",
        extra_fields={"title": other_title},
    )
    errors = await loop._validate_patch_operations(resolved(parsed, memory))
    assert len(errors) == 1
    expected = [other_uri] if other_title == "missing title" else []
    assert errors[0]["found_in_other_uris"] == expected


@pytest.mark.parametrize("field", ["content", "title"])
@pytest.mark.parametrize("full_method", ["field", "object"])
@pytest.mark.parametrize("patch_method", ["edit", "drop", "field_value", "object_value"])
@pytest.mark.parametrize("patch_first", [False, True])
def test_replace_rejects_mixed_full_and_partial_updates(
    field, full_method, patch_method, patch_first
):
    protocol, context, memory = bound()
    full_value = "before\nold line\nNEW EXTRA\nafter"
    if full_method == "field":
        full = f"notes_1.{field}.update({full_value!r})"
    else:
        full = f"notes_1.update({field}={full_value!r})"
    patch_value = {"blocks": [{"search": "old line", "replace": "new line"}]}
    partial = {
        "edit": f'notes_1.{field}.edit(search="old line", replace="new line")',
        "drop": f'notes_1.{field}.drop(text="old line")',
        "field_value": f"notes_1.{field}.update({patch_value!r})",
        "object_value": f"notes_1.update({field}={patch_value!r})",
    }[patch_method]
    statements = [partial, full] if patch_first else [full, partial]

    parsed, error = protocol.parse("\n".join(statements), context)

    assert parsed is None
    assert error is not None and "cannot mix" in error
    assert memory.content == OLD


def test_replace_full_updates_and_edits_on_other_fields_are_independent():
    protocol, context, _ = bound()
    parsed, error = protocol.parse(
        'notes_1.content.update("first value")\n'
        'notes_1.update(content="complete final value")\n'
        'notes_1.title.edit(search="old title", replace="new title")',
        context,
    )
    assert error is None
    assert parsed.notes[0].content == "complete final value"
    assert isinstance(parsed.notes[0].title, StrPatch)


@pytest.mark.asyncio
async def test_replace_accumulates_edits_across_statements():
    protocol, context, memory = bound()
    parsed, error = protocol.parse(
        'notes_1.content.edit(search="old line", replace="new line")\n'
        'notes_1.content.drop(text="before")',
        context,
    )
    assert error is None
    operations = resolved(parsed, memory)
    assert await validator(memory)._validate_patch_operations(operations) == []
    assert await ReplaceOp().apply(OLD, parsed.notes[0].content) == "new line\nafter"


def new_memory_updater():
    fs = SimpleNamespace(read_file=AsyncMock(side_effect=FileNotFoundError), write_file=AsyncMock())
    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(schema())
    updater = MemoryUpdater(registry=registry)
    updater._viking_fs = fs
    return updater, fs


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol_name", ["python", "json"])
@pytest.mark.parametrize("field", ["content", "title"])
@pytest.mark.parametrize("block", [{"search": "old", "replace": "new"}, {"delete": "old"}])
async def test_new_replace_patch_is_rejected_before_any_write(protocol_name, field, block):
    _, context, memory = bound()
    protocol = create_extraction_output_protocol(protocol_name)
    fields = {"key": "fresh", "title": "full title", "content": "full body", "count": 0}
    fields[field] = {"blocks": [block]}
    if protocol_name == "python":
        code = "sdk.create_notes(" + ", ".join(f"{k}={v!r}" for k, v in fields.items()) + ")"
    else:
        code = json.dumps({"notes": [{"page_id": 100, **fields}]})
    parsed, error = protocol.parse(code, context)
    updater, fs = new_memory_updater()
    if parsed is not None:
        for item in parsed.notes:
            operation = ResolvedOperation(
                memory_type="notes",
                uris=[URI.replace("example.md", "fresh.md")],
                page_id=item.page_id,
                old_memory_file_content=None,
                memory_fields=dict(item.model_dump(exclude={"page_id"})),
            )
            operations = ResolvedOperations(
                upsert_operations=[operation], delete_file_contents=[], errors=[]
            )
            assert await validator(memory)._validate_patch_operations(operations) == []
            await updater._apply_upsert(operation, None)

    fs.write_file.assert_not_awaited()
    # Both protocols reject the item at the shared schema boundary. JSON keeps
    # its existing tolerant-parser policy of filtering invalid list items.
    with pytest.raises(ValidationError, match="complete string"):
        context.operations_model.model_validate({"notes": [{"page_id": 100, **fields}]})
    if protocol_name == "python":
        assert parsed is None
        assert error is not None and "complete string" in error
    else:
        assert error is None
        assert parsed.notes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol_name", ["python", "json"])
async def test_new_replace_full_strings_are_parsed_and_persisted(protocol_name):
    _, context, _ = bound()
    protocol = create_extraction_output_protocol(protocol_name)
    fields = {"key": "fresh", "title": "full title", "content": "full body", "count": 0}
    if protocol_name == "python":
        code = "sdk.create_notes(" + ", ".join(f"{k}={v!r}" for k, v in fields.items()) + ")"
    else:
        code = json.dumps({"notes": [{"page_id": 100, **fields}]})
    parsed, error = protocol.parse(code, context)
    assert error is None
    item = parsed.notes[0]
    uri = URI.replace("example.md", "fresh.md")
    operation = ResolvedOperation(
        memory_type="notes",
        uris=[uri],
        page_id=item.page_id,
        old_memory_file_content=None,
        memory_fields=dict(item.model_dump(exclude={"page_id"})),
    )
    updater, fs = new_memory_updater()
    await updater._apply_upsert(operation, None)
    fs.write_file.assert_awaited_once()
    written = MemoryFileUtils.read(fs.write_file.await_args.args[1], uri=uri)
    assert written.content == "full body"
    assert written.extra_fields["title"] == "full title"
    assert written.extra_fields["count"] == 0


def test_json_filters_new_replace_patch_without_dropping_valid_items():
    _, context, _ = bound()
    protocol = create_extraction_output_protocol("json")
    invalid = {
        "page_id": 100,
        "key": "bad",
        "content": {"blocks": [{"search": "old", "replace": "new"}]},
    }
    valid = {"page_id": 101, "key": "good", "content": "complete content"}
    parsed, error = protocol.parse(json.dumps({"notes": [invalid, valid]}), context)
    assert error is None
    assert len(parsed.notes) == 1
    assert parsed.notes[0].key == "good"
    assert parsed.notes[0].content == "complete content"
