# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import asyncio
from types import SimpleNamespace

from openviking.session import compressor_v2 as compressor_v2_module
from openviking.session.memory.dataclass import (
    MemoryField,
    MemoryFile,
    MemoryTypeSchema,
    ResolvedOperation,
    ResolvedOperations,
    StoredLink,
)
from openviking.session.memory.merge_op import FieldType, MergeOp
from openviking.session.memory.utils.json_parser import JsonUtils
from openviking.telemetry import OperationTelemetry, bind_telemetry


def _experience_schema() -> MemoryTypeSchema:
    content_description = """
      Structured experience extraction in EXACTLY this 3-section format.

      ## Situation
      <markdown bullets: Entry Conditions>

      ## Approach
      <markdown bullets: Active Execution Logic>

      ## Reflect
      <markdown bullets: Guardrails>
    """
    return MemoryTypeSchema(
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

    schema = _experience_schema()
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


def test_create_new_experience_consolidation_keeps_ops_when_schema_repair_fails():
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
                                "content": "## Situation\n- Return delivered item",
                            }
                        ]
                    }
                )
            return JsonUtils.dumps({"content": "## Situation\n- Still missing sections"})

    schema = _experience_schema()
    registry = SimpleNamespace(
        get=lambda memory_type: schema if memory_type == "experiences" else None
    )
    uris = [
        "viking://agent/default/memories/experiences/delivered_order_return.md",
        "viking://agent/default/memories/experiences/order_item_return.md",
    ]
    original_contents = ["Experience card 0", "Experience card 1"]
    operations = ResolvedOperations(
        upsert_operations=[
            ResolvedOperation(
                old_memory_file_content=None,
                memory_type="experiences",
                uris=[uri],
                memory_fields={
                    "experience_name": f"card_{index}",
                    "content": original_contents[index],
                },
            )
            for index, uri in enumerate(uris)
        ],
        delete_file_contents=[],
        errors=[],
        resolved_links=[],
    )

    telemetry = OperationTelemetry(operation="test", enabled=True)
    with bind_telemetry(telemetry):
        remap = asyncio.run(
            compressor_v2_module._synthesize_create_new_experience_consolidation(
                vlm=FakeVLM(),
                operations=operations,
                phase_metric_key="experience_single",
                registry=registry,
            )
        )

    assert remap == {}
    assert [op.uris[0] for op in operations.upsert_operations] == uris
    assert [op.memory_fields["content"] for op in operations.upsert_operations] == original_contents
    phase = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
    assert phase["operation_exact_apply_window_schema_repair_attempts"] == 1
    assert phase["operation_exact_apply_window_schema_repair_failed"] == 1
    assert phase["operation_exact_apply_window_create_new_consolidation_schema_rejected"] == 1


def test_create_new_experience_consolidation_rejects_groups_without_content():
    class FakeVLM:
        async def get_completion_async(self, messages):
            return JsonUtils.dumps({"groups": [{"canonical_index": 0, "member_indices": [0, 1]}]})

    schema = _experience_schema()
    registry = SimpleNamespace(
        get=lambda memory_type: schema if memory_type == "experiences" else None
    )
    uris = [
        "viking://agent/default/memories/experiences/delivered_order_return.md",
        "viking://agent/default/memories/experiences/order_item_return.md",
    ]
    links = [
        StoredLink(
            from_uri=uris[0],
            to_uri="viking://agent/default/memories/trajectories/return_a.md",
            link_type="derived_from",
        ),
        StoredLink(
            from_uri=uris[1],
            to_uri="viking://agent/default/memories/trajectories/return_b.md",
            link_type="derived_from",
        ),
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
        resolved_links=list(links),
    )

    telemetry = OperationTelemetry(operation="test", enabled=True)
    with bind_telemetry(telemetry):
        remap = asyncio.run(
            compressor_v2_module._synthesize_create_new_experience_consolidation(
                vlm=FakeVLM(),
                operations=operations,
                phase_metric_key="experience_single",
                registry=registry,
            )
        )

    assert remap == {}
    assert [op.uris[0] for op in operations.upsert_operations] == uris
    assert operations.resolved_links == links
    phase = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
    assert phase["operation_exact_apply_window_create_new_consolidation_schema_rejected"] == 1


def test_create_new_experience_consolidation_rejects_overlapping_groups():
    class FakeVLM:
        async def get_completion_async(self, messages):
            return JsonUtils.dumps(
                {
                    "groups": [
                        {
                            "canonical_index": 0,
                            "member_indices": [0, 1],
                            "content": (
                                "## Situation\n- A plus B\n\n"
                                "## Approach\n- Keep B fact\n\n"
                                "## Reflect\n- Guard B"
                            ),
                        },
                        {
                            "canonical_index": 0,
                            "member_indices": [0, 2],
                            "content": (
                                "## Situation\n- A plus C\n\n"
                                "## Approach\n- Keep C fact\n\n"
                                "## Reflect\n- Guard C"
                            ),
                        },
                    ]
                }
            )

    schema = _experience_schema()
    registry = SimpleNamespace(
        get=lambda memory_type: schema if memory_type == "experiences" else None
    )
    uris = [
        "viking://agent/default/memories/experiences/card_a.md",
        "viking://agent/default/memories/experiences/card_b.md",
        "viking://agent/default/memories/experiences/card_c.md",
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
        resolved_links=[
            StoredLink(
                from_uri=uri,
                to_uri=f"viking://agent/default/memories/trajectories/source_{index}.md",
                link_type="derived_from",
            )
            for index, uri in enumerate(uris)
        ],
    )

    telemetry = OperationTelemetry(operation="test", enabled=True)
    with bind_telemetry(telemetry):
        remap = asyncio.run(
            compressor_v2_module._synthesize_create_new_experience_consolidation(
                vlm=FakeVLM(),
                operations=operations,
                phase_metric_key="experience_single",
                registry=registry,
            )
        )

    assert remap == {uris[1]: uris[0]}
    assert [op.uris[0] for op in operations.upsert_operations] == [uris[0], uris[2]]
    assert operations.upsert_operations[0].memory_fields["content"].count("B") > 0
    assert "C fact" not in operations.upsert_operations[0].memory_fields["content"]
    assert {link.from_uri for link in operations.resolved_links} == {uris[0], uris[2]}
    phase = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
    assert phase["operation_exact_apply_window_create_new_consolidation_overlap_rejected"] == 1


def test_timeline_conflict_synthesis_returns_none_when_schema_repair_fails():
    class FakeVLM:
        def __init__(self):
            self.messages = []

        async def get_completion_async(self, messages):
            self.messages.append(messages)
            if len(self.messages) == 1:
                return JsonUtils.dumps({"fields": {"content": "## Situation\n- Bad shape"}})
            return JsonUtils.dumps({"content": "## Situation\n- Still bad"})

    schema = _experience_schema()
    current = MemoryFile(
        uri="viking://agent/default/memories/experiences/a.md",
        content=("## Situation\n- Existing\n\n## Approach\n- Existing\n\n## Reflect\n- Existing"),
        memory_type="experiences",
    )
    base = MemoryFile(uri=current.uri, content=current.content, memory_type="experiences")
    operations = [
        ResolvedOperation(
            old_memory_file_content=base,
            memory_type="experiences",
            uris=[current.uri],
            memory_fields={"content": "## Situation\n- Proposed"},
        )
    ]

    telemetry = OperationTelemetry(operation="test", enabled=True)
    with bind_telemetry(telemetry):
        synthesized = asyncio.run(
            compressor_v2_module._synthesize_timeline_conflict_fields(
                vlm=FakeVLM(),
                uri=current.uri,
                memory_type="experiences",
                schema=schema,
                current_file=current,
                resolved_ops=operations,
                conflicts=[
                    {
                        "uri": current.uri,
                        "memory_type": "experiences",
                        "field": "content",
                        "error": "Patch application failed",
                    }
                ],
                phase_metric_key="experience_single",
            )
        )

    assert synthesized is None
    phase = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
    assert phase["operation_exact_apply_window_timeline_conflict_synthesis_failed"] == 1
    assert phase["operation_exact_apply_window_schema_repair_failed"] == 1


def test_synthesized_field_schema_rejects_duplicate_or_out_of_order_heading_repair():
    class FakeVLM:
        async def get_completion_async(self, messages):
            return JsonUtils.dumps(
                {
                    "content": (
                        "## Reflect\n- Still first\n\n"
                        "## Situation\n- Present\n\n"
                        "## Approach\n- Present\n\n"
                        "## Reflect\n- Duplicate"
                    )
                }
            )

    telemetry = OperationTelemetry(operation="test", enabled=True)
    with bind_telemetry(telemetry):
        repaired = asyncio.run(
            compressor_v2_module._ensure_synthesized_field_schema(
                vlm=FakeVLM(),
                memory_type="experiences",
                field_name="content",
                value=(
                    "## Reflect\n- First\n\n"
                    "## Situation\n- Present\n\n"
                    "## Approach\n- Present\n\n"
                    "## Reflect\n- Duplicate"
                ),
                schema=_experience_schema(),
                phase_metric_key="experience_single",
            )
        )

    assert repaired is None
    phase = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
    assert phase["operation_exact_apply_window_schema_repair_attempts"] == 1
    assert phase["operation_exact_apply_window_schema_repair_failed"] == 1
    assert phase["operation_exact_apply_window_schema_repair_heading_errors"] == 4


def test_synthesized_field_schema_noops_without_heading_requirements():
    class FakeVLM:
        async def get_completion_async(self, messages):
            raise AssertionError("schema-less fields should not call the model")

    schema = MemoryTypeSchema(
        memory_type="notes",
        fields=[
            MemoryField(
                name="content",
                field_type=FieldType.STRING,
                description="A plain free-form note.",
                merge_op=MergeOp.REPLACE,
            )
        ],
    )
    content = "Plain note without markdown headings"

    repaired = asyncio.run(
        compressor_v2_module._ensure_synthesized_field_schema(
            vlm=FakeVLM(),
            memory_type="notes",
            field_name="content",
            value=content,
            schema=schema,
            phase_metric_key="experience_single",
        )
    )

    assert repaired == content


def test_operation_telemetry_exposes_create_new_key_count():
    telemetry = OperationTelemetry(operation="test", enabled=True)
    with bind_telemetry(telemetry):
        telemetry.count(
            "memory.agent.extract.phase.experience_single."
            "operation_exact_apply_window_create_new_key_count",
            3,
        )

    phase = telemetry.finish().summary["memory"]["agent"]["phase"]["experience_single"]
    assert phase["operation_exact_apply_window_create_new_key_count"] == 3
