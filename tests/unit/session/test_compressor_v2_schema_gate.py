# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session import compressor_v2 as compressor_v2_module
from openviking.session.memory.dataclass import MemoryField, MemoryTypeSchema
from openviking.session.memory.merge_op import FieldType, MergeOp, SearchReplaceBlock, StrPatch
from openviking.session.memory.schema_model_generator import SchemaModelGenerator


def test_schema_exact_file_apply_gate_rejects_string_patch_fields():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply([schema])

    assert allowed is False
    assert unsupported == ["experiences.content:patch:string"]


def test_schema_exact_file_apply_gate_allows_string_patch_when_schema_is_structured():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply(
        [schema],
        string_patch_exact_safe=True,
    )

    assert allowed is True
    assert unsupported == []


def test_structured_string_patch_schema_rejects_plain_string_outputs():
    schema = MemoryTypeSchema(
        memory_type="notes",
        description="notes",
        fields=[
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
        ],
    )
    generator = SchemaModelGenerator([schema], structured_string_patches_only=True)
    model = generator.create_flat_data_model(schema)

    valid = model.model_validate(
        {
            "page_id": 1,
            "content": StrPatch(blocks=[SearchReplaceBlock(search="old", replace="new")]),
        }
    )
    assert valid.content.blocks[0].replace == "new"

    from_json = model.model_validate(
        {
            "page_id": 1,
            "content": {"blocks": [{"search": "old", "replace": "new"}]},
        }
    )
    assert from_json.content.blocks[0].search == "old"

    with pytest.raises(ValueError):
        model.model_validate({"page_id": 1, "content": "plain replacement"})


def test_schema_exact_file_apply_gate_allows_string_immutable_fields():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(
                name="tool_name",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply([schema])

    assert allowed is True
    assert unsupported == []


def test_schema_exact_file_apply_gate_rejects_non_string_patch_fields():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(name="count", field_type=FieldType.INT64, merge_op=MergeOp.PATCH),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply([schema])

    assert allowed is False
    assert unsupported == ["experiences.count:patch:int64"]


def test_schema_exact_file_apply_gate_allows_replace_fields():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(
                name="experience_name",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(name="content", field_type=FieldType.STRING, merge_op=MergeOp.REPLACE),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply([schema])

    assert allowed is True
    assert unsupported == []


def test_schema_exact_file_apply_gate_rejects_non_string_replace_fields():
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(name="score", field_type=FieldType.INT64, merge_op=MergeOp.REPLACE),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply([schema])

    assert allowed is False
    assert unsupported == ["experiences.score:replace:int64"]


def test_schema_exact_file_apply_gate_rejects_unsupported_memory_types():
    schema = MemoryTypeSchema(
        memory_type="tools",
        fields=[
            MemoryField(
                name="tool_name",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(name="call_count", field_type=FieldType.INT64, merge_op=MergeOp.SUM),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply([schema])

    assert allowed is False
    assert unsupported == ["tools:unsupported_memory_type"]
