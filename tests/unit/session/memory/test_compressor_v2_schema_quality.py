# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace

from openviking.session import compressor_v2 as compressor_v2_module
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
)
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.telemetry import OperationTelemetry, bind_telemetry


def test_create_new_experience_consolidation_repairs_schema_heading_drift():
    class FakeVLM:
        def __init__(self):
            self.messages = []

        async def get_completion_async(self, messages):
            self.messages.append(messages)
            if len(self.messages) == 1:
                return JsonUtils.dumps(
                    {
                        "groups": [
                            {
                                "canonical_index": 0,
                                "member_indices": [0, 1],
                                "content": (
                                    "## Situation\n"
                                    "- User wants to return a delivered order item\n\n"
                                    "- Verify the order and request confirmation\n\n"
                                    "- Do not process non-delivered orders"
                                ),
                            }
                        ]
                    }
                )
            return JsonUtils.dumps(
                {
                    "content": (
                        "## Situation\n"
                        "- User wants to return a delivered order item\n\n"
                        "## Approach\n"
                        "- Verify the order and request confirmation\n\n"
                        "## Reflect\n"
                        "- Do not process non-delivered orders"
                    )
                }
            )

    content_description = """
      Structured experience extraction in EXACTLY this 3-section format.

      ## Situation
      <markdown bullets: Entry Conditions>

      ## Approach
      <markdown bullets: Active Execution Logic>

      ## Reflect
      <markdown bullets: Guardrails>
    """
    schema = MemoryTypeSchema(
        memory_type="experiences",
        fields=[
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                description=content_description,
                merge_op=MergeOp.REPLACE,
            )
        ],
    )
    registry = SimpleNamespace(
        get=lambda memory_type: schema if memory_type == "experiences" else None
    )

    uris = [
        "viking://agent/default/memories/experiences/delivered_order_return.md",
        "viking://agent/default/memories/experiences/order_item_return.md",
    ]
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=None,
                memory_type="experiences",
                uris=[uri],
                memory_fields={
                    "experience_name": f"card_{index}",
                    "content": f"Experience card {index}",
                },
            )
            for index, uri in enumerate(uris)
        ],
        delete_file_contents=[],
        errors=[],
        resolved_links=[],
    )
    fake_vlm = FakeVLM()

    telemetry = OperationTelemetry(operation="test", enabled=True)
    with bind_telemetry(telemetry):
        remap = asyncio.run(
            compressor_v2_module._synthesize_create_new_experience_consolidation(
                vlm=fake_vlm,
                operations=operations,
                phase_metric_key="experience_single",
                registry=registry,
            )
        )

    assert remap == {uris[1]: uris[0]}
    assert len(fake_vlm.messages) == 2
    repaired_content = operations.upsert_operations[0].memory_fields["content"]
    assert "## Situation" in repaired_content
    assert "## Approach" in repaired_content
    assert "## Reflect" in repaired_content
    phase = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
    assert phase["operation_exact_apply_window_schema_repair_attempts"] == 1
    assert phase["operation_exact_apply_window_schema_repair_success"] == 1
