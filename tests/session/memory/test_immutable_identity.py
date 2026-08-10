# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for immutable memory identity routing."""

from unittest.mock import MagicMock

import pytest

from openviking.session.memory.dataclass import MemoryField, MemoryFile, MemoryTypeSchema
from openviking.session.memory.extract_loop import ExtractLoop, _schema_identity_fields
from openviking.session.memory.memory_isolation_handler import RoleScope
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.merge_op import FieldType, MergeOp


_OLD_URI = "viking://user/default/memories/preferences/workflow.md"


def _preference_schema() -> MemoryTypeSchema:
    return MemoryTypeSchema(
        memory_type="preferences",
        description="User preferences",
        directory="viking://user/{{ user_space }}/memories/preferences",
        filename_template="{{ topic }}.md",
        fields=[
            MemoryField(
                name="topic",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.REPLACE,
            ),
        ],
    )


def test_uri_template_filters_still_mark_schema_fields_as_identity():
    schema = MemoryTypeSchema(
        memory_type="custom",
        directory="viking://user/{{ user_space }}/memories/custom/{{ tenant | lower }}",
        filename_template="{{ slug(name) }}.md",
        fields=[
            MemoryField(
                name="tenant",
                field_type=FieldType.STRING,
                merge_op=MergeOp.REPLACE,
            ),
            MemoryField(
                name="name",
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

    assert _schema_identity_fields(schema) == {"tenant", "name"}


def _make_loop(
    responses: list[str],
    *,
    old_uri: str = _OLD_URI,
    old_file: MemoryFile | None = None,
    read_files: dict[str, MemoryFile] | None = None,
) -> tuple[ExtractLoop, object]:
    schema = _preference_schema()
    old_file = old_file or MemoryFile(
        uri=old_uri,
        content="Prefers concise status updates.",
        extra_fields={"topic": "workflow", "memory_type": "preferences"},
    )

    class DummyProvider:
        def __init__(self):
            self.extract_context = ExtractContext([])
            self.read_file_contents = dict(read_files or {old_uri: old_file})

        def get_memory_schemas(self, _ctx):
            return [schema]

        def get_output_language(self):
            return "English"

        def get_tools(self):
            return []

        def instruction(self):
            return "Extract preference memories."

        async def prefetch(self):
            return []

        def get_extract_context(self):
            return self.extract_context

    class DummyIsolationHandler:
        def get_read_scope(self):
            return RoleScope(user_ids=["default"])

        def fill_identity_fields(self, item_dict, role_scope):
            del role_scope

        def calculate_memory_uris(self, memory_type_schema, operation, extract_context):
            del memory_type_schema, extract_context
            return [
                "viking://user/default/memories/preferences/"
                f"{operation.memory_fields['topic']}.md"
            ]

    class DummyVLM:
        model = "dummy"

        def __init__(self):
            self.responses = list(responses)
            self.messages = []

        async def get_completion_async(
            self, messages, tools=None, tool_choice=None, thinking=False
        ):
            del tools, tool_choice, thinking
            self.messages.append(list(messages))
            return self.responses.pop(0)

    vlm = DummyVLM()
    loop = ExtractLoop(
        vlm=vlm,
        viking_fs=MagicMock(),
        max_iterations=1,
        context_provider=DummyProvider(),
        isolation_handler=DummyIsolationHandler(),
    )
    return loop, vlm


@pytest.mark.asyncio
async def test_immutable_topic_mismatch_gets_one_safe_repair_round():
    loop, vlm = _make_loop(
        [
            '{"preferences":[{"page_id":1,"topic":"private_food_topic",'
            '"content":"Prefers noodles."}],"delete_ids":[]}',
            '{"preferences":[{"page_id":100,"topic":"private_food_topic",'
            '"content":"Prefers noodles."}],"delete_ids":[]}',
        ]
    )

    operations, _ = await loop.run()

    assert len(vlm.messages) == 2
    repair_prompt = "\n".join(message["content"] for message in vlm.messages[1])
    assert "immutable identity field" in repair_prompt
    assert "page_id >= 100" in repair_prompt
    # Conflict diagnostics describe the schema boundary without echoing either
    # the stored or newly proposed identity value.
    assert "private_food_topic" not in repair_prompt
    assert "workflow" not in repair_prompt
    assert operations.errors == []
    assert operations.upsert_operations[0].page_id == 100
    assert operations.upsert_operations[0].uris == [
        "viking://user/default/memories/preferences/private_food_topic.md"
    ]


@pytest.mark.asyncio
async def test_repeated_immutable_topic_mismatch_fails_closed():
    mismatch = (
        '{"preferences":[{"page_id":1,"topic":"another_topic",'
        '"content":"Must not overwrite workflow."}],"delete_ids":[]}'
    )
    loop, vlm = _make_loop([mismatch, mismatch])

    operations, _ = await loop.run()

    assert len(vlm.messages) == 2
    assert operations.has_errors()
    assert operations.errors[0].startswith("Immutable identity mismatch:")
    assert "another_topic" not in operations.errors[0]
    assert "workflow" not in operations.errors[0]
    # Resolution pins the stored identity as a second line of defense; the
    # batch-level error prevents the mutable content from being applied.
    assert operations.upsert_operations[0].memory_fields["topic"] == "workflow"


@pytest.mark.asyncio
async def test_replace_field_on_matching_identity_is_not_treated_as_immutable():
    loop, vlm = _make_loop(
        [
            '{"preferences":[{"page_id":1,"topic":"workflow",'
            '"content":"Prefers detailed status updates."}],"delete_ids":[]}'
        ]
    )

    operations, _ = await loop.run()

    assert len(vlm.messages) == 1
    assert operations.errors == []
    assert (
        operations.upsert_operations[0].memory_fields["content"]
        == "Prefers detailed status updates."
    )


@pytest.mark.asyncio
async def test_page_id_bound_to_another_memory_type_fails_closed():
    entity_uri = "viking://user/default/memories/entities/person/alice.md"
    entity_file = MemoryFile(
        uri=entity_uri,
        content="Alice profile",
        memory_type="entities",
        extra_fields={"name": "alice"},
    )
    mismatch = (
        '{"preferences":[{"page_id":1,"topic":"workflow",'
        '"content":"Must not overwrite an entity."}],"delete_ids":[]}'
    )
    loop, _ = _make_loop(
        [mismatch, mismatch],
        old_uri=entity_uri,
        old_file=entity_file,
    )

    operations, _ = await loop.run()

    assert operations.has_errors()
    assert operations.errors[0].startswith("Invalid page_id:")
    assert "memory_type=entities, not preferences" in operations.errors[0]
    assert entity_uri not in operations.errors[0]
    assert operations.upsert_operations[0].uris == []


@pytest.mark.asyncio
async def test_page_id_uri_type_mismatch_cannot_be_hidden_by_stored_type():
    entity_uri = "viking://user/default/memories/entities/person/alice.md"
    inconsistent_file = MemoryFile(
        uri=entity_uri,
        content="Alice profile",
        memory_type="preferences",
        extra_fields={"memory_type": "preferences", "topic": "workflow"},
    )
    mismatch = (
        '{"preferences":[{"page_id":1,"topic":"workflow",'
        '"content":"Must not overwrite an entity path."}],"delete_ids":[]}'
    )
    loop, _ = _make_loop(
        [mismatch, mismatch],
        old_uri=entity_uri,
        old_file=inconsistent_file,
    )

    operations, _ = await loop.run()

    assert operations.has_errors()
    assert "memory_type=entities, not preferences" in operations.errors[0]
    assert operations.upsert_operations[0].uris == []


@pytest.mark.asyncio
async def test_page_id_bound_to_resource_cannot_be_used_for_memory_upsert():
    resource_uri = "viking://user/default/resources/private-notes.md"
    resource_file = MemoryFile(uri=resource_uri, content="Private resource body")
    invalid = (
        '{"preferences":[{"page_id":1,"topic":"workflow",'
        '"content":"Must not overwrite a resource."}],"delete_ids":[]}'
    )
    loop, _ = _make_loop(
        [invalid, invalid],
        old_uri=resource_uri,
        old_file=resource_file,
    )

    operations, _ = await loop.run()

    assert operations.has_errors()
    assert operations.errors[0].startswith("Invalid page_id:")
    assert "not bound to a canonical memory item" in operations.errors[0]
    assert resource_uri not in operations.errors[0]
    assert operations.upsert_operations[0].uris == []


@pytest.mark.asyncio
async def test_resource_page_id_cannot_be_deleted_by_memory_operations():
    resource_uri = "viking://user/default/resources/private-notes.md"
    resource_file = MemoryFile(uri=resource_uri, content="Private resource body")
    invalid = (
        '{"preferences":[],"delete_ids":['
        '{"delete_page_id":1,"replacement_page_id":null}]}'
    )
    loop, _ = _make_loop(
        [invalid, invalid],
        old_uri=resource_uri,
        old_file=resource_file,
    )

    operations, _ = await loop.run()

    assert operations.has_errors()
    assert operations.errors[0].startswith("Invalid page_id:")
    assert "not bound to a canonical memory item" in operations.errors[0]
    assert resource_uri not in operations.errors[0]
    assert operations.delete_file_contents == []


@pytest.mark.asyncio
async def test_delete_page_id_must_belong_to_effective_memory_schemas():
    entity_uri = "viking://user/default/memories/entities/person/alice.md"
    entity_file = MemoryFile(
        uri=entity_uri,
        content="Alice profile",
        memory_type="entities",
        extra_fields={"name": "alice", "memory_type": "entities"},
    )
    invalid = (
        '{"preferences":[],"delete_ids":['
        '{"delete_page_id":1,"replacement_page_id":null}]}'
    )
    loop, _ = _make_loop(
        [invalid, invalid],
        old_uri=entity_uri,
        old_file=entity_file,
    )

    operations, _ = await loop.run()

    assert operations.has_errors()
    assert operations.errors[0].startswith("Invalid page_id:")
    assert "memory_type=entities" in operations.errors[0]
    assert "not writable in this extraction" in operations.errors[0]
    assert entity_uri not in operations.errors[0]
    assert operations.delete_file_contents == []


@pytest.mark.asyncio
async def test_unknown_delete_page_id_gets_one_repair_round():
    loop, vlm = _make_loop(
        [
            '{"preferences":[],"delete_ids":['
            '{"delete_page_id":42,"replacement_page_id":null}]}',
            '{"preferences":[],"delete_ids":[]}',
        ]
    )

    operations, _ = await loop.run()

    assert len(vlm.messages) == 2
    assert operations.errors == []
    assert operations.delete_file_contents == []


@pytest.mark.asyncio
async def test_resource_page_id_cannot_be_a_delete_replacement():
    resource_uri = "viking://user/default/resources/private-notes.md"
    preference_file = MemoryFile(
        uri=_OLD_URI,
        content="Prefers concise status updates.",
        memory_type="preferences",
        extra_fields={"topic": "workflow", "memory_type": "preferences"},
    )
    resource_file = MemoryFile(uri=resource_uri, content="Private resource body")
    invalid = (
        '{"preferences":[],"delete_ids":['
        '{"delete_page_id":1,"replacement_page_id":2}]}'
    )
    loop, _ = _make_loop(
        [invalid, invalid],
        read_files={_OLD_URI: preference_file, resource_uri: resource_file},
    )

    operations, _ = await loop.run()

    assert operations.has_errors()
    assert operations.errors[0].startswith("Invalid page_id:")
    assert "not bound to a canonical memory item" in operations.errors[0]
    assert resource_uri not in operations.errors[0]
    assert operations.delete_replacements == {}


@pytest.mark.asyncio
async def test_unknown_low_page_id_repairs_to_new_page_id():
    loop, vlm = _make_loop(
        [
            '{"preferences":[{"page_id":42,"topic":"food",'
            '"content":"Prefers noodles."}],"delete_ids":[]}',
            '{"preferences":[{"page_id":100,"topic":"food",'
            '"content":"Prefers noodles."}],"delete_ids":[]}',
        ]
    )

    operations, _ = await loop.run()

    assert len(vlm.messages) == 2
    assert operations.errors == []
    assert operations.upsert_operations[0].page_id == 100
    assert operations.upsert_operations[0].uris == [
        "viking://user/default/memories/preferences/food.md"
    ]


@pytest.mark.asyncio
async def test_duplicate_new_page_ids_get_one_repair_round():
    loop, vlm = _make_loop(
        [
            '{"preferences":['
            '{"page_id":100,"topic":"food","content":"Prefers noodles."},'
            '{"page_id":100,"topic":"travel","content":"Prefers trains."}'
            '],"delete_ids":[]}',
            '{"preferences":['
            '{"page_id":100,"topic":"food","content":"Prefers noodles."},'
            '{"page_id":101,"topic":"travel","content":"Prefers trains."}'
            '],"delete_ids":[]}',
        ]
    )

    operations, _ = await loop.run()

    assert len(vlm.messages) == 2
    assert operations.errors == []
    assert [operation.page_id for operation in operations.upsert_operations] == [100, 101]
    assert [operation.uris[0] for operation in operations.upsert_operations] == [
        "viking://user/default/memories/preferences/food.md",
        "viking://user/default/memories/preferences/travel.md",
    ]
