# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""EXP prompt contracts and storage compatibility without model calls."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.extraction_output_protocol import (
    ExtractionOutputContext,
    create_extraction_output_protocol,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.page_id_map import PageIdMap
from openviking.session.memory.schema_model_generator import SchemaModelGenerator
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils


@pytest.fixture
def experience_schema(monkeypatch):
    config = SimpleNamespace(memory=SimpleNamespace(link_enabled=False))
    monkeypatch.setattr("openviking_cli.utils.config.get_openviking_config", lambda: config)
    registry = MemoryTypeRegistry(load_schemas=False)
    template = (
        Path(__file__).resolve().parents[3] / "openviking/prompts/templates/memory/experiences.yaml"
    )
    registry.load_from_yaml(str(template))
    return registry.get("experiences")


@pytest.mark.parametrize("language", ["en", "zh-CN"])
@pytest.mark.parametrize("output_format", ["json", "python"])
def test_experience_contract_renders_fixed_headings_and_body_language(
    experience_schema, language, output_format
):
    generator = SchemaModelGenerator([experience_schema], template_context={"language": language})
    operations_model = generator.create_structured_operations_model()
    context = ExtractionOutputContext(
        operations_model=operations_model,
        schemas=(experience_schema,),
        page_id_map=PageIdMap(),
        read_file_contents={},
        link_enabled=False,
        template_context={"language": language},
    )

    contract = create_extraction_output_protocol(output_format).render_contract(context)

    assert f"title and body must be written in {language}" in contract
    assert "{{ language }}" not in contract
    assert "one-sentence title" in contract
    assert "Keep the English headings below exactly as written" in contract
    headings = [
        "## Situation",
        "## Procedure",
        "## Anti-pattern",
    ]
    positions = [contract.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "title, Situation, and Procedure are required" in contract
    assert "Anti-pattern is optional" in contract
    assert "omit empty or redundant sections" in contract
    assert "## Approach" not in contract
    assert "## Reflect" not in contract
    assert "No numbered lists" not in contract


def test_experience_contract_keeps_method_boundaries(experience_schema):
    content = next(
        field.description for field in experience_schema.fields if field.name == "content"
    )

    for rule in (
        '"When <condition>, <action>"',
        "Omit routine setup, unrelated requests, and instance-specific choices",
        "retain supported checks and state the limitation",
        "wrong action, omission, or misuse and its consequence",
        "There is no fixed quota of experiences or steps",
        "parameter semantics, calculation relationships, and ordering constraints",
        "Past user choices are not current defaults or authorization",
    ):
        assert rule in content


def test_experience_storage_schema_includes_replaceable_reasoning(experience_schema):
    assert [
        (field.name, field.field_type.value, field.merge_op.value)
        for field in experience_schema.fields
    ] == [
        ("experience_name", "string", "immutable"),
        ("reasoning", "string", "replace"),
        ("content", "string", "replace"),
        ("supersedes", "string", "replace"),
    ]
    assert experience_schema.directory == "viking://user/{{ user_space }}/memories/experiences"
    assert experience_schema.filename_template == "{{ experience_name|lower }}.md"
    assert experience_schema.operation_mode == "upsert"
    assert experience_schema.stage == "agent"
    reasoning = next(
        field.description for field in experience_schema.fields if field.name == "reasoning"
    )
    assert "Write before content" in reasoning
    assert "three short items, each 1–2 sentences" in reasoning
    assert "match evaluation feedback to actual steps" in reasoning
    assert "do not audit every step, repeat the body" in reasoning

    proposal_schema = experience_schema.model_copy(update={"operation_mode": "add_only"})
    for output_format in ("json", "python"):
        context = ExtractionOutputContext(
            operations_model=SchemaModelGenerator(
                [proposal_schema]
            ).create_structured_operations_model(),
            schemas=(proposal_schema,),
            page_id_map=PageIdMap(),
            read_file_contents={},
            link_enabled=False,
        )
        contract = create_extraction_output_protocol(output_format).render_contract(context)
        assert "Full replacement for" not in contract
        assert "read the current content first" not in contract


@pytest.mark.parametrize("output_format", ["json", "python"])
def test_experience_protocol_accepts_zero_experiences(experience_schema, output_format):
    context = ExtractionOutputContext(
        operations_model=SchemaModelGenerator(
            [experience_schema]
        ).create_structured_operations_model(),
        schemas=(experience_schema,),
        page_id_map=PageIdMap(),
        read_file_contents={},
        link_enabled=False,
    )
    response = '{"experiences": []}' if output_format == "json" else "sdk.commit()"

    operations, error = create_extraction_output_protocol(output_format).parse(response, context)

    assert error is None
    assert operations.experiences == []
    assert operations.is_empty()


@pytest.mark.parametrize(
    "body",
    [
        "# 保留未修改字段\n## Situation\n接口要求完整列表。\n## Procedure\n先读取列表，再仅修改目标字段。",
        "## Situation\nLegacy condition.\n## Approach\n- Read first.\n## Reflect\n- Do not guess.",
    ],
)
def test_new_and_existing_experience_bodies_keep_the_same_storage_shell(body):
    original = MemoryFile(
        uri="viking://user/u/memories/experiences/retain_unchanged_fields.md",
        memory_type="experiences",
        content=body,
        extra_fields={
            "experience_name": "retain_unchanged_fields",
            "status": "staging",
            "version": 2,
        },
    )

    raw = MemoryFileUtils.write(original)
    restored = MemoryFileUtils.read(raw, uri=original.uri)

    assert restored.content == body
    assert restored.to_metadata() == original.to_metadata()


@pytest.mark.parametrize(
    "reasoning", [None, "先修改地址的轨迹完成了两项修改；反序轨迹被订单状态阻止。"]
)
def test_experience_reasoning_is_optional_and_stored_outside_body(experience_schema, reasoning):
    model = SchemaModelGenerator([experience_schema]).create_structured_operations_model()
    fields = {
        "page_id": 100,
        "experience_name": "edit_order",
        "content": "## Procedure\n先修改地址，再修改商品。",
    }
    if reasoning is not None:
        fields["reasoning"] = reasoning
    parsed = model.model_validate({"experiences": [fields]})
    memory = MemoryFile.from_parsed(parsed=parsed.experiences[0].model_dump(exclude_none=True))
    restored = MemoryFileUtils.read(MemoryFileUtils.write(memory))

    assert restored.extra_fields.get("reasoning") == reasoning
    assert restored.plain_content() == fields["content"]
