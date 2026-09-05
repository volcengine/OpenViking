# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for JSON and restricted-Python memory extraction output protocols."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from openviking.session.memory.dataclass import MemoryField, MemoryFile, MemoryTypeSchema
from openviking.session.memory.extraction_output_protocol import (
    ExtractionOutputContext,
    create_extraction_output_protocol,
)
from openviking.session.memory.memory_isolation_handler import RoleScope
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.page_id_map import PageIdMap
from openviking.session.memory.schema_model_generator import SchemaModelGenerator


def _preference_schema(*, operation_mode: str = "upsert") -> MemoryTypeSchema:
    return MemoryTypeSchema(
        memory_type="preferences",
        description="User preferences",
        directory="viking://user/{{ user_space }}/memories/preferences",
        filename_template="{{ topic }}.md",
        operation_mode=operation_mode,
        fields=[
            MemoryField(
                name="topic",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
            MemoryField(
                name="score",
                field_type=FieldType.INT64,
                merge_op=MergeOp.SUM,
            ),
        ],
    )


def _project_schema() -> MemoryTypeSchema:
    return MemoryTypeSchema(
        memory_type="projects",
        description="Projects",
        directory="viking://user/{{ user_space }}/memories/projects",
        filename_template="{{ name }}.md",
        fields=[
            MemoryField(
                name="name",
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


def _profile_schema() -> MemoryTypeSchema:
    return MemoryTypeSchema(
        memory_type="profile",
        description="User profile",
        directory="viking://user/{{ user_space }}/memories",
        filename_template="profile.md",
        fields=[
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            )
        ],
    )


def _context(
    schemas: list[MemoryTypeSchema],
    *,
    files: list[MemoryFile] | None = None,
    link_enabled: bool = False,
    role_scope: RoleScope | None = None,
    available_tools: tuple[str, ...] = ("read",),
) -> ExtractionOutputContext:
    config = SimpleNamespace(memory=SimpleNamespace(link_enabled=link_enabled))
    with patch("openviking_cli.utils.config.get_openviking_config", return_value=config):
        operations_model = SchemaModelGenerator(schemas).create_structured_operations_model(
            role_scope
        )
    page_id_map = PageIdMap()
    read_file_contents = {}
    for memory_file in files or []:
        read_file_contents[memory_file.uri] = memory_file
        page_id_map.get_page_id(memory_file.uri)
    return ExtractionOutputContext(
        operations_model=operations_model,
        schemas=tuple(schemas),
        page_id_map=page_id_map,
        read_file_contents=read_file_contents,
        link_enabled=link_enabled,
        role_scope=role_scope,
        available_tools=available_tools,
    )


def _existing_preference(uri: str, topic: str, content: str, score: int = 0) -> MemoryFile:
    return MemoryFile(
        uri=uri,
        memory_type="preferences",
        content=content,
        extra_fields={
            "topic": topic,
            "score": score,
            "version": 7,
            "_uri": uri,
        },
    )


def _bind(protocol, context: ExtractionOutputContext) -> str:
    protocol.render_contract(context)
    return protocol.render_new_bindings(context, source="test read")


def test_json_protocol_preserves_stable_parser_and_empty_contract():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("json")

    operations, error = protocol.parse(
        '{"preferences": [], "delete_ids": [], "ignored": true}', context
    )

    assert error is None
    assert operations.model_dump() == {"preferences": [], "delete_ids": []}
    assert '"preferences": []' in protocol.render_final_instruction(context)
    assert '"delete_ids": []' in protocol.render_final_instruction(context)


def test_python_contract_and_bindings_expose_only_selected_schema_fields():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim", 2)],
    )
    protocol = create_extraction_output_protocol("python")

    contract = protocol.render_contract(context)
    bindings = protocol.render_new_bindings(context, source="search then read")

    assert "sdk.create_preferences" in contract
    assert "sdk.create_projects" not in contract
    assert "preferences_1 = sdk.existing" in bindings
    assert "topic='editor'" in bindings
    assert "content='Use Vim'" in bindings
    assert "version" not in bindings
    assert uri not in bindings
    assert protocol.render_new_bindings(context, source="duplicate read") == ""


def test_python_contract_includes_link_rules_when_enabled():
    context = _context([_preference_schema()], link_enabled=True)
    protocol = create_extraction_output_protocol("python")

    contract = protocol.render_contract(context)

    assert "## Link Rules" in contract
    assert "obj.link(target" in contract
    assert "match_text" in contract
    assert "obj_a.link(" in contract
    assert "assign the create/set call to a variable first" in contract

def test_python_contract_omits_link_rules_when_disabled():
    context = _context([_preference_schema()], link_enabled=False)
    protocol = create_extraction_output_protocol("python")

    contract = protocol.render_contract(context)

    assert "## Link Rules" not in contract
    assert "obj.link(...) is unavailable" in contract


def test_python_protocol_compiles_extracted_code_from_fence():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")
    compiled = []

    def record_compile(self, code):
        compiled.append(code)
        return context.operations_model(preferences=[], delete_ids=[])

    with patch(
        "openviking.session.memory.extraction_output_protocol.python_protocol._PythonProgramCompiler.compile",
        autospec=True,
        side_effect=record_compile,
    ):
        operations, error = protocol.parse("```python\nsdk.commit()\n```", context)

    assert error is None
    assert operations is not None
    # The fenced code (not the raw fence wrapper) is what gets compiled.
    assert compiled == ["sdk.commit()"]


def test_json_protocol_preserves_tool_result_context_shape():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("json")
    result = {"memory_type": "preferences", "content": "1\tUse Vim"}

    messages = protocol.render_tool_result_messages(
        context,
        call_id="call-1",
        tool_name="read",
        params={"uri": "viking://user/alice/memories/preferences/editor.md"},
        result=result,
        source="tool call",
    )

    assert messages == [
        {
            "role": "user",
            "content": json.dumps(
                {
                    "tool_call_name": "read",
                    "args": {"uri": "viking://user/alice/memories/preferences/editor.md"},
                    "result": result,
                },
                ensure_ascii=False,
            ),
        }
    ]


def test_python_protocol_renders_search_result_as_read_tool_comment():
    uri_a = "viking://user/alice/memories/entities/person/tim.md"
    uri_b = "viking://user/alice/memories/entities/person/maria.md"
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    messages = protocol.render_tool_result_messages(
        context,
        call_id="call-1",
        tool_name="search",
        params={"query": "tim maria"},
        result=[{"uri": uri_a, "score": 0.9}, {"uri": uri_b, "score": 0.8}],
        source="tool call",
    )

    assert len(messages) == 1
    content = messages[0]["content"]
    assert content.startswith("# Search")
    assert "query='tim maria'" in content
    assert "read them with the read tool" in content
    assert f"# - {uri_a}" in content
    assert f"# - {uri_b}" in content
    # Search yields no content, so it must not fabricate an sdk.existing() binding.
    assert "sdk.existing(" not in content
    assert "search_results" not in content
    assert "0.9" not in content


def test_python_protocol_renders_empty_search_result_as_comment():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    messages = protocol.render_tool_result_messages(
        context,
        call_id="call-1",
        tool_name="search",
        params={"query": "nobody"},
        result=[],
        source="tool call",
    )

    assert messages[0]["content"] == "# Search query='nobody' found no memory files."


def test_python_protocol_search_comment_omits_read_tool_when_unavailable():
    uri = "viking://user/alice/memories/entities/person/tim.md"
    # eager_prefetch preloads everything and exposes no tools to the model.
    context = _context([_preference_schema()], available_tools=())
    protocol = create_extraction_output_protocol("python")

    messages = protocol.render_tool_result_messages(
        context,
        call_id="call-1",
        tool_name="search",
        params={"query": "tim"},
        result=[{"uri": uri, "score": 0.9}],
        source="prefetch",
    )

    content = messages[0]["content"]
    assert "read them with the read tool" not in content
    assert "there is no read tool" in content
    assert "treat anything not shown above as new" in content
    assert f"# - {uri}" in content


def test_python_protocol_renders_read_result_directly_as_existing_binding():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim\nKeep plugins small", 2)],
    )
    protocol = create_extraction_output_protocol("python")
    protocol.render_contract(context)

    messages = protocol.render_tool_result_messages(
        context,
        call_id="call-1",
        tool_name="read",
        params={"uri": uri, "offset": 1, "limit": 1},
        result={
            "memory_type": "preferences",
            "topic": "editor",
            "score": 2,
            "content": "2\tKeep plugins small",
            "page_id": 1,
            "memory_maintenance_notice": {
                "maintenance_required": True,
                "guidance": "choose split or compact",
            },
        },
        source="tool call",
    )

    assert len(messages) == 1
    content = messages[0]["content"]
    assert content.startswith("# Existing memory loaded by tool call")
    assert "preferences_1 = sdk.existing(" in content
    assert "content='Keep plugins small'" in content
    assert "2\\tKeep plugins small" not in content
    assert "Use Vim" not in content
    assert "tool_call_name" not in content
    assert "page_id" not in content
    assert uri not in content
    assert "# Memory maintenance notice:" in content
    assert '"maintenance_required": true' in content
    assert "estimated_tokens" not in content
    assert "content_characters" not in content
    assert "choose split or compact" in content


def test_python_protocol_converts_prefetch_tool_messages_without_duplicating_read_content():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim", 2)],
    )
    protocol = create_extraction_output_protocol("python")
    protocol.render_contract(context)
    prefetched = [
        {"role": "user", "content": "Conversation History"},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "tool_call_name": "search",
                    "args": {"query": "editor"},
                    "result": [{"uri": uri, "score": 0.9}],
                }
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "tool_call_name": "read",
                    "args": {"uri": uri},
                    "result": {
                        "memory_type": "preferences",
                        "topic": "editor",
                        "score": 2,
                        "content": "1\tUse Vim",
                    },
                }
            ),
        },
    ]

    messages = protocol.render_prefetch_messages(prefetched, context)
    combined = "\n".join(message["content"] for message in messages)

    assert messages[0]["content"] == "Conversation History"
    # Search only yields URIs, so it renders as a read-tool comment, not a binding.
    assert "read them with the read tool" in combined
    assert f"# - {uri}" in combined
    assert "search_results = [" not in combined
    # The file that was actually read becomes a system-provided existing binding.
    assert "preferences_1 = sdk.existing(" in combined
    assert combined.count("Use Vim") == 1
    assert "tool_call_name" not in combined
    assert '"result"' not in combined


def test_python_contract_uses_set_for_single_file_schema_and_create_for_collection():
    context = _context(
        [_profile_schema(), _preference_schema()],
        role_scope=RoleScope(user_ids=["alice"], peer_ids=["bob"]),
    )
    protocol = create_extraction_output_protocol("python")

    contract = protocol.render_contract(context)

    assert "sdk.set_profile" in contract
    assert "- sdk.create_profile(*," not in contract
    assert (
        "profile is a SINGLETON per identity: sdk.create_profile() does not exist, and "
        "sdk.set_profile() must appear AT MOST ONCE for each identity"
    ) in contract
    assert (
        "Current identity peer_id='bob': exactly one "
        "sdk.set_profile(peer_id='bob', ...) call; combine every person and section"
    ) in contract
    assert "Memory type rules:" in contract
    assert "User profile" in contract
    assert "sdk.create_preferences" in contract
    assert "sdk.set_preferences" not in contract
    assert "canonical = sdk.create_<type>(...)" in contract
    assert "duplicate_1.delete(replacement=canonical)" in contract
    assert "Identity fields (primary key): peer_id." in contract
    assert "Identity fields (primary key): peer_id, topic." in contract
    assert "Current self identity: exactly one sdk.set_profile() call without peer_id" in contract


def test_python_reserved_existing_retry_explains_new_replacement_binding():
    protocol = create_extraction_output_protocol("python")

    retry = protocol.render_format_retry(
        "Line 3: sdk.existing() is reserved for system-provided bindings"
    )

    assert "existing-object variable names already supplied by the system" in retry
    assert "canonical = sdk.create_<type>(...)" in retry
    assert "duplicate_1.delete(replacement=canonical)" in retry


def test_python_string_literal_retry_pushes_triple_quotes():
    protocol = create_extraction_output_protocol("python")

    retry = protocol.render_format_retry(
        "Line 33: invalid syntax. Perhaps you forgot a comma?"
    )

    assert "offending line is shown above" in retry
    assert 'triple-quoted string ("""...""")' in retry


def test_python_non_string_syntax_error_omits_quote_guidance():
    protocol = create_extraction_output_protocol("python")

    retry = protocol.render_format_retry("Line 3: unexpected indent")

    assert "offending line is shown above" not in retry


def test_resolution_repair_matches_protocol_output_shape():
    issues = [
        {
            "memory_type": "events",
            "page_id": 100,
            "reason_code": "no_writable_target",
            "reason": "range out of bounds",
            "operation": {"event_name": "yoga_class_started", "ranges": "99"},
        }
    ]

    json_repair = create_extraction_output_protocol("json").render_resolution_repair(issues)
    assert "ONLY one JSON object" in json_repair
    assert "yoga_class_started" in json_repair

    python_repair = create_extraction_output_protocol("python").render_resolution_repair(issues)
    assert "sdk.create_events" in python_repair
    assert "Output only Python code." in python_repair
    assert "JSON object" not in python_repair


def _kebab_schema() -> MemoryTypeSchema:
    return MemoryTypeSchema(
        memory_type="project-notes",
        description="Project notes",
        directory="viking://user/{{ user_space }}/memories/project-notes",
        filename_template="{{ project_name }}.md",
        fields=[
            MemoryField(
                name="project_name",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(
                name="note-body",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
        ],
    )


def test_python_contract_aliases_non_identifier_schema_names():
    context = _context([_kebab_schema()])
    protocol = create_extraction_output_protocol("python")

    contract = protocol.render_contract(context)

    # Method/parameter names are folded to valid identifiers on the DSL surface.
    assert "sdk.create_project_notes(" in contract
    assert "note_body" in contract
    assert "sdk.create_project-notes" not in contract


def test_python_compiles_aliased_names_back_to_real_schema():
    context = _context([_kebab_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        """
sdk.create_project_notes(
    project_name="atlas",
    note_body=\"\"\"Kickoff on 2023-06-09.\"\"\",
)
sdk.commit()
""",
        context,
    )

    assert error is None, error
    dumped = operations.model_dump()
    # Real schema name and real field name are preserved in the operations.
    assert dumped["project-notes"] == [
        {"page_id": 100, "project_name": "atlas", "note-body": "Kickoff on 2023-06-09."}
    ]


def test_python_edits_aliased_field_on_existing_object():
    existing = MemoryFile(
        uri="viking://user/alice/memories/project-notes/atlas.md",
        memory_type="project-notes",
        content="",
        extra_fields={
            "project_name": "atlas",
            "note-body": "Kickoff on 2023-06-09.",
            "version": 3,
            "_uri": "x",
        },
    )
    context = _context([_kebab_schema()], files=[existing])
    protocol = create_extraction_output_protocol("python")

    bindings = _bind(protocol, context)
    # Existing-object binding exposes the field under its identifier alias.
    assert "note_body=" in bindings

    var = bindings.split(" = ", 1)[0].strip().splitlines()[-1]
    operations, error = protocol.parse(
        f"""
{var}.note_body.edit(search=\"\"\"Kickoff on 2023-06-09.\"\"\", replace=\"\"\"Kickoff moved to 2023-07-01.\"\"\")
sdk.commit()
""",
        context,
    )

    assert error is None, error
    edited = operations.model_dump()["project-notes"]
    assert edited and edited[0]["page_id"] == 1


def test_python_syntax_error_includes_offending_source_line():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    # A double-quoted summary that itself embeds a double quote ends the string
    # early; the compiler should surface the real offending line, not just the
    # misleading "forgot a comma" hint.
    _operations, error = protocol.parse(
        'sdk.create_preferences(topic="x", content="he saw "Little Women" today", score=1)',
        context,
    )

    assert error is not None
    assert "invalid Python syntax" in error
    assert 'Little Women' in error
    assert "^" in error


def test_memory_schema_identity_fields_follow_scope_and_uri_template():
    entities = MemoryTypeSchema(
        memory_type="entities",
        directory="viking://user/{{ user_space }}/memories/entities",
        filename_template="{{ category|lower }}/{{ name|lower }}.md",
        fields=[
            MemoryField(name="category", field_type=FieldType.STRING),
            MemoryField(name="name", field_type=FieldType.STRING),
            MemoryField(name="content", field_type=FieldType.STRING),
        ],
    )
    preferences = MemoryTypeSchema(
        memory_type="preferences",
        directory="viking://user/{{ user_space }}/memories/preferences",
        filename_template="{{ user }}/{{ topic }}.md",
        fields=[
            MemoryField(name="user", field_type=FieldType.STRING),
            MemoryField(name="topic", field_type=FieldType.STRING),
            MemoryField(name="content", field_type=FieldType.STRING),
        ],
    )

    assert _profile_schema().identity_fields() == ("peer_id",)
    assert preferences.identity_fields() == ("peer_id", "user", "topic")
    assert entities.identity_fields() == ("peer_id", "category", "name")


def test_memory_schema_identity_fields_ignore_jinja_token_collisions():
    # `year` and `context` appear only inside a filter/function/attribute
    # expression, never as referenced variables, so they must not be treated
    # as identity fields. Only `ranges` and `event_name` are real references.
    events = MemoryTypeSchema(
        memory_type="events",
        directory="viking://user/{{ user_space }}/memories/events",
        filename_template="{{ extract_context.get_year(ranges) }}/{{ event_name }}.md",
        fields=[
            MemoryField(name="event_name", field_type=FieldType.STRING),
            MemoryField(name="ranges", field_type=FieldType.STRING),
            MemoryField(name="year", field_type=FieldType.STRING),
            MemoryField(name="context", field_type=FieldType.STRING),
            MemoryField(name="content", field_type=FieldType.STRING),
        ],
    )

    assert events.identity_fields() == ("peer_id", "event_name", "ranges")


def test_python_set_single_file_memory_compiles_to_same_operations_as_json():
    context = _context([_profile_schema()])
    python_protocol = create_extraction_output_protocol("python")
    json_protocol = create_extraction_output_protocol("json")

    python_operations, python_error = python_protocol.parse(
        "sdk.set_profile(content='Engineer')\nsdk.commit()",
        context,
    )
    json_operations, json_error = json_protocol.parse(
        '{"profile":[{"page_id":100,"content":"Engineer"}],"delete_ids":[]}',
        context,
    )

    assert python_error is None
    assert json_error is None
    assert python_operations.model_dump() == json_operations.model_dump()


def test_python_commit_is_optional():
    context = _context([_profile_schema()])
    protocol = create_extraction_output_protocol("python")

    # A program without a trailing sdk.commit() is accepted as-is.
    without_commit, error = protocol.parse("sdk.set_profile(content='Engineer')", context)
    assert error is None
    assert without_commit.model_dump()["profile"][0]["content"] == "Engineer"

    # An empty program means "no changes".
    empty_operations, empty_error = protocol.parse("", context)
    assert empty_error is None
    assert empty_operations.model_dump() == {"profile": [], "delete_ids": []}


def test_python_rejects_create_for_single_file_and_set_for_collection():
    context = _context([_profile_schema(), _preference_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        "sdk.create_profile(content='Engineer')\nsdk.commit()", context
    )
    assert operations is None
    assert "use sdk.set_profile()" in error

    operations, error = protocol.parse(
        "sdk.set_preferences(topic='editor', content='Use Vim', score=1)\nsdk.commit()",
        context,
    )
    assert operations is None
    assert "use sdk.create_preferences()" in error


def test_python_rejects_repeated_single_file_set_for_same_peer_scope():
    context = _context(
        [_profile_schema()],
        role_scope=RoleScope(user_ids=["alice"], peer_ids=["bob"]),
    )
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        """
sdk.set_profile(peer_id="bob", content="Engineer")
sdk.set_profile(peer_id="bob", content="Musician")
sdk.commit()
""",
        context,
    )

    assert operations is None
    assert "duplicate profile identity (peer_id='bob')" in error
    assert "may be called only once for peer 'bob'" in error
    assert "combine all people and content sections" in error


def test_python_rejects_repeated_single_file_set_for_self_scope():
    context = _context([_profile_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        """
sdk.set_profile(content="Engineer")
sdk.set_profile(content="Musician")
sdk.commit()
""",
        context,
    )

    assert operations is None
    assert "may be called only once for self" in error


def test_python_allows_one_single_file_set_per_peer_scope():
    context = _context(
        [_profile_schema()],
        role_scope=RoleScope(user_ids=["alice"], peer_ids=["bob", "carol"]),
    )
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        """
sdk.set_profile(peer_id="bob", content="Engineer")
sdk.set_profile(peer_id="carol", content="Musician")
sdk.commit()
""",
        context,
    )

    assert error is None
    assert operations.model_dump()["profile"] == [
        {"peer_id": "bob", "page_id": 100, "content": "Engineer"},
        {"peer_id": "carol", "page_id": 101, "content": "Musician"},
    ]


def test_python_create_compiles_to_same_operations_as_json():
    context = _context([_preference_schema()])
    python_protocol = create_extraction_output_protocol("python")
    json_protocol = create_extraction_output_protocol("json")

    python_operations, python_error = python_protocol.parse(
        """
topic = "editor".upper().lower()
score = len([1, 2]) + 1
sdk.create_preferences(
    topic=topic,
    content=f"Use {topic}",
    score=score,
    ignored=unknown_name_that_must_not_be_evaluated,
)
sdk.commit()
""",
        context,
    )
    json_operations, json_error = json_protocol.parse(
        """
{
  "preferences": [
    {"page_id": 100, "topic": "editor", "content": "Use editor", "score": 3}
  ],
  "delete_ids": []
}
""",
        context,
    )

    assert python_error is None
    assert json_error is None
    assert python_operations.model_dump() == json_operations.model_dump()


def test_python_create_avoids_existing_page_id_collisions():
    context = _context([_preference_schema()])
    context.page_id_map.register_new_page_id("viking://existing/100.md", 100)
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        "sdk.create_preferences(topic='editor', content='Use Vim', score=1)\nsdk.commit()",
        context,
    )

    assert error is None
    assert operations.model_dump()["preferences"][0]["page_id"] == 101


def test_python_protocol_includes_dynamic_peer_id_field():
    schema = _preference_schema()
    context = _context(
        [schema],
        role_scope=RoleScope(user_ids=["alice"], peer_ids=["bob", "carol"]),
    )
    protocol = create_extraction_output_protocol("python")

    contract = protocol.render_contract(context)
    operations, error = protocol.parse(
        "sdk.create_preferences(peer_id='bob', topic='editor', content='Use Vim', score=1)\nsdk.commit()",
        context,
    )

    assert "peer_id: str" in contract
    assert "Available peer_id values in this session: bob, carol" in contract
    assert error is None
    assert operations.model_dump()["preferences"][0]["peer_id"] == "bob"


def test_python_existing_binding_exposes_but_does_not_update_dynamic_peer_id():
    schema = _preference_schema()
    memory_file = _existing_preference(
        "viking://user/alice/peers/bob/memories/preferences/editor.md",
        "editor",
        "Use Vim",
    )
    memory_file.extra_fields["peer_id"] = "bob"
    context = _context(
        [schema],
        files=[memory_file],
        role_scope=RoleScope(user_ids=["alice"], peer_ids=["bob", "carol"]),
    )
    protocol = create_extraction_output_protocol("python")

    bindings = _bind(protocol, context)
    operations, error = protocol.parse(
        "preferences_1.update(peer_id='carol')\npreferences_1.content.update('Use Emacs')\nsdk.commit()",
        context,
    )

    assert "peer_id='bob'" in bindings
    assert error is None
    item = operations.model_dump()["preferences"][0]
    assert item["content"] == "Use Emacs"
    assert item["peer_id"] is None


def test_python_existing_updates_emit_patch_blocks_and_accumulate_sum_delta():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim\nDark theme\nTabs", 10)],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        """
preferences_1.content.edit(search="Use Vim", replace="Use Neovim")
preferences_1.update(score=2)
preferences_1.content.drop(text="Tabs")
preferences_1.update(
    topic="ignored immutable change",
    score=3,
    unknown_field="ignored",
    another_unknown=unknown_name_that_must_not_be_evaluated,
)
sdk.commit()
""",
        context,
    )

    assert error is None
    # edit/drop are emitted as StrPatch blocks (applied later by the shared path),
    # while SUM deltas still accumulate at compile time.
    item = operations.model_dump()["preferences"][0]
    assert item["page_id"] == 1
    assert item["topic"] == "editor"
    assert item["score"] == 5
    assert item["content"]["blocks"] == [
        {"search": "Use Vim", "replace": "Use Neovim"},
        {"delete": "Tabs"},
    ]


def test_python_patch_preserves_unescaped_markers_in_blocks():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "<<<<<<< literal", 0)],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        r"""
preferences_1.content.edit(search=r"\<<<<<<< literal", replace="plain")
sdk.commit()
""",
        context,
    )

    assert error is None
    blocks = operations.model_dump()["preferences"][0]["content"]["blocks"]
    assert blocks == [{"search": r"\<<<<<<< literal", "replace": "plain"}]


def test_python_field_edit_chain_emits_blocks_in_order():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim\nDark theme", 0)],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        """
preferences_1.content.edit(search="Use Vim", replace="Use Neovim").drop(text="Dark theme")
sdk.commit()
""",
        context,
    )

    assert error is None
    blocks = operations.model_dump()["preferences"][0]["content"]["blocks"]
    assert blocks == [
        {"search": "Use Vim", "replace": "Use Neovim"},
        {"delete": "Dark theme"},
    ]


def test_python_field_update_replaces_whole_field():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim\nDark theme", 0)],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        '''
preferences_1.content.update("""# Editor
- Use Neovim""")
sdk.commit()
''',
        context,
    )

    assert error is None
    assert operations.model_dump()["preferences"][0]["content"] == "# Editor\n- Use Neovim"


def test_python_field_edit_emits_block_even_when_search_absent():
    # Matching/rejection is now the shared validation path's job (json parity),
    # so parse() succeeds and just emits the block; a bad snippet no longer fails
    # the whole program at compile time.
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim", 0)],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        """
preferences_1.content.edit(search="Prefers Neovim", replace="Prefers Emacs")
sdk.commit()
""",
        context,
    )

    assert error is None
    blocks = operations.model_dump()["preferences"][0]["content"]["blocks"]
    assert blocks == [{"search": "Prefers Neovim", "replace": "Prefers Emacs"}]


def test_python_field_edit_rejects_literal_field_placeholder():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim", 0)],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        "preferences_1.field.edit(search='Use Vim', replace='Use Neovim')\nsdk.commit()",
        context,
    )

    assert operations is None
    assert "memory field 'field' is unavailable" in error
    assert "not the literal word 'field'" in error


def test_python_field_edit_emits_block_regardless_of_uniqueness():
    # Uniqueness enforcement now lives in the shared validation path, so parse()
    # just emits the block; matching (and any non-unique rejection) happens later.
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim\nUse Vim", 0)],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        "preferences_1.content.edit(search='Use Vim', replace='Use Neovim')\nsdk.commit()",
        context,
    )

    assert error is None
    blocks = operations.model_dump()["preferences"][0]["content"]["blocks"]
    assert blocks == [{"search": "Use Vim", "replace": "Use Neovim"}]


@pytest.mark.parametrize(
    "operation",
    [
        "preferences_1.content.edit(search='Use Vim', replace='Use Neovim')",
        "preferences_1.content.drop(text='Dark theme')",
    ],
)
def test_python_field_edit_and_drop_are_standalone_statements(operation: str):
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim\nDark theme")],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(f"x = {operation}\nsdk.commit()", context)

    assert operations is None
    assert "must be standalone statements" in error


def test_python_delete_replacement_and_links_compile_with_page_id_remapping():
    duplicate = _existing_preference(
        "viking://user/alice/memories/preferences/duplicate.md",
        "duplicate",
        "duplicate content",
    )
    project = MemoryFile(
        uri="viking://user/alice/memories/projects/openviking.md",
        memory_type="projects",
        content="Memory platform",
        extra_fields={"name": "openviking"},
    )
    context = _context(
        [_preference_schema(), _project_schema()],
        files=[duplicate, project],
        link_enabled=True,
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        """
canonical = sdk.create_preferences(topic="editor", content="Use Neovim", score=1)
preferences_1.link(projects_1, match_text="editor")
preferences_1.delete(replacement=canonical)
sdk.commit()
""",
        context,
    )

    assert error is None
    payload = operations.model_dump()
    assert payload["preferences"] == [
        {"page_id": 100, "topic": "editor", "content": "Use Neovim", "score": 1}
    ]
    assert payload["delete_ids"] == [{"delete_page_id": 1, "replacement_page_id": 100}]
    assert payload["links"] == [
        {
            "f": 100,
            "t": 2,
            "link_type": "related_to",
            "weight": 0.5,
            "match_text": "editor",
            "description": "",
        }
    ]


def test_python_update_after_delete_restores_object_and_delete_after_update_discards_update():
    first = _existing_preference(
        "viking://user/alice/memories/preferences/first.md", "first", "old one"
    )
    second = _existing_preference(
        "viking://user/alice/memories/preferences/second.md", "second", "old two"
    )
    context = _context([_preference_schema()], files=[first, second])
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        """
preferences_1.delete()
preferences_1.update(content="restored")
preferences_2.update(content="discarded")
preferences_2.delete()
preferences_1.link(preferences_2) if False else None
sdk.commit()
""",
        context,
    )

    assert error is not None
    assert operations is None

    operations, error = protocol.parse(
        """
preferences_1.delete()
preferences_1.update(content="restored")
preferences_2.update(content="discarded")
preferences_2.delete()
sdk.commit()
""",
        context,
    )
    assert error is None
    payload = operations.model_dump()
    assert payload["preferences"] == [
        {"page_id": 1, "topic": "first", "content": "restored", "score": None}
    ]
    assert payload["delete_ids"] == [{"delete_page_id": 2, "replacement_page_id": None}]


@pytest.mark.parametrize(
    ("program", "message"),
    [
        ("sdk.commit()\nsdk.commit()", "final statement"),
        ("for item in []:\n    pass\nsdk.commit()", "only assignments"),
        ("sdk.existing(memory_type='preferences')\nsdk.commit()", "reserved"),
        ("sdk.create_preferences(topic='x', content='y')\nsdk.commit()", "missing: score"),
        (
            "x = sdk.create_preferences(topic='x', content='y', score=1)\nx.delete()\nsdk.commit()",
            "cannot be deleted",
        ),
        (
            "obj = sdk.create_preferences(topic='x', content='y', score=1)\n"
            "obj.content.edit(search='y', replace='z')\nsdk.commit()",
            "can only patch an existing memory",
        ),
        (
            "sdk.replace(search='x', replacement='y')\nsdk.commit()",
            "unknown SDK method sdk.replace()",
        ),
        (
            "x = sdk.create_preferences(topic='x', content='y', score=1)\n"
            "x.content.update('a', 'b')\nsdk.commit()",
            "exactly one positional argument",
        ),
        (
            "x = sdk.create_preferences(topic='x', content='y', score=1)\nstr(x)\nsdk.commit()",
            "memory objects cannot be converted",
        ),
        (
            "x = sdk.create_preferences(topic='x', content='y', score=1)\nitems = [x]\nsdk.commit()",
            "memory objects cannot be stored",
        ),
        (
            "x = sdk.create_preferences(topic='x', content='y', score=1)\ntext = f'{x}'\nsdk.commit()",
            "memory objects cannot be formatted",
        ),
        (
            "x = sdk.create_preferences(topic='x', content='y', score=1)\nx.update(content=x)\nsdk.commit()",
            "memory objects cannot be used as business field values",
        ),
        (
            "sdk.create_preferences(topic='x', content='y', score=1).update(content='z')\nsdk.commit()",
            "assign a new memory object",
        ),
        ("import os\nsdk.commit()", "only assignments"),
        ("sdk.commit()\nprint('after')", "final statement"),
    ],
)
def test_python_rejects_invalid_or_unsafe_programs(program: str, message: str):
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(program, context)

    assert operations is None
    assert message in error


def test_python_rejects_delete_for_add_only_schema():
    memory_file = _existing_preference(
        "viking://user/alice/memories/preferences/editor.md", "editor", "Use Vim"
    )
    context = _context([_preference_schema(operation_mode="add_only")], files=[memory_file])
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse("preferences_1.delete()\nsdk.commit()", context)

    assert operations is None
    assert "delete() is unavailable" in error


def test_python_rejects_add_only_delete_when_another_schema_enables_deletes():
    memory_file = _existing_preference(
        "viking://user/alice/memories/preferences/editor.md", "editor", "Use Vim"
    )
    context = _context(
        [_preference_schema(operation_mode="add_only"), _project_schema()],
        files=[memory_file],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse("preferences_1.delete()\nsdk.commit()", context)

    assert operations is None
    assert "delete() is unavailable" in error


def test_python_aliases_non_identifier_memory_type_instead_of_raising():
    schema = _preference_schema()
    schema.memory_type = "user-preferences"
    context = _context([schema])
    protocol = create_extraction_output_protocol("python")

    contract = protocol.render_contract(context)
    assert "sdk.set_user_preferences(" in contract or "sdk.create_user_preferences(" in contract
    assert "user-preferences" not in contract.split("Memory type rules")[0]


def test_python_accepts_one_fence_with_surrounding_text():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse("```python\nsdk.commit()\n```", context)
    assert error is None
    assert operations.model_dump() == {"preferences": [], "delete_ids": []}

    operations, error = protocol.parse("Explanation\n```python\nsdk.commit()\n```", context)
    assert error is None
    assert operations.model_dump() == {"preferences": [], "delete_ids": []}


def test_python_accepts_trace_style_explanation_around_program():
    context = _context(
        [_preference_schema()],
        files=[
            _existing_preference(
                "viking://user/alice/memories/preferences/editor.md",
                "editor",
                "Use Vim",
            )
        ],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(
        """Update the existing preference.
```python
preferences_1.content.edit(search="Use Vim", replace="Use Neovim")
sdk.commit()
```
The program above applies the requested update.""",
        context,
    )

    assert error is None
    blocks = operations.model_dump()["preferences"][0]["content"]["blocks"]
    assert blocks == [{"search": "Use Vim", "replace": "Use Neovim"}]


@pytest.mark.parametrize(
    ("call", "error_fragment"),
    [
        ("sdk.read('viking://user/alice/memories/preferences/editor.md')", "sdk.read()"),
        ("read('viking://user/alice/memories/preferences/editor.md')", "function 'read'"),
        ("sdk.search('editor')", "sdk.search()"),
        ("search('editor')", "function 'search'"),
    ],
)
def test_python_read_search_errors_keep_native_tools_available_for_retry(
    call: str, error_fragment: str
):
    protocol = create_extraction_output_protocol("python")
    context = _context([_preference_schema()])

    operations, error = protocol.parse(
        f"item = {call}\nsdk.commit()",
        context,
    )

    assert operations is None
    assert error_fragment in error
    assert protocol.keep_tools_enabled_after_parse_error(error)
    retry = protocol.render_format_retry(error)
    assert "native search/read tool" in retry
    assert "Do not emit sdk.search(), sdk.read()" in retry


def test_python_sdk_read_error_takes_priority_over_reassigning_existing_binding():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim")],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse(f"preferences_1 = sdk.read({uri!r})\nsdk.commit()", context)

    assert operations is None
    assert "unknown SDK method sdk.read()" in error
    assert protocol.keep_tools_enabled_after_parse_error(error)


def test_python_object_read_error_keeps_native_tools_available_for_retry():
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "Use Vim")],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    operations, error = protocol.parse("preferences_1.read()\nsdk.commit()", context)

    assert operations is None
    assert "unknown memory method read()" in error
    assert protocol.keep_tools_enabled_after_parse_error(error)


@pytest.mark.parametrize(
    "program",
    [
        "```python\nsdk.commit()\n```\n```python\nsdk.commit()\n```",
        "Explanation\n```python\nsdk.commit()\n```\n```text\nextra\n```",
    ],
)
def test_python_rejects_multiple_fences(program: str):
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(program, context)
    assert operations is None
    assert "only one complete" in error


@pytest.mark.parametrize(
    "program",
    [
        "```python\nsdk.commit()```",
        "```python\nsdk.commit()",
        "Explanation\n```python\nsdk.commit()",
    ],
)
def test_python_accepts_single_recoverable_fence_variants(program: str):
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(program, context)

    assert error is None
    assert operations.model_dump() == {"preferences": [], "delete_ids": []}


def test_python_unclosed_fence_still_rejects_truncated_python():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse("```python\npreferences_1.update(", context)

    assert operations is None
    assert "invalid Python syntax" in error


def test_python_rejects_bare_markdown_with_actionable_error():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        "# Caroline\n- Transgender woman (as of 2023-06-09)", context
    )

    assert operations is None
    assert "bare Markdown, not a Python SDK program" in error
    assert "leading zeros" not in error
    retry = protocol.render_format_retry(error)
    assert "Markdown memory content inside quoted SDK arguments" in retry


def test_python_accepts_update_only_program_with_markdown_content():
    # A valid program that only uses obj.update() (no literal "sdk." token) and
    # embeds Markdown bullets in a triple-quoted string must not be mistaken for
    # bare Markdown just because a comment starts with "# " and bullets appear.
    uri = "viking://user/alice/memories/preferences/editor.md"
    context = _context(
        [_preference_schema()],
        files=[_existing_preference(uri, "editor", "# Editor\n- Use Vim")],
    )
    protocol = create_extraction_output_protocol("python")
    _bind(protocol, context)

    program = (
        "# Merge updates to the editor preference\n"
        'preferences_1.update(content="""# Editor\n'
        "A friend of Dave's who is involved in music.\n"
        "## Key Facts\n"
        "- Enjoys Japanese culture (as of 2023-10-19)\n"
        "- Relaxes by taking long walks\n"
        '""")'
    )

    operations, error = protocol.parse(program, context)

    assert error is None
    assert "# Editor" in operations.model_dump()["preferences"][0]["content"]


def test_python_rejects_huge_string_multiplication_before_allocation():
    context = _context([_profile_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        'sdk.set_profile(content="x" * 1_000_000_000)\nsdk.commit()',
        context,
    )

    assert operations is None
    assert "size limit" in error


def test_python_rejects_huge_string_multiplication_reversed_operands():
    context = _context([_profile_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        'sdk.set_profile(content=1_000_000_000 * "x")\nsdk.commit()',
        context,
    )

    assert operations is None
    assert "size limit" in error


def test_python_allows_small_string_repetition():
    context = _context([_profile_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        "sdk.set_profile(content='ab' * 3)\nsdk.commit()",
        context,
    )

    assert error is None
    assert operations.model_dump()["profile"][0]["content"] == "ababab"


def test_python_rejects_multiplication_between_two_non_literals():
    context = _context([_preference_schema()])
    protocol = create_extraction_output_protocol("python")

    # Multiplying a non-literal local value by a non-literal local value has
    # no bounded literal to repeat and must not be evaluated.
    operations, error = protocol.parse(
        "a = 'ab'\nn = 3\nsdk.create_preferences(topic='x', score=1, content=a * n)\nsdk.commit()",
        context,
    )

    assert operations is None
    assert "multiplication is only supported" in error


def test_python_rejects_numeric_multiplication():
    context = _context([_profile_schema()])
    protocol = create_extraction_output_protocol("python")

    # Number * number cannot repeat a literal and is useless for memory content.
    operations, error = protocol.parse(
        "sdk.set_profile(content=str(2 * 3))\nsdk.commit()",
        context,
    )

    assert operations is None
    assert "multiplication is only supported" in error


def test_python_rejects_huge_join_before_allocation():
    context = _context([_profile_schema()])
    protocol = create_extraction_output_protocol("python")

    operations, error = protocol.parse(
        'sdk.set_profile(content="".join(["xxxxxxxxxx"] * 200_000))\nsdk.commit()',
        context,
    )

    assert operations is None
    assert "size limit" in error
