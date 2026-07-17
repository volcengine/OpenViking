# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from openviking.session.memory.dataclass import MemoryField, MemoryTypeSchema
from openviking.session.memory.merge_op.base import FieldType
from openviking.session.memory.utils.template_utils import TemplateUtils


def test_variables_returns_all_undeclared_template_variables() -> None:
    template = "{{ situation }}\n{{ evidence }}\n{{ extract_context.scope }}"

    assert TemplateUtils.variables(template) == {"situation", "evidence", "extract_context"}


def test_schema_content_field_names_use_template_membership_and_schema_order() -> None:
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(name="experience_name", field_type=FieldType.STRING),
            MemoryField(name="evidence", field_type=FieldType.STRING),
            MemoryField(name="situation", field_type=FieldType.STRING),
        ],
        content_template="## Situation\n{{ situation }}\n\n## Evidence\n{{ evidence }}",
    )

    assert schema.content_field_names() == ("evidence", "situation")
