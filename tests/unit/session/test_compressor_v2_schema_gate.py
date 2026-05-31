# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session import compressor_v2 as compressor_v2_module
from openviking.session.memory.dataclass import MemoryField, MemoryTypeSchema
from openviking.session.memory.merge_op import FieldType, MergeOp


def test_schema_exact_file_apply_gate_allows_patch_sum_immutable():
    schema = MemoryTypeSchema(
        memory_type="tools",
        fields=[
            MemoryField(
                name="tool_name",
                field_type=FieldType.STRING,
                merge_op=MergeOp.IMMUTABLE,
            ),
            MemoryField(name="call_count", field_type=FieldType.INT64, merge_op=MergeOp.SUM),
            MemoryField(
                name="guidelines",
                field_type=FieldType.STRING,
                merge_op=MergeOp.PATCH,
            ),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply([schema])

    assert allowed is True
    assert unsupported == []


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
        memory_type="scores",
        fields=[
            MemoryField(name="score", field_type=FieldType.INT64, merge_op=MergeOp.REPLACE),
        ],
    )

    allowed, unsupported = compressor_v2_module._schemas_support_exact_file_apply([schema])

    assert allowed is False
    assert unsupported == ["scores.score:replace:int64"]
