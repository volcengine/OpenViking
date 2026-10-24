# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Normal-mode event padding, with explicit zero preserving the original selection."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from openviking.message import Message, TextPart
from openviking.server.identity import RequestContext, Role
from openviking.session.memory.memory_type_registry import (
    MemoryTypeRegistry,
    resolve_memory_templates_dir,
)
from openviking.session.memory.memory_updater import (
    ExtractContext,
    MemoryUpdater,
    MemoryUpdateResult,
)
from openviking.session.memory.session_extract_context_provider import SessionExtractContextProvider
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking_cli.session.user_id import UserIdentifier
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config_data,padding,expected_ranges",
    [
        ({}, None, "1-3"),
        ({"memory": {}}, None, "1-3"),
        ({"memory": {"minimal_mode": False}}, 0, "2"),
        ({"memory": {"minimal_mode": False}}, 1, "1-3"),
        ({"memory": {"minimal_mode": False}}, 2, "0-4"),
    ],
    ids=["memory-omitted", "mode-omitted", "padding-zero", "padding-one", "padding-two"],
)
async def test_normal_mode_pads_events_and_preserves_standard_extraction(
    monkeypatch, config_data, padding, expected_ranges
):
    import openviking.session.compressor_v3 as module
    from openviking.session.memory import account_templates

    config = OpenVikingConfig.from_dict(config_data)
    assert getattr(config.memory, "minimal_mode", False) is False
    for name in (
        "openviking.session.compressor_v3",
        "openviking.session.memory.extract_loop",
        "openviking.session.memory.session_extract_context_provider",
        "openviking_cli.utils.config",
    ):
        monkeypatch.setattr(f"{name}.get_openviking_config", lambda: config)
    monkeypatch.setattr("openviking.session.memory.tools.uuid4", lambda: UUID(int=1))
    registry = MemoryTypeRegistry(load_schemas=False)
    for name in ("events", "profile"):
        registry.load_from_yaml(str(resolve_memory_templates_dir() / f"{name}.yaml"))
    if padding is not None:
        schema = registry.get("events")
        schema.content_template = schema.content_template.replace(
            "get_event_content(ranges, summary, 0, range_padding=1)",
            f"get_event_content(ranges, summary, 0, range_padding={padding})",
        )
    registry.initialize_memory_files = AsyncMock()
    original_schemas = [schema.model_dump() for schema in registry.list_all()]
    monkeypatch.setattr(module, "get_default_registry", lambda: registry)
    monkeypatch.setattr(
        account_templates, "resolve_account_memory_registry", AsyncMock(return_value=registry)
    )
    prefetch = AsyncMock(return_value="[]")
    monkeypatch.setattr(SessionExtractContextProvider, "_eval_memory", prefetch)
    fs = SimpleNamespace(
        _uri_to_path=lambda uri, *, ctx: uri,
        exists=AsyncMock(return_value=False),
        read_file=AsyncMock(side_effect=FileNotFoundError),
    )
    monkeypatch.setattr(module, "get_viking_fs", lambda: fs)
    response = (
        'sdk.create_events(event_name="move_to_paris", goal="Relocation", '
        'summary="Alice moved to Paris.", ranges="2")\nsdk.commit()'
    )
    vlm = SimpleNamespace(model="test-model", get_completion_async=AsyncMock(return_value=response))
    compressor = module.SessionCompressorV3(
        vikingdb=None, vlm_resolver=SimpleNamespace(get_vlm=AsyncMock(return_value=vlm))
    )
    compressor.train_from_extracted_cases = AsyncMock(return_value={"submitted": 0})
    compressor.extract_session_skills = AsyncMock()
    providers = []
    create_loop = compressor._get_or_create_react

    def capture_provider(**kwargs):
        providers.append(kwargs["context_provider"])
        return create_loop(**kwargs)

    compressor._get_or_create_react = capture_provider
    ctx = RequestContext(user=UserIdentifier("default", "alice"), role=Role.ROOT)
    messages = [
        Message(
            id=f"m{i}", role="user", parts=[TextPart(text=text)], created_at="2024-02-03T12:00:00Z"
        )
        for i, text in enumerate(
            ["First context.", "Hello.", "I moved to Paris.", "Thanks.", "Last context."]
        )
    ]
    submitted = []
    rendered = []

    async def submit(request):
        submitted.append(request)
        updater = MemoryUpdater(registry=request.memory_registry)
        updater._viking_fs = fs
        for operation in request.operations.upsert_operations:
            rendered.extend(
                await updater._prepare_upsert(
                    operation, ctx, extract_context=ExtractContext(request.messages)
                )
            )
        return SimpleNamespace(apply_result=MemoryUpdateResult(), operations=request.operations)

    monkeypatch.setattr(
        module,
        "get_streaming_memory_updater",
        AsyncMock(return_value=SimpleNamespace(submit=submit)),
    )
    await compressor.extract_long_term_memories(messages, ctx=ctx, strict_extract_errors=True)

    assert len(providers) == len(submitted) == len(rendered) == 1
    provider = providers[0]
    request = submitted[0]
    operation = request.operations.upsert_operations[0]
    assert type(provider) is SessionExtractContextProvider
    assert request.memory_registry is registry
    assert request.isolation_options["allowed_memory_types"] is None
    assert operation.memory_fields["ranges"] == "2"
    assert operation.memory_fields["event_name"] == "move_to_paris"
    assert operation.memory_fields["summary"] == "Alice moved to Paris."
    assert [schema.model_dump() for schema in registry.list_all()] == original_schemas
    assert {schema.memory_type for schema in provider.get_memory_schemas(ctx)} == {
        "events",
        "profile",
    }
    assert {field.name for field in registry.get("events").fields} >= {
        "ranges",
        "summary",
        "goal",
        "event_name",
    }
    registry.initialize_memory_files.assert_awaited_once_with(ctx, allowed_memory_types=None)
    prefetch.assert_awaited()  # Normal prefetch must not silently become minimal-mode prefetch.
    compressor.train_from_extracted_cases.assert_awaited_once()
    compressor.extract_session_skills.assert_not_awaited()
    vlm.get_completion_async.assert_awaited_once()
    uri, raw = rendered[0]
    restored = MemoryFileUtils.read(raw, uri=uri)
    assert restored.extra_fields["ranges"] == "2"
    body = restored.content
    assert body.startswith("# Summary\nAlice moved to Paris.\n")
    assert "I moved to Paris." in body
    assert ("Hello." in body) is (expected_ranges != "2")
    assert ("Thanks." in body) is (expected_ranges != "2")
    assert ("First context." in body) is (expected_ranges == "0-4")
    assert ("Last context." in body) is (expected_ranges == "0-4")
