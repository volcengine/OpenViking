# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
)
from openviking.session.memory.merge_op.base import FieldType


def _schema() -> MemoryTypeSchema:
    return MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(name="experience_name", field_type=FieldType.STRING),
            MemoryField(name="situation", field_type=FieldType.STRING),
            MemoryField(name="evidence", field_type=FieldType.STRING),
        ],
        content_template="## Situation\n{{ situation }}\n\n## Evidence\n{{ evidence }}",
    )


def test_render_operation_after_file_uses_schema_template_and_preserves_unchanged_fields() -> None:
    from openviking.session.memory.memory_updater import render_operation_after_file

    uri = "viking://user/u/memories/experiences/example.md"
    old_file = MemoryFile(
        uri=uri,
        content="old rendered body",
        memory_type="experiences",
        extra_fields={
            "memory_type": "experiences",
            "experience_name": "example",
            "situation": "old situation",
            "evidence": "old evidence",
            "version": 2,
        },
    )
    operation = ResolvedOperation(
        old_memory_file_content=old_file,
        memory_fields={"situation": "new situation"},
        memory_type="experiences",
        uris=[uri],
    )

    after_file = render_operation_after_file(operation, schema=_schema())

    assert after_file.content == "## Situation\nnew situation\n\n## Evidence\nold evidence"
    assert after_file.extra_fields["situation"] == "new situation"
    assert after_file.extra_fields["evidence"] == "old evidence"
    assert after_file.extra_fields["version"] == 3


def test_patch_merge_instruction_lists_content_fields_from_schema() -> None:
    from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
    from openviking.session.memory.patch_merge_context_provider import PatchMergeContextProvider

    registry = MemoryTypeRegistry(load_schemas=False)
    registry.register(_schema())
    provider = PatchMergeContextProvider(memory_type="experiences", patches=[])
    provider._registry = registry

    instruction = provider.instruction()

    assert "`situation`, `evidence`" in instruction
    assert "reminder" not in instruction
